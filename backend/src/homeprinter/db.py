"""SQLite storage: print schedules and scan jobs. Files live next to the
database under data_dir; rows only hold their relative paths."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, TypeDecorator
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_utc() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    # Random, so ids can't be guessed by other people on the LAN.
    return uuid.uuid4().hex


class UTCDateTime(TypeDecorator):
    """SQLite has no time zones: store naive UTC, hand back aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    pass


class PrintSchedule(Base):
    __tablename__ = "print_schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10))  # "once" or "cron"
    run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # once
    cron: Mapped[str | None] = mapped_column(String(100))  # cron, evaluated in `timezone`
    timezone: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)

    file_path: Mapped[str] = mapped_column(String(300))  # relative to data_dir
    filename: Mapped[str] = mapped_column(String(255))
    options: Mapped[dict[str, Any]] = mapped_column(JSON)  # /print form fields

    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_status: Mapped[str | None] = mapped_column(String(20))  # submitted, failed, missed
    last_error: Mapped[str | None] = mapped_column(Text)
    last_job_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now_utc)


class ScanJob(Base):
    __tablename__ = "scan_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(20), default="scanning")  # scanning, completed, failed
    settings: Mapped[dict[str, Any]] = mapped_column(JSON)  # /scan form fields

    file_path: Mapped[str | None] = mapped_column(String(300))  # relative to data_dir
    media_type: Mapped[str | None] = mapped_column(String(50))
    filename: Mapped[str | None] = mapped_column(String(100))
    pages: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


class Storage:
    """Database engine plus the directory that holds stored files."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.engine: AsyncEngine | None = None
        self.session: async_sessionmaker | None = None

    async def open(self) -> None:
        for sub in ("files/schedules", "files/scans"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.data_dir / 'homeprinter.db'}")
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        if self.engine:
            await self.engine.dispose()

    def path(self, relative: str) -> Path:
        return self.data_dir / relative

    def delete_file(self, relative: str | None) -> None:
        if relative:
            self.path(relative).unlink(missing_ok=True)
