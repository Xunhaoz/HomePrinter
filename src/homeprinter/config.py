"""Settings read from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # mDNS name or IP. A .local name is resolved to IPv4 on every connection,
    # because the IPv6 link-local address it also resolves to is unreachable.
    printer_host: str = os.environ.get("PRINTER_HOST", "EPSONE62DA3.local")
    printer_port: int = int(os.environ.get("PRINTER_PORT", "631"))
    printer_path: str = os.environ.get("PRINTER_PATH", "/ipp/print")
    # eSCL (AirScan) on the same device; the L6490 serves it over HTTPS.
    scanner_port: int = int(os.environ.get("SCANNER_PORT", "443"))
    scanner_path: str = os.environ.get("SCANNER_PATH", "/eSCL")
    pdf_dpi: int = int(os.environ.get("PDF_DPI", "300"))
    jpeg_quality: int = int(os.environ.get("JPEG_QUALITY", "92"))
    max_upload_mb: int = int(os.environ.get("MAX_UPLOAD_MB", "100"))
    # SQLite database and stored files (schedule documents, scans).
    data_dir: Path = Path(os.environ.get("DATA_DIR", "data"))
    # Schedules without an explicit timezone use this one.
    default_timezone: str = os.environ.get("DEFAULT_TIMEZONE", "Asia/Taipei")
    scan_retention_days: int = int(os.environ.get("SCAN_RETENTION_DAYS", "7"))


settings = Settings()
