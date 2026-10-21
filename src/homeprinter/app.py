"""HTTP API: printer status, print uploads, and job tracking."""

import asyncio
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from . import convert
from .config import settings
from .jobs import Job, JobManager
from .printer import PrinterClient, PrinterError, PrintOptions

printer = PrinterClient(settings)
jobs = JobManager(printer, settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    jobs.start()
    yield
    await jobs.stop()


app = FastAPI(title="HomePrinter", lifespan=lifespan)


async def _printer_call(coro):
    try:
        return await coro
    except PrinterError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/printer")
async def printer_status():
    return await _printer_call(printer.status())


@app.get("/printer/capabilities")
async def printer_capabilities():
    return await _printer_call(printer.capabilities())


@app.post("/print", status_code=202)
async def print_file(
    file: Annotated[UploadFile, File(description="PDF or image (JPEG, PNG, HEIC, ...)")],
    copies: Annotated[int, Form(ge=1, le=99)] = 1,
    media: Annotated[str | None, Form(description="e.g. iso_a4_210x297mm, na_index-4x6_4x6in")] = None,
    color_mode: Annotated[Literal["auto", "color", "monochrome"] | None, Form()] = None,
    quality: Annotated[Literal["normal", "high"] | None, Form()] = None,
    scaling: Annotated[Literal["auto", "auto-fit", "fit", "fill", "none"], Form()] = "fit",
    sides: Annotated[
        Literal["one-sided", "two-sided-long-edge", "two-sided-short-edge"],
        Form(description="PDF only; long-edge for portrait booklets, short-edge for flip-up pages"),
    ] = "one-sided",
    pages: Annotated[str | None, Form(description="PDF only, e.g. 1-3,5")] = None,
    dry_run: Annotated[bool, Form(description="Validate with the printer without printing")] = False,
):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"file is larger than {settings.max_upload_mb} MB")

    options = PrintOptions(
        copies=copies, media=media, color_mode=color_mode, quality=quality, scaling=scaling,
        sides=None if sides == "one-sided" else sides,
    )
    filename = file.filename or "upload"
    try:
        if convert.is_pdf(data):
            page_list = convert.parse_page_ranges(pages, convert.pdf_page_count(data))
            if media:
                convert.media_size_mm(media)  # reject unknown sizes before queueing
            job = Job(filename=filename, kind="pdf", data=data, options=options, pages=page_list, dry_run=dry_run)
        else:
            if pages:
                raise convert.UnsupportedFile("'pages' only applies to PDFs")
            if options.sides:
                raise convert.UnsupportedFile("two-sided printing needs a PDF; the printer prints photos one-sided")
            jpeg = await asyncio.to_thread(convert.image_to_jpeg, data, settings.jpeg_quality)
            job = Job(filename=filename, kind="image", data=jpeg, options=options, pages=[0], dry_run=dry_run)
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
