"""In-memory job queue that feeds the printer one job at a time.

Photos go out as a single JPEG job. A PDF is rendered to one multi-page PWG
raster document in a temp file, then streamed as a single job, which is
what lets the printer do duplex.
"""

import asyncio
import logging
import tempfile
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, BinaryIO, Literal

from pyipp.enums import IppJobState

from . import convert
from .config import Settings
from .printer import QUALITY, TERMINAL_STATES, PrinterClient, PrinterError, PrintOptions

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "rendering", "printing", "completed", "failed", "canceled"]
POLL_SECONDS = 2
RETRY_SECONDS = 5
RETRY_LIMIT_SECONDS = 600
KEEP_FINISHED = 100
UPLOAD_SECONDS_PER_PAGE = 60  # the printer reads data roughly as fast as it prints


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
    pages_total: int = 0
    pages_done: int = 0
    printer_job_id: int | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_requested: bool = False

    def __post_init__(self) -> None:
        # Images use the IPP copies attribute; PDFs repeat the pages (blank-padded for duplex).
        self.pages_total = self.options.copies if self.kind == "image" else len(self.pages) * self.options.copies

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "kind": self.kind,
            "status": self.status,
            "dry_run": self.dry_run,
            "pages": [p + 1 for p in self.pages] if self.kind == "pdf" else None,
            "copies": self.options.copies,
            "sides": self.options.sides or "one-sided",
            "pages_done": self.pages_done,
            "pages_total": self.pages_total,
            "printer_job_id": self.printer_job_id,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class JobCanceled(Exception):
    pass


async def build_job(data: bytes, filename: str, form: dict[str, Any], jpeg_quality: int) -> Job:
    """Validate an upload plus /print form fields and turn it into a Job.

    Raises convert.UnsupportedFile for anything the printer can't take, so
    callers can reject it before queueing (or before saving a schedule).
    """
    sides = form.get("sides") or "one-sided"
    options = PrintOptions(
        copies=form.get("copies", 1), media=form.get("media"), color_mode=form.get("color_mode"),
        quality=form.get("quality"), scaling=form.get("scaling") or "fit",
        sides=None if sides == "one-sided" else sides,
    )
    pages, dry_run = form.get("pages"), bool(form.get("dry_run"))
    if not data:
        raise convert.UnsupportedFile("empty file")
    if convert.is_pdf(data):
        page_list = convert.parse_page_ranges(pages, convert.pdf_page_count(data))
        if options.media:
            convert.media_size_mm(options.media)  # reject unknown sizes before queueing
        return Job(filename=filename, kind="pdf", data=data, options=options, pages=page_list, dry_run=dry_run)
    if pages:
        raise convert.UnsupportedFile("'pages' only applies to PDFs")
    if options.sides:
        raise convert.UnsupportedFile("two-sided printing needs a PDF; the printer prints photos one-sided")
    jpeg = await asyncio.to_thread(convert.image_to_jpeg, data, jpeg_quality)
    return Job(filename=filename, kind="image", data=jpeg, options=options, pages=[0], dry_run=dry_run)


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
        if job.status in ("completed", "failed", "canceled"):
            return
        job.cancel_requested = True
        if job.status == "queued":
            self._finish(job, "canceled")
        elif job.status == "printing" and job.printer_job_id is not None:
            try:
                await self.printer.cancel(job.printer_job_id)
            except PrinterError as exc:
                log.warning("cancel of printer job %s failed: %s", job.printer_job_id, exc)

    def _prune(self) -> None:
        finished = [j for j in self.jobs.values() if j.finished_at is not None]
        for job in sorted(finished, key=lambda j: j.finished_at)[:-KEEP_FINISHED]:
            del self.jobs[job.id]

    def _finish(self, job: Job, status: JobStatus, error: str | None = None) -> None:
        job.status = status
        job.error = error
        job.finished_at = time.time()
        job.data = b""
        if status == "completed":
            job.pages_done = job.pages_total

    async def _run(self) -> None:
        while True:
            job = self.jobs.get(await self.queue.get())
            if job is None or job.status != "queued":
                continue
            try:
                if job.kind == "image":
                    await self._print_image(job)
                else:
                    await self._print_pdf(job)
                self._finish(job, "completed")
            except JobCanceled:
                self._finish(job, "canceled")
            except Exception as exc:
                log.exception("job %s failed", job.id)
                self._finish(job, "failed", str(exc))

    async def _print_image(self, job: Job) -> None:
        job.status = "printing"
        await self._send(job, job.data, "image/jpeg", job.options, timeout=120)

    async def _print_pdf(self, job: Job) -> None:
        job.status = "rendering"
        caps = await self.printer.raster_capabilities()
        options = job.options
        media = options.media or caps.media_default
        dpi = self.settings.pdf_dpi if self.settings.pdf_dpi in caps.resolutions else caps.resolutions[0]
        layout = convert.RasterLayout(
            media=media,
            dpi=dpi,
            color=caps.color and options.color_mode != "monochrome",
            margin_mm=caps.margin_mm,
            scaling=options.scaling or "fit",
            sides=options.sides or "one-sided",
            sheet_back=caps.sheet_back,
            copies=options.copies,
            print_quality=QUALITY[options.quality] if options.quality else 0,
        )

        def on_page(_: int) -> None:
            if job.cancel_requested:
                raise JobCanceled

        with tempfile.TemporaryFile() as document:
            job.pages_total = await asyncio.to_thread(
                convert.write_pdf_as_pwg, job.data, job.pages, layout, document, on_page
            )
            # Copies are already in the document, and the layout replaces print-scaling.
            pwg_options = replace(options, copies=1, media=media, scaling=None)
            job.status = "printing"
            await self._send(
                job, document, "image/pwg-raster", pwg_options,
                timeout=120 + UPLOAD_SECONDS_PER_PAGE * job.pages_total,
            )

    async def _send(self, job: Job, document: bytes | BinaryIO, fmt: str, options: PrintOptions, timeout: int) -> None:
        printer_job = await self._submit_with_retry(job, document, fmt, options, timeout)
        if printer_job is not None:
            job.printer_job_id = printer_job
            await self._wait_until_done(job, printer_job)

    async def _submit_with_retry(
        self, job: Job, document: bytes | BinaryIO, fmt: str, options: PrintOptions, timeout: int
    ) -> int | None:
        deadline = time.monotonic() + RETRY_LIMIT_SECONDS
        while True:
            if job.cancel_requested:
                raise JobCanceled
            try:
                return await self.printer.submit(
                    document, fmt, job.filename, options, validate=job.dry_run, timeout=timeout
                )
            except PrinterError as exc:
                if not exc.retryable or time.monotonic() > deadline:
                    raise
                log.info("printer busy or unreachable (%s), retrying job %s", exc, job.id)
            await asyncio.sleep(RETRY_SECONDS)

    async def _wait_until_done(self, job: Job, printer_job: int) -> None:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            try:
                progress = await self.printer.job_progress(printer_job)
            except PrinterError as exc:
                if not exc.retryable:
                    raise
                continue
            job.pages_done = min(progress.impressions, job.pages_total)
            if progress.state not in TERMINAL_STATES:
                continue
            if progress.state == IppJobState.COMPLETED:
                return
            if job.cancel_requested:
                raise JobCanceled
            raise PrinterError(
                f"printer job {printer_job} {progress.state.name.lower()}: {', '.join(progress.reasons)}"
            )
