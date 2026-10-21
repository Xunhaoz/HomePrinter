import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pymupdf
import pytest

from homeprinter import testpage
from homeprinter.config import Settings
from homeprinter.db import Storage
from homeprinter.printer import PrinterError
from homeprinter.schedules import ScheduleService

STATUS = {
    "model": "EPSON L6490 Series", "state": "idle", "state_reasons": ["media-low-warning"], "message": None,
    "media_ready": "iso_a4_210x297mm",
    "ink": [{"name": "Black ink", "level": 47, "color": "#000000"}, {"name": "Cyan ink", "level": 59, "color": "#00FFFF"}],
}
NOW = datetime(2026, 10, 21, 20, 30, 5, tzinfo=ZoneInfo("Asia/Taipei"))


def run(coro):
    return asyncio.run(coro)


def page_text(data: bytes) -> str:
    with pymupdf.open(stream=data) as doc:
        assert doc.page_count == 1
        return doc[0].get_text()


def test_page_shows_printer_info_and_local_time():
    text = page_text(testpage.render(STATUS, NOW, "192.168.0.126"))
    for expected in ("2026-10-21 20:30:05 (Asia/Taipei)", "EPSON L6490 Series", "192.168.0.126",
                     "media-low-warning", "待機", "Black ink  47%"):
        assert expected in text


def test_page_without_printer_info_still_renders():
    assert "無法取得印表機資訊" in page_text(testpage.render(None, NOW, "printer"))


def test_page_uses_every_ink():
    with pymupdf.open(stream=testpage.render(STATUS, NOW, "p")) as doc:
        pix = doc[0].get_pixmap(dpi=20, colorspace=pymupdf.csRGB)
    pixels = [tuple(pix.samples[i:i + 3]) for i in range(0, len(pix.samples), 3)]
    share = lambda match: sum(map(match, pixels)) / len(pixels)  # noqa: E731
    assert share(lambda p: p[0] < 40 and p[1] > 215 and p[2] > 215) > 0.04  # cyan
    assert share(lambda p: p[0] > 215 and p[1] < 40 and p[2] > 215) > 0.04  # magenta
    assert share(lambda p: p[0] > 215 and p[1] > 215 and p[2] < 40) > 0.04  # yellow
    assert share(lambda p: max(p) < 40) > 0.04  # black


class FakePrinter:
    def __init__(self, fail=False):
        self.fail = fail

    async def status(self):
        if self.fail:
            raise PrinterError("cannot talk to printer")
        return STATUS


class FakeJobs:
    def __init__(self, printer):
        self.printer = printer
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


@pytest.mark.parametrize("printer_fails", [False, True])
def test_test_page_schedule_stores_no_file_and_renders_at_each_run(storage, printer_fails):
    jobs = FakeJobs(FakePrinter(fail=printer_fails))
    service = ScheduleService(storage, jobs, Settings(data_dir=storage.data_dir))
    form = {"copies": 1, "scaling": "fit", "sides": "one-sided", "dry_run": True, "test_page": True}

    schedule = run(service.create(name="", kind="cron", run_at=None, cron="0 10 * * 6", timezone=None,
                                  data=None, filename="test-page.pdf", form=form))
    assert schedule.file_path == ""
    assert not any((storage.data_dir / "files/schedules").iterdir())

    schedule = run(service.run_now(schedule.id))
    assert schedule.last_status == "submitted"
    job = jobs.submitted[0]
    assert job.kind == "pdf" and job.filename.startswith("test-page-")
    assert ("EPSON L6490 Series" in page_text(job.data)) is not printer_fails

    assert run(service.delete(schedule.id))
