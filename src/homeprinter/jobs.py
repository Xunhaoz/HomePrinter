"""In-memory job queue that feeds the printer one sheet at a time.

The printer has a single queue and no multi-document jobs, so each PDF page
becomes its own Print-Job, sent only after the previous one has finished.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pyipp.enums import IppJobState

from . import convert
from .config import Settings
from .printer import TERMINAL_STATES, PrinterClient, PrinterError, PrintOptions

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "printing", "completed", "failed", "canceled"]
POLL_SECONDS = 2
RETRY_SECONDS = 5
RETRY_LIMIT_SECONDS = 600
KEEP_FINISHED = 100


@dataclass
class Job:
    filename: str
    kind: Literal["image", "pdf"]
    data: bytes = field(repr=False)  # JPEG for images, the original file for PDFs
    options: PrintOptions
    pages: list[int]  # 0-based PDF pages; [0] for an image
    dry_run: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: JobStatus = "queued"
    sheets_done: int = 0
    printer_job_ids: list[int] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_requested: bool = False
    current_printer_job: int | None = None

    @property
    def sheets_total(self) -> int:
        # Images use the IPP copies attribute; PDFs repeat the page run per copy (collated).
        return 1 if self.kind == "image" else len(self.pages) * self.options.copies

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "kind": self.kind,
            "status": self.status,
            "dry_run": self.dry_run,
            "pages": [p + 1 for p in self.pages] if self.kind == "pdf" else None,
            "copies": self.options.copies,
            "sheets_done": self.sheets_done,
            "sheets_total": self.sheets_total,
            "printer_job_ids": self.printer_job_ids,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class JobCanceled(Exception):
    pass


class JobManager:
    def __init__(self, printer: PrinterClient, settings: Settings):
        self.printer = printer
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    def start(self) -> None:
        self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()

    def submit(self, job: Job) -> Job:
        self.jobs[job.id] = job
        self._prune()
        self.queue.put_nowait(job.id)
        return job

    async def cancel(self, job: Job) -> None:
        if job.status not in ("queued", "printing"):
            return
        job.cancel_requested = True
        if job.status == "queued":
            self._finish(job, "canceled")
        elif job.current_printer_job is not None:
            try:
                await self.printer.cancel(job.current_printer_job)
            except PrinterError as exc:
                log.warning("cancel of printer job %s failed: %s", job.current_printer_job, exc)

    def _prune(self) -> None:
        finished = [j for j in self.jobs.values() if j.finished_at is not None]
        for job in sorted(finished, key=lambda j: j.finished_at)[:-KEEP_FINISHED]:
            del self.jobs[job.id]

    def _finish(self, job: Job, status: JobStatus, error: str | None = None) -> None:
        job.status = status
        job.error = error
        job.finished_at = time.time()
        job.current_printer_job = None
        job.data = b""

    async def _run(self) -> None:
        while True:
            job = self.jobs.get(await self.queue.get())
            if job is None or job.status != "queued":
                continue
            job.status = "printing"
            try:
                await self._print(job)
                self._finish(job, "completed")
            except JobCanceled:
                self._finish(job, "canceled")
            except Exception as exc:
                log.exception("job %s failed", job.id)
                self._finish(job, "failed", str(exc))

    async def _render(self, job: Job, page: int) -> bytes:
        if job.kind == "image":
            return job.data  # already converted to JPEG on upload
        return await asyncio.to_thread(
            convert.pdf_page_to_jpeg, job.data, page, self.settings.pdf_dpi, self.settings.jpeg_quality
        )

    async def _print(self, job: Job) -> None:
        runs = 1 if job.kind == "image" else job.options.copies
        sheet_options = job.options
        if job.kind == "pdf":
            sheet_options = PrintOptions(**{**vars(job.options), "copies": 1})

        for _ in range(runs):
            for page in job.pages:
                if job.cancel_requested:
                    raise JobCanceled
                jpeg = await self._render(job, page)
                name = job.filename if job.kind == "image" else f"{job.filename} p{page + 1}"
                printer_job = await self._submit_with_retry(job, jpeg, name, sheet_options)
                if printer_job is not None:
                    job.printer_job_ids.append(printer_job)
                    job.current_printer_job = printer_job
                    await self._wait_until_done(job, printer_job)
                    job.current_printer_job = None
                job.sheets_done += 1

    async def _submit_with_retry(self, job: Job, jpeg: bytes, name: str, options: PrintOptions) -> int | None:
        deadline = time.monotonic() + RETRY_LIMIT_SECONDS
        while True:
            try:
                return await self.printer.submit_jpeg(jpeg, name, options, validate=job.dry_run)
            except PrinterError as exc:
                if not exc.retryable or time.monotonic() > deadline:
                    raise
                log.info("printer busy or unreachable (%s), retrying job %s", exc, job.id)
            await asyncio.sleep(RETRY_SECONDS)
            if job.cancel_requested:
                raise JobCanceled

    async def _wait_until_done(self, job: Job, printer_job: int) -> None:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            try:
                state, reasons = await self.printer.job_state(printer_job)
            except PrinterError as exc:
                if not exc.retryable:
                    raise
                continue
            if state not in TERMINAL_STATES:
                continue
            if state == IppJobState.COMPLETED:
                return
            if job.cancel_requested:
                raise JobCanceled
            raise PrinterError(f"printer job {printer_job} {state.name.lower()}: {', '.join(reasons)}")
