"""HTTP API: printing, print schedules, scanning, and job tracking."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import convert
from .config import settings
from .db import Storage
from .jobs import Job, JobManager, build_job
from .printer import PrinterClient, PrinterError
from .scanner import ScannerClient, ScannerError
from .scans import ScanService, scan_to_dict
from .schedules import ScheduleError, ScheduleService, schedule_to_dict

log = logging.getLogger(__name__)
storage = Storage(settings.data_dir)
scanner = ScannerClient(settings)
printer = PrinterClient(settings)
jobs = JobManager(printer, settings)
scans = ScanService(scanner, storage, settings)
schedules = ScheduleService(storage, jobs, settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await storage.open()
    jobs.start()
    await scans.start()
    schedules.start()
    yield
    await schedules.stop()
    await scans.stop()
    await jobs.stop()
    await storage.close()


app = FastAPI(title="HomePrinter", lifespan=lifespan)


@app.exception_handler(StarletteHTTPException)
async def log_http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code >= 400 and exc.status_code != 404:  # 404s are mostly browsers asking for favicon.ico
        log.warning("%s %s -> %d: %s", request.method, request.url.path, exc.status_code, exc.detail)
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def log_validation_error(request: Request, exc: RequestValidationError):
    log.warning("%s %s -> 422: %s", request.method, request.url.path, exc.errors())
    return await request_validation_exception_handler(request, exc)


async def _printer_call(coro):
    try:
        return await coro
    except PrinterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"file is larger than {settings.max_upload_mb} MB")
    return data


def print_form(
    copies: Annotated[int, Form(ge=1, le=99)] = 1,
    # Explicit examples: otherwise Swagger UI pre-fills optional strings with "string".
    media: Annotated[
        str | None,
        Form(description="e.g. iso_a4_210x297mm, na_index-4x6_4x6in", examples=["iso_a4_210x297mm"]),
    ] = None,
    color_mode: Annotated[Literal["auto", "color", "monochrome"] | None, Form()] = None,
    quality: Annotated[Literal["normal", "high"] | None, Form()] = None,
    scaling: Annotated[Literal["auto", "auto-fit", "fit", "fill", "none"], Form()] = "fit",
    sides: Annotated[
        Literal["one-sided", "two-sided-long-edge", "two-sided-short-edge"],
        Form(description="PDF only; long-edge for portrait booklets, short-edge for flip-up pages"),
    ] = "one-sided",
    pages: Annotated[str | None, Form(description="PDF only, e.g. 1-3,5; empty for all", examples=[""])] = None,
    dry_run: Annotated[bool, Form(description="Validate with the printer without printing")] = False,
) -> dict[str, Any]:
    return {"copies": copies, "media": media or None, "color_mode": color_mode, "quality": quality,
            "scaling": scaling, "sides": sides, "pages": pages or None, "dry_run": dry_run}


PrintForm = Annotated[dict[str, Any], Depends(print_form)]


# --- printer -----------------------------------------------------------------

@app.get("/printer")
async def printer_status():
    return await _printer_call(printer.status())


@app.get("/printer/capabilities")
async def printer_capabilities():
    return await _printer_call(printer.capabilities())


@app.post("/print", status_code=202)
async def print_file(
    file: Annotated[UploadFile, File(description="PDF or image (JPEG, PNG, HEIC, ...)")],
    form: PrintForm,
):
    data = await _read_upload(file)
    try:
        job = await build_job(data, file.filename or "upload", form, settings.jpeg_quality)
    except convert.UnsupportedFile as exc:
        raise HTTPException(400, str(exc)) from exc
    return jobs.submit(job).to_dict()


@app.get("/jobs")
async def list_jobs():
    return [job.to_dict() for job in sorted(jobs.jobs.values(), key=lambda j: j.created_at, reverse=True)]


def _get_job(job_id: str) -> Job:
    job = jobs.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    return _get_job(job_id).to_dict()


@app.delete("/jobs/{job_id}")
async def cancel_job(job_id: str):
    job = _get_job(job_id)
    await jobs.cancel(job)
    return job.to_dict()


# --- print schedules -----------------------------------------------------------

@app.post("/print-schedule", status_code=201)
async def create_schedule(
    file: Annotated[UploadFile, File(description="PDF or image to print on schedule")],
    form: PrintForm,
    kind: Annotated[Literal["once", "cron"], Form(description="once = at run_at; cron = repeat on the cron expression")],
    name: Annotated[str, Form(examples=[""])] = "",
    run_at: Annotated[str, Form(description="kind=once: e.g. 2026-10-22T09:00 (in `timezone` unless it has an offset)",
                                examples=[""])] = "",
    cron: Annotated[str, Form(description="kind=cron: minute hour day month weekday, e.g. '0 9 * * 1' = Mondays 09:00",
                              examples=[""])] = "",
    timezone: Annotated[str, Form(description=f"IANA name; default {settings.default_timezone}",
                                  examples=[settings.default_timezone])] = "",
):
    """Store a document and print it once or on a repeating cron schedule."""
    data = await _read_upload(file)
    try:
        schedule = await schedules.create(
            name=name, kind=kind, run_at=run_at, cron=cron, timezone=timezone or None,
            data=data, filename=file.filename or "upload", form=form,
        )
    except (ScheduleError, convert.UnsupportedFile) as exc:
        raise HTTPException(400, str(exc)) from exc
    return schedule_to_dict(schedule)


@app.get("/print-schedule")
async def list_schedules():
    return [schedule_to_dict(s) for s in await schedules.list()]


@app.get("/print-schedule/{schedule_id}")
async def get_schedule(schedule_id: str):
    schedule = await schedules.get(schedule_id)
    if schedule is None:
        raise HTTPException(404, "schedule not found")
    return schedule_to_dict(schedule)


@app.patch("/print-schedule/{schedule_id}")
async def update_schedule(
    schedule_id: str,
    enabled: Annotated[bool | None, Body(embed=True)] = None,
    name: Annotated[str | None, Body(embed=True)] = None,
):
    """Pause or resume (`{"enabled": false}`), or rename a schedule."""
    try:
        schedule = await schedules.update(schedule_id, enabled=enabled, name=name)
    except ScheduleError as exc:
        raise HTTPException(409, str(exc)) from exc
    if schedule is None:
        raise HTTPException(404, "schedule not found")
    return schedule_to_dict(schedule)


@app.post("/print-schedule/{schedule_id}/run")
async def run_schedule_now(schedule_id: str):
    """Print the schedule's document right away, without changing its timetable."""
    schedule = await schedules.run_now(schedule_id)
    if schedule is None:
        raise HTTPException(404, "schedule not found")
    if schedule.last_status == "failed":
        raise HTTPException(400, schedule.last_error)
    return schedule_to_dict(schedule)


@app.delete("/print-schedule/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str):
    if not await schedules.delete(schedule_id):
        raise HTTPException(404, "schedule not found")


# --- scanner -----------------------------------------------------------------

@app.get("/scanner")
async def scanner_status():
    try:
        status, caps = await asyncio.gather(scanner.status(), scanner.capabilities())
    except ScannerError as exc:
        raise HTTPException(exc.http_status, str(exc)) from exc
    return {**status, "busy": scanner.busy, "sources": {name: vars(c) for name, c in caps.items()}}


@app.post("/scan", status_code=202)
async def scan(
    source: Annotated[Literal["flatbed", "feeder"], Form(description="feeder = the document feeder (ADF)")] = "flatbed",
    color_mode: Annotated[Literal["color", "grayscale", "bw"], Form()] = "color",
    resolution: Annotated[int, Form(description="dpi: 100, 200, 300, 600; 1200 on the flatbed only")] = 300,
    format: Annotated[Literal["pdf", "jpeg"], Form(description="multi-page feeder scans as JPEG come back zipped")] = "pdf",
    size: Annotated[Literal["a4", "letter", "legal", "max"], Form(description="max = the whole scan area")] = "a4",
):
    """Start a scan and return its id; poll GET /scans/{id}, then download from /scans/{id}/file."""
    try:
        job = await scans.create(source, color_mode, resolution, format, size)
    except ScannerError as exc:
        raise HTTPException(exc.http_status, str(exc)) from exc
    return scan_to_dict(job, settings.default_timezone)


@app.get("/scans")
async def list_scans():
    return [scan_to_dict(job, settings.default_timezone) for job in await scans.list()]


async def _get_scan(scan_id: str):
    job = await scans.get(scan_id)
    if job is None:
        raise HTTPException(404, "scan not found")
    return job


@app.get("/scans/{scan_id}")
async def get_scan(scan_id: str):
    return scan_to_dict(await _get_scan(scan_id), settings.default_timezone)


@app.get(
    "/scans/{scan_id}/file",
    response_class=FileResponse,
    responses={200: {"content": {"application/pdf": {}, "image/jpeg": {}, "application/zip": {}}}},
)
async def download_scan(scan_id: str):
    job = await _get_scan(scan_id)
    if job.status != "completed":
        raise HTTPException(409, f"scan is {job.status}" + (f": {job.error}" if job.error else ""))
    path = storage.path(job.file_path)
    if not path.exists():
        raise HTTPException(410, "scan file is gone")
    return FileResponse(path, media_type=job.media_type, filename=job.filename,
                        headers={"X-Scan-Pages": str(job.pages)})


@app.delete("/scans/{scan_id}", status_code=204)
async def delete_scan(scan_id: str):
    try:
        found = await scans.delete(scan_id)
    except ScannerError as exc:
        raise HTTPException(exc.http_status, str(exc)) from exc
    if not found:
        raise HTTPException(404, "scan not found")
