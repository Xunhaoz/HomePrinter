import asyncio
from datetime import UTC, datetime, timedelta

import pymupdf
import pytest

from homeprinter.config import Settings
from homeprinter.db import PrintSchedule, ScanJob, Storage, now_utc
from homeprinter.scans import ScanService
from homeprinter.schedules import (
    ScheduleError, ScheduleService, next_cron_run, parse_run_at, validate_cron,
)

TAIPEI = "Asia/Taipei"


def run(coro):
    return asyncio.run(coro)


def test_cron_runs_in_the_schedule_time_zone():
    # Sunday 2026-10-25 12:00 Taipei; "Mondays 09:00 Taipei" is Monday 01:00 UTC.
    after = datetime(2026, 10, 25, 4, 0, tzinfo=UTC)
    assert next_cron_run("0 9 * * 1", TAIPEI, after) == datetime(2026, 10, 26, 1, 0, tzinfo=UTC)
    # Strictly after: exactly on a run time gives the following one.
    assert next_cron_run("0 9 * * *", TAIPEI, datetime(2026, 10, 26, 1, 0, tzinfo=UTC)) == \
        datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


@pytest.mark.parametrize("bad", ["", "every monday", "0 9 * *", "0 0 9 * * 1", "61 9 * * *"])
def test_invalid_cron_is_rejected(bad):
    with pytest.raises(ScheduleError):
        validate_cron(bad)


def test_run_at_without_offset_is_local_time():
    now = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
    assert parse_run_at("2026-10-22T09:00", TAIPEI, now) == datetime(2026, 10, 22, 1, 0, tzinfo=UTC)
    assert parse_run_at("2026-10-22T09:00+00:00", TAIPEI, now) == datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    with pytest.raises(ScheduleError):
        parse_run_at("2026-10-20T09:00", TAIPEI, now)
    with pytest.raises(ScheduleError):
        parse_run_at("tomorrow", TAIPEI, now)


class FakeJobs:
    def __init__(self):
        self.submitted = []

    def submit(self, job):
        self.submitted.append(job)
        return job


@pytest.fixture
def storage(tmp_path):
    s = Storage(tmp_path)
    run(s.open())
    yield s
    run(s.close())


def add_schedule(storage: Storage, **fields) -> str:
    with pymupdf.open() as doc:
        doc.new_page()
        pdf = doc.tobytes()
    relative = f"files/schedules/{fields['id']}.pdf"
    storage.path(relative).write_bytes(pdf)

    async def insert():
        async with storage.session() as session:
            session.add(PrintSchedule(
                name=fields["id"], timezone=TAIPEI, enabled=True, file_path=relative,
                filename="doc.pdf", options={"dry_run": True}, **fields,
            ))
            await session.commit()
    run(insert())
    return fields["id"]


def test_fire_due_submits_skips_and_advances(storage):
    now = datetime(2026, 10, 26, 1, 0, 30, tzinfo=UTC)  # Monday 09:00:30 Taipei
    add_schedule(storage, id="weekly", kind="cron", cron="0 9 * * 1",
                 next_run_at=datetime(2026, 10, 26, 1, 0, tzinfo=UTC))
    add_schedule(storage, id="once", kind="once", run_at=now - timedelta(seconds=10),
                 next_run_at=now - timedelta(seconds=10))
    add_schedule(storage, id="stale", kind="once", run_at=now - timedelta(hours=3),
                 next_run_at=now - timedelta(hours=3))
    add_schedule(storage, id="later", kind="once", run_at=now + timedelta(hours=1),
                 next_run_at=now + timedelta(hours=1))
    jobs = FakeJobs()
    service = ScheduleService(storage, jobs, Settings())

    assert run(service.fire_due(now)) == 3
    assert sorted(j.filename for j in jobs.submitted) == ["doc.pdf", "doc.pdf"]
    assert all(j.dry_run for j in jobs.submitted)

    weekly, once, stale, later = (run(service.get(i)) for i in ("weekly", "once", "stale", "later"))
    assert weekly.last_status == "submitted" and weekly.enabled
    assert weekly.next_run_at == datetime(2026, 11, 2, 1, 0, tzinfo=UTC)
    assert once.last_status == "submitted" and not once.enabled and once.next_run_at is None
    assert stale.last_status == "missed" and not stale.enabled
    assert later.last_status is None and later.enabled

    assert run(service.fire_due(now)) == 0, "nothing fires twice"


def test_pausing_and_resuming_a_cron_schedule_skips_missed_runs(storage):
    add_schedule(storage, id="daily", kind="cron", cron="0 9 * * *",
                 next_run_at=datetime(2026, 1, 1, 1, 0, tzinfo=UTC))
    service = ScheduleService(storage, FakeJobs(), Settings())
    assert run(service.update("daily", enabled=False)).next_run_at is None
    resumed = run(service.update("daily", enabled=True))
    assert resumed.enabled and resumed.next_run_at > now_utc()


def test_restart_fails_inflight_scans_and_cleanup_removes_expired(storage):
    old_file = "files/scans/old.pdf"
    storage.path(old_file).write_bytes(b"%PDF-")

    async def seed():
        async with storage.session() as session:
            session.add_all([
                ScanJob(id="inflight", status="scanning", settings={}, expires_at=now_utc() + timedelta(days=7)),
                ScanJob(id="old", status="completed", settings={}, file_path=old_file,
                        expires_at=now_utc() - timedelta(minutes=1)),
                ScanJob(id="fresh", status="completed", settings={}, expires_at=now_utc() + timedelta(days=7)),
            ])
            await session.commit()
    run(seed())

    async def scenario():
        service = ScanService(scanner=None, storage=storage, settings=Settings())
        await service.start()
        await service.stop()  # cancels the cleanup loop before its first pass
        return service, await service.delete_expired()

    service, deleted = run(scenario())
    assert deleted == 1
    assert not storage.path(old_file).exists()
    assert run(service.get("old")) is None
    inflight = run(service.get("inflight"))
    assert inflight.status == "failed" and "restart" in inflight.error
    assert run(service.get("fresh")).status == "completed"
