"""Print schedules: a stored document plus /print options, printed once at
run_at or repeatedly on a cron expression evaluated in the schedule's time zone.

A due schedule just submits an ordinary print job, so conversion, retries
and tracking are the same as for /print. Runs missed while the service was
down are printed late if within GRACE, otherwise skipped (status "missed").
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import PurePath
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import select

from . import convert, testpage
from .config import Settings
from .db import PrintSchedule, Storage, new_id, now_utc
from .jobs import JobManager, build_job

log = logging.getLogger(__name__)

Kind = Literal["once", "cron"]
GRACE = timedelta(hours=1)
TICK_SECONDS = 30


class ScheduleError(ValueError):
    pass


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError(f"unknown time zone {name!r}") from exc


def next_cron_run(cron: str, tz: str, after: datetime) -> datetime:
    """Next time strictly after `after` (aware), as aware UTC."""
    local = after.astimezone(zone(tz))
    return croniter(cron, local).get_next(datetime).astimezone(UTC)


def validate_cron(cron: str | None) -> str:
    cron = (cron or "").strip()
    # Five fields only: croniter also accepts a sixth (seconds), which would fire every second.
    if len(cron.split()) != 5 or not croniter.is_valid(cron):
        raise ScheduleError(f"invalid cron expression {cron!r}; expected 5 fields like '0 9 * * 1'")
    return cron


def parse_run_at(value: str | None, tz: str, now: datetime) -> datetime:
    """ISO 8601 time; without an offset it's read in the schedule's time zone."""
    try:
        run_at = datetime.fromisoformat((value or "").strip())
    except ValueError as exc:
        raise ScheduleError(f"invalid run_at {value!r}; expected e.g. 2026-10-22T09:00") from exc
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=zone(tz))
    run_at = run_at.astimezone(UTC)
    if run_at <= now:
        raise ScheduleError("run_at is in the past")
    return run_at


def schedule_to_dict(s: PrintSchedule) -> dict[str, Any]:
    tz = ZoneInfo(s.timezone)
    local = lambda t: t.astimezone(tz).isoformat() if t else None  # noqa: E731
    return {
        "id": s.id,
        "name": s.name,
        "kind": s.kind,
        "run_at": local(s.run_at),
        "cron": s.cron,
        "timezone": s.timezone,
        "enabled": s.enabled,
        "next_run_at": local(s.next_run_at),
        "filename": s.filename,
        "options": s.options,
        "last_run_at": local(s.last_run_at),
        "last_status": s.last_status,
        "last_error": s.last_error,
        "last_job": f"/jobs/{s.last_job_id}" if s.last_job_id else None,
        "created_at": local(s.created_at),
    }


class ScheduleService:
    def __init__(self, storage: Storage, jobs: JobManager, settings: Settings):
        self.storage = storage
        self.jobs = jobs
        self.settings = settings
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def create(
        self, *, name: str, kind: Kind, run_at: str | None, cron: str | None, timezone: str | None,
        data: bytes | None, filename: str, form: dict[str, Any],
    ) -> PrintSchedule:
        """`data` is None for a test-page schedule: the page is generated fresh at each run."""
        tz = timezone or self.settings.default_timezone
        zone(tz)
        now = now_utc()
        if kind == "once":
            when, cron = parse_run_at(run_at, tz, now), None
        else:
            cron = validate_cron(cron)
            when = next_cron_run(cron, tz, now)
        # Fail now, not at 3 a.m., if the document or options can't be printed.
        sample = data if data is not None else await asyncio.to_thread(testpage.render, None, now, "")
        await build_job(sample, filename, form, self.settings.jpeg_quality)

        schedule_id = new_id()
        relative = ""
        if data is not None:
            relative = f"files/schedules/{schedule_id}{PurePath(filename).suffix.lower()[:10]}"
            await asyncio.to_thread(self.storage.path(relative).write_bytes, data)
        schedule = PrintSchedule(
            id=schedule_id, name=name or filename, kind=kind, run_at=when if kind == "once" else None,
            cron=cron, timezone=tz, enabled=True, next_run_at=when,
            file_path=relative, filename=filename, options=form,
        )
        try:
            async with self.storage.session() as session:
                session.add(schedule)
                await session.commit()
        except BaseException:
            self.storage.delete_file(relative)
            raise
        return schedule

    async def list(self) -> list[PrintSchedule]:
        async with self.storage.session() as session:
            return list((await session.scalars(select(PrintSchedule).order_by(PrintSchedule.created_at))).all())

    async def get(self, schedule_id: str) -> PrintSchedule | None:
        async with self.storage.session() as session:
            return await session.get(PrintSchedule, schedule_id)

    async def delete(self, schedule_id: str) -> bool:
        async with self.storage.session() as session:
            schedule = await session.get(PrintSchedule, schedule_id)
            if schedule is None:
                return False
            self.storage.delete_file(schedule.file_path)
            await session.delete(schedule)
            await session.commit()
        return True

    async def update(self, schedule_id: str, *, enabled: bool | None = None, name: str | None = None) -> PrintSchedule | None:
        async with self.storage.session() as session:
            schedule = await session.get(PrintSchedule, schedule_id)
            if schedule is None:
                return None
            if name:
                schedule.name = name
            if enabled is not None and enabled != schedule.enabled:
                now = now_utc()
                if enabled:
                    # Resume from now: don't replay runs skipped while disabled.
                    if schedule.kind == "cron":
                        schedule.next_run_at = next_cron_run(schedule.cron, schedule.timezone, now)
                    elif schedule.run_at and schedule.run_at > now:
                        schedule.next_run_at = schedule.run_at
                    else:
                        raise ScheduleError("this one-time schedule already ran or its time has passed")
                else:
                    schedule.next_run_at = None
                schedule.enabled = enabled
            await session.commit()
            return schedule

    async def run_now(self, schedule_id: str) -> PrintSchedule | None:
        """Print it right away; the regular timetable is unchanged."""
        async with self.storage.session() as session:
            schedule = await session.get(PrintSchedule, schedule_id)
            if schedule is None:
                return None
            await self._submit(schedule, now_utc())
            await session.commit()
            return schedule

    async def _submit(self, schedule: PrintSchedule, now: datetime) -> None:
        schedule.last_run_at = now
        try:
            if schedule.options.get("test_page"):
                data = await testpage.generate(self.jobs.printer, self.settings)
                filename = testpage.filename(testpage.now(self.settings))
            else:
                data = await asyncio.to_thread(self.storage.path(schedule.file_path).read_bytes)
                filename = schedule.filename
            job = await build_job(data, filename, schedule.options, self.settings.jpeg_quality)
            self.jobs.submit(job)
            schedule.last_status, schedule.last_error, schedule.last_job_id = "submitted", None, job.id
            log.info("schedule %s (%s) submitted print job %s", schedule.id, schedule.name, job.id)
        except (OSError, convert.UnsupportedFile) as exc:
            schedule.last_status, schedule.last_error, schedule.last_job_id = "failed", str(exc), None
            log.warning("schedule %s (%s) failed: %s", schedule.id, schedule.name, exc)

    async def fire_due(self, now: datetime) -> int:
        """Run every enabled schedule whose time has come. Returns how many were due."""
        async with self.storage.session() as session:
            due = (await session.scalars(
                select(PrintSchedule).where(PrintSchedule.enabled, PrintSchedule.next_run_at <= now)
            )).all()
            for schedule in due:
                if now - schedule.next_run_at > GRACE:
                    schedule.last_run_at, schedule.last_status = now, "missed"
                    schedule.last_error = f"was due at {schedule.next_run_at.isoformat()}, while the service was down"
                    log.warning("schedule %s (%s) missed its run", schedule.id, schedule.name)
                else:
                    await self._submit(schedule, now)
                if schedule.kind == "cron":
                    schedule.next_run_at = next_cron_run(schedule.cron, schedule.timezone, now)
                else:
                    schedule.next_run_at, schedule.enabled = None, False
            await session.commit()
            return len(due)

    async def _loop(self) -> None:
        while True:
            try:
                await self.fire_due(now_utc())
            except Exception:
                log.exception("schedule tick failed")
            await asyncio.sleep(TICK_SECONDS)
