"""Scan jobs: POST /scan starts one in the background and returns its id;
the file is stored under data_dir and kept for scan_retention_days."""

import asyncio
import logging
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .config import Settings
from .db import ScanJob, Storage, now_utc
from .scanner import ColorMode, Format, ScannerClient, ScannerError, Size, Source

log = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 3600


def scan_to_dict(job: ScanJob, tz: str) -> dict[str, Any]:
    zone = ZoneInfo(tz)
    local = lambda t: t.astimezone(zone).isoformat() if t else None  # noqa: E731
    return {
        "id": job.id,
        "status": job.status,
        **job.settings,
        "filename": job.filename,
        "media_type": job.media_type,
        "pages": job.pages,
        "size_bytes": job.size_bytes,
        "error": job.error,
        "created_at": local(job.created_at),
        "finished_at": local(job.finished_at),
        "expires_at": local(job.expires_at),
        "download": f"/scans/{job.id}/file" if job.status == "completed" else None,
    }


class ScanService:
    def __init__(self, scanner: ScannerClient, storage: Storage, settings: Settings):
        self.scanner = scanner
        self.storage = storage
        self.retention = timedelta(days=settings.scan_retention_days)
        self.timezone = settings.default_timezone
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        # A restart kills in-flight scans; don't leave them "scanning" forever.
        async with self.storage.session() as session:
            for job in (await session.scalars(select(ScanJob).where(ScanJob.status == "scanning"))).all():
                job.status, job.error, job.finished_at = "failed", "interrupted by a service restart", now_utc()
            await session.commit()
        self._spawn(self._cleanup_loop())

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def create(self, source: Source, color_mode: ColorMode, resolution: int, fmt: Format, size: Size) -> ScanJob:
        """Validate and start a scan. Raises ScannerError (400/409) before anything is stored."""
        self.scanner.reserve()
        try:
            settings_xml = await self.scanner.prepare(source, color_mode, resolution, fmt, size)
            job = ScanJob(
                settings={"source": source, "color_mode": color_mode, "resolution": resolution,
                          "format": fmt, "size": size},
                expires_at=now_utc() + self.retention,
            )
            async with self.storage.session() as session:
                session.add(job)
                await session.commit()
        except BaseException:
            self.scanner.release()
            raise
        self._spawn(self._run(job.id, settings_xml, source, fmt))
        return job

    async def _run(self, job_id: str, settings_xml: str, source: Source, fmt: Format) -> None:
        try:
            result = await self.scanner.run(settings_xml, source, fmt)
            relative = f"files/scans/{job_id}.{result.extension}"
            await asyncio.to_thread(self.storage.path(relative).write_bytes, result.content)
            async with self.storage.session() as session:
                job = await session.get(ScanJob, job_id)
                job.status, job.finished_at = "completed", now_utc()
                job.file_path, job.media_type, job.pages = relative, result.media_type, result.pages
                job.size_bytes = len(result.content)
                local_time = job.created_at.astimezone(ZoneInfo(self.timezone))
                job.filename = local_time.strftime(f"scan-%Y%m%d-%H%M%S.{result.extension}")
                await session.commit()
        except Exception as exc:
            if not isinstance(exc, ScannerError):
                log.exception("scan %s failed", job_id)
            async with self.storage.session() as session:
                job = await session.get(ScanJob, job_id)
                job.status, job.error, job.finished_at = "failed", str(exc), now_utc()
                await session.commit()
        finally:
            self.scanner.release()

    async def list(self) -> list[ScanJob]:
        async with self.storage.session() as session:
            return list((await session.scalars(select(ScanJob).order_by(ScanJob.created_at.desc()))).all())

    async def get(self, job_id: str) -> ScanJob | None:
        async with self.storage.session() as session:
            return await session.get(ScanJob, job_id)

    async def delete(self, job_id: str) -> bool:
        """Delete a finished scan and its file. False if it doesn't exist; ScannerError if still running."""
        async with self.storage.session() as session:
            job = await session.get(ScanJob, job_id)
            if job is None:
                return False
            if job.status == "scanning":
                raise ScannerError("scan is still running", 409)
            self.storage.delete_file(job.file_path)
            await session.delete(job)
            await session.commit()
        return True

    async def delete_expired(self) -> int:
        async with self.storage.session() as session:
            expired = (await session.scalars(
                select(ScanJob).where(ScanJob.expires_at < now_utc(), ScanJob.status != "scanning")
            )).all()
            for job in expired:
                self.storage.delete_file(job.file_path)
                await session.delete(job)
            await session.commit()
        return len(expired)

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                if count := await self.delete_expired():
                    log.info("deleted %d expired scans", count)
            except Exception:
                log.exception("scan cleanup failed")
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
