"""eSCL (AirScan / Mopria scan) client for the printer's built-in scanner.

A scan is: POST ScanSettings XML to /ScanJobs (201 + Location), then GET
{job}/NextDocument until it answers 404; each 200 is one document (the
feeder may give one per page). The job is deleted afterwards.
"""

import asyncio
import io
import socket
import time
import zipfile
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import aiohttp
import pymupdf

from .config import Settings
from .net import resolve_ipv4

SCAN_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"
NS = {"scan": SCAN_NS, "pwg": PWG_NS}

Source = Literal["flatbed", "feeder"]
ColorMode = Literal["color", "grayscale", "bw"]
Format = Literal["pdf", "jpeg"]
Size = Literal["a4", "letter", "legal", "max"]

SOURCES = {"flatbed": ("Platen", "scan:Platen/scan:PlatenInputCaps"),
           "feeder": ("Feeder", "scan:Adf/scan:AdfSimplexInputCaps")}
COLOR_MODES = {"color": "RGB24", "grayscale": "Grayscale8", "bw": "BlackAndWhite1"}
FORMATS = {"pdf": "application/pdf", "jpeg": "image/jpeg"}
SIZES_MM = {"a4": (210.0, 297.0), "letter": (215.9, 279.4), "legal": (215.9, 355.6)}
UNITS_PER_INCH = 300  # eSCL regions are in 1/300 inch
DOCUMENT_TIMEOUT = 300  # a 1200 dpi color page takes a while
BUSY_RETRY_SECONDS = 1


class ScannerError(Exception):
    def __init__(self, message: str, http_status: int = 502):
        super().__init__(message)
        self.http_status = http_status


@dataclass
class SourceCaps:
    max_width: int  # 1/300 inch
    max_height: int
    resolutions: list[int]
    color_modes: list[str]
    formats: list[str]


@dataclass
class ScanResult:
    content: bytes
    media_type: str
    extension: str
    pages: int


class ScannerClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._reserved = False

    @property
    def busy(self) -> bool:
        return self._reserved

    def reserve(self) -> None:
        """Claim the scanner; synchronous so two requests can't both get in."""
        if self._reserved:
            raise ScannerError("scanner is busy with another scan", 409)
        self._reserved = True

    def release(self) -> None:
        self._reserved = False

    async def _request(
        self, method: str, path: str, body: str | None = None, timeout: int = 30
    ) -> tuple[int, dict[str, str], bytes]:
        try:
            host = await resolve_ipv4(self.settings.printer_host, self.settings.scanner_port)
        except socket.gaierror as exc:
            raise ScannerError(f"cannot resolve scanner host {self.settings.printer_host!r}") from exc
        url = f"https://{host}:{self.settings.scanner_port}{path}"
        headers = {"Content-Type": "text/xml"} if body else {}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                # Self-signed certificate, same as the printer's IPP endpoint.
                async with session.request(method, url, data=body, headers=headers, ssl=False) as resp:
                    return resp.status, dict(resp.headers), await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ScannerError(f"cannot talk to scanner: {exc!r}") from exc

    async def _get_xml(self, name: str) -> ElementTree.Element:
        status, _, body = await self._request("GET", f"{self.settings.scanner_path}/{name}")
        if status != 200:
            raise ScannerError(f"scanner answered HTTP {status} for {name}")
        return ElementTree.fromstring(body)

    async def status(self) -> dict[str, str | None]:
        root = await self._get_xml("ScannerStatus")
        return {
            "state": root.findtext("pwg:State", namespaces=NS),
            "adf_state": root.findtext("scan:AdfState", namespaces=NS),
        }

    async def capabilities(self) -> dict[str, SourceCaps]:
        root = await self._get_xml("ScannerCapabilities")
        caps = {}
        for source, (_, path) in SOURCES.items():
            node = root.find(path, NS)
            if node is None:
                continue
            caps[source] = SourceCaps(
                max_width=int(node.findtext("scan:MaxWidth", "0", NS)),
                max_height=int(node.findtext("scan:MaxHeight", "0", NS)),
                resolutions=sorted({int(r.text) for r in node.iterfind(".//scan:DiscreteResolution/scan:XResolution", NS)}),
                color_modes=[m.text for m in node.iterfind(".//scan:ColorMode", NS)],
                formats=sorted({f.text for f in node.iterfind(".//pwg:DocumentFormat", NS)}),
            )
        return caps

    async def prepare(self, source: Source, color_mode: ColorMode, resolution: int, fmt: Format, size: Size) -> str:
        """Validate the request against the scanner and its current state; returns the job XML.

        Call with the scanner reserved, so the state can't change before run().
        """
        caps = (await self.capabilities()).get(source)
        if caps is None:
            raise ScannerError(f"this scanner has no {source}", 400)
        if resolution not in caps.resolutions:
            raise ScannerError(f"{source} supports {caps.resolutions} dpi, not {resolution}", 400)
        if COLOR_MODES[color_mode] not in caps.color_modes or FORMATS[fmt] not in caps.formats:
            raise ScannerError(f"{source} can't scan {color_mode} as {fmt}", 400)

        state = await self.status()
        if state["state"] != "Idle":
            raise ScannerError(f"scanner is {state['state']}", 409)
        if source == "feeder" and state["adf_state"] != "ScannerAdfLoaded":
            raise ScannerError("the document feeder is empty", 409)
        return self._settings_xml(caps, source, color_mode, resolution, fmt, size)

    async def run(self, settings_xml: str, source: Source, fmt: Format) -> ScanResult:
        documents = await self._run_job(settings_xml, source)
        return await asyncio.to_thread(_combine, documents, fmt)

    def _settings_xml(self, caps: SourceCaps, source: Source, color_mode: ColorMode,
                      resolution: int, fmt: Format, size: Size) -> str:
        if size == "max":
            width, height = caps.max_width, caps.max_height
        else:
            width_mm, height_mm = SIZES_MM[size]
            width = min(round(width_mm / 25.4 * UNITS_PER_INCH), caps.max_width)
            height = min(round(height_mm / 25.4 * UNITS_PER_INCH), caps.max_height)
        intent = "Photo" if fmt == "jpeg" and color_mode == "color" else "Document"
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{SCAN_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:Version>2.63</pwg:Version>
  <scan:Intent>{intent}</scan:Intent>
  <pwg:ScanRegions pwg:MustHonor="true">
    <pwg:ScanRegion>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
      <pwg:Width>{width}</pwg:Width>
      <pwg:Height>{height}</pwg:Height>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:InputSource>{SOURCES[source][0]}</pwg:InputSource>
  <scan:ColorMode>{COLOR_MODES[color_mode]}</scan:ColorMode>
  <pwg:DocumentFormat>{escape(FORMATS[fmt])}</pwg:DocumentFormat>
  <scan:DocumentFormatExt>{escape(FORMATS[fmt])}</scan:DocumentFormatExt>
  <scan:XResolution>{resolution}</scan:XResolution>
  <scan:YResolution>{resolution}</scan:YResolution>
</scan:ScanSettings>
"""

    async def _run_job(self, settings_xml: str, source: Source) -> list[bytes]:
        status, headers, body = await self._request("POST", f"{self.settings.scanner_path}/ScanJobs", settings_xml)
        if status in (409, 503):
            raise ScannerError("scanner is busy", 409)
        if status != 201 or "Location" not in headers:
            raise ScannerError(f"scanner refused the scan (HTTP {status}): {body[:200]!r}")
        # Use only the path: the Location host may be a .local name or another port.
        job_path = urlparse(headers["Location"]).path.rstrip("/")

        documents: list[bytes] = []
        deadline = time.monotonic() + DOCUMENT_TIMEOUT
        try:
            while True:
                status, _, body = await self._request("GET", f"{job_path}/NextDocument", timeout=DOCUMENT_TIMEOUT)
                if status == 200:
                    documents.append(body)
                    deadline = time.monotonic() + DOCUMENT_TIMEOUT
                    if source == "flatbed":
                        break  # one page; asking again can trigger another pass on some scanners
                elif status == 404:
                    break
                elif status == 503 and time.monotonic() < deadline:
                    await asyncio.sleep(BUSY_RETRY_SECONDS)  # still scanning
                else:
                    raise ScannerError(f"scan failed (HTTP {status})")
        finally:
            try:
                await self._request("DELETE", job_path)
            except ScannerError:
                pass
        if not documents:
            raise ScannerError("the scan produced no pages")
        return documents


def _combine(documents: list[bytes], fmt: Format) -> ScanResult:
    """Feeder scans can come back one document per page: merge PDFs, zip JPEGs."""
    if len(documents) == 1:
        if fmt == "jpeg":
            return ScanResult(documents[0], FORMATS[fmt], "jpg", 1)
        with pymupdf.open(stream=documents[0], filetype="pdf") as doc:
            return ScanResult(documents[0], FORMATS[fmt], "pdf", doc.page_count)
    if fmt == "pdf":
        with pymupdf.open() as merged:
            for doc in documents:
                with pymupdf.open(stream=doc, filetype="pdf") as part:
                    merged.insert_pdf(part)
            return ScanResult(merged.tobytes(garbage=3, deflate=True), FORMATS[fmt], "pdf", merged.page_count)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:  # JPEGs are already compressed
        for i, doc in enumerate(documents, 1):
            archive.writestr(f"page-{i:03d}.jpg", doc)
    return ScanResult(buf.getvalue(), "application/zip", "zip", len(documents))
