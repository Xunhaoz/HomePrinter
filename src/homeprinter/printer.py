"""Thin async wrapper around pyipp for the one printer this service drives."""

import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, BinaryIO

import aiohttp
from pyipp import IPP
from pyipp.const import DEFAULT_CHARSET, DEFAULT_CHARSET_LANGUAGE, DEFAULT_PROTO_VERSION
from pyipp.enums import IppJobState, IppOperation, IppTag
from pyipp.exceptions import IPPConnectionError, IPPError, IPPParseError, IPPResponseError
from pyipp.parser import parse as parse_response
from pyipp.serializer import encode_dict
from pyipp.tags import ATTRIBUTE_TAG_MAP

from .config import Settings

# pyipp silently drops attributes missing from its tag map, so register the
# ones we send. "media" is a keyword per RFC 8011; pyipp maps it to name.
ATTRIBUTE_TAG_MAP.update({
    "media": IppTag.KEYWORD,
    "print-color-mode": IppTag.KEYWORD,
    "print-scaling": IppTag.KEYWORD,
    "ipp-attribute-fidelity": IppTag.BOOLEAN,
})

QUALITY = {"draft": 3, "normal": 4, "high": 5}
TERMINAL_STATES = {IppJobState.CANCELED, IppJobState.ABORTED, IppJobState.COMPLETED}
STATUS_BUSY = 0x0507
STATUS_NOT_ACCEPTING = 0x0506
STATUS_MESSAGES = {
    0x040A: "document format not supported",
    0x040B: "printer does not support one of the requested options",
    STATUS_NOT_ACCEPTING: "printer is not accepting jobs",
    STATUS_BUSY: "printer is busy",
}
UPLOAD_CHUNK = 1024 * 1024


class PrinterError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        return self.status_code in (STATUS_BUSY, STATUS_NOT_ACCEPTING, None)


@dataclass
class PrintOptions:
    copies: int = 1
    media: str | None = None
    color_mode: str | None = None
    quality: str | None = None
    scaling: str | None = None
    sides: str | None = None

    def job_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"copies": self.copies}
        if self.media:
            attrs["media"] = self.media
        if self.color_mode:
            attrs["print-color-mode"] = self.color_mode
        if self.quality:
            attrs["print-quality"] = QUALITY[self.quality]
        if self.scaling:
            attrs["print-scaling"] = self.scaling
        if self.sides:
            attrs["sides"] = self.sides
        return attrs


@dataclass
class RasterCapabilities:
    media_default: str
    resolutions: list[int]
    color: bool
    sheet_back: str
    margin_mm: float


@dataclass
class JobProgress:
    state: IppJobState
    reasons: list[str]
    impressions: int


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else [value]


def _check_response(response: dict[str, Any]) -> dict[str, Any]:
    status = response["status-code"]
    if status >= 0x0200:
        raise PrinterError(STATUS_MESSAGES.get(status, f"printer returned IPP status {status:#06x}"), status)
    return response


class PrinterClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def _resolve_host(self) -> str:
        host = self.settings.printer_host
        try:
            ipaddress.ip_address(host)
            return host
        except ValueError:
            pass
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, self.settings.printer_port, family=socket.AF_INET)
        except socket.gaierror as exc:
            raise PrinterError(f"cannot resolve printer host {host!r}") from exc
        return infos[0][4][0]

    async def _execute(self, operation: IppOperation, message: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
        host = await self._resolve_host()
        # The printer answers plain IPP with "426 Upgrade Required"; its
        # certificate is self-signed, so TLS is used without verification.
        async with IPP(
            host=host,
            port=self.settings.printer_port,
            base_path=self.settings.printer_path,
            tls=True,
            verify_ssl=False,
            request_timeout=timeout,
        ) as ipp:
            message.setdefault("operation-attributes-tag", {}).setdefault("requesting-user-name", "homeprinter")
            try:
                return await ipp.execute(operation, message)
            except IPPError as exc:
                status = exc.args[1]["status-code"] if len(exc.args) > 1 else None
                text = STATUS_MESSAGES.get(status, f"printer returned IPP status {status:#06x}")
                raise PrinterError(text, status) from exc
            except (IPPConnectionError, IPPResponseError, IPPParseError) as exc:
                raise PrinterError(f"cannot talk to printer: {exc}") from exc

    async def status(self) -> dict[str, Any]:
        response = await self._execute(IppOperation.GET_PRINTER_ATTRIBUTES, {
            "operation-attributes-tag": {"requested-attributes": [
                "printer-make-and-model", "printer-state", "printer-state-reasons",
                "printer-state-message", "marker-names", "marker-levels", "marker-colors", "media-ready",
            ]},
        })
        attrs = response["printers"][0]
        return {
            "model": attrs.get("printer-make-and-model"),
            "state": {3: "idle", 4: "processing", 5: "stopped"}.get(attrs.get("printer-state"), "unknown"),
            "state_reasons": _as_list(attrs.get("printer-state-reasons", [])),
            "message": attrs.get("printer-state-message") or None,
            "media_ready": attrs.get("media-ready"),
            "ink": [
                {"name": name, "level": level, "color": color}
                for name, level, color in zip(
                    attrs.get("marker-names", []), attrs.get("marker-levels", []), attrs.get("marker-colors", [])
                )
            ],
        }

    async def capabilities(self) -> dict[str, Any]:
        response = await self._execute(IppOperation.GET_PRINTER_ATTRIBUTES, {
            "operation-attributes-tag": {
                # Supported sides/copies depend on the format; PDFs are sent as PWG raster.
                "document-format": "image/pwg-raster",
                "requested-attributes": [
                    "media-supported", "media-default", "print-color-mode-supported",
                    "print-quality-supported", "print-scaling-supported", "sides-supported",
                ],
            },
        })
        attrs = response["printers"][0]
        quality_names = {v: k for k, v in QUALITY.items()}
        return {
            "media": attrs.get("media-supported", []),
            "media_default": attrs.get("media-default"),
            "color_modes": attrs.get("print-color-mode-supported", []),
            "qualities": [quality_names[int(q)] for q in attrs.get("print-quality-supported", []) if int(q) in quality_names],
            "scaling": attrs.get("print-scaling-supported", []),
            "sides_pdf": _as_list(attrs.get("sides-supported", [])),
        }

    async def raster_capabilities(self) -> RasterCapabilities:
        response = await self._execute(IppOperation.GET_PRINTER_ATTRIBUTES, {
            "operation-attributes-tag": {
                "document-format": "image/pwg-raster",
                "requested-attributes": [
                    "document-format-supported", "media-default",
                    "pwg-raster-document-resolution-supported", "pwg-raster-document-type-supported",
                    "pwg-raster-document-sheet-back", "media-left-margin-supported",
                    "media-right-margin-supported", "media-top-margin-supported", "media-bottom-margin-supported",
                ],
            },
        })
        attrs = response["printers"][0]
        if "image/pwg-raster" not in _as_list(attrs.get("document-format-supported", [])):
            raise PrinterError("printer does not accept PWG raster, so PDFs can't be printed")
        margins = [
            min(_as_list(attrs.get(f"media-{side}-margin-supported", 0)))
            for side in ("left", "right", "top", "bottom")
        ]
        return RasterCapabilities(
            media_default=attrs.get("media-default", "iso_a4_210x297mm"),
            resolutions=sorted({r[0] for r in _as_list(attrs.get("pwg-raster-document-resolution-supported", []))}),
            color="srgb_8" in _as_list(attrs.get("pwg-raster-document-type-supported", [])),
            sheet_back=attrs.get("pwg-raster-document-sheet-back", "normal"),
            margin_mm=max(margins) / 100,
        )

    async def submit(
        self,
        document: bytes | BinaryIO,
        document_format: str,
        job_name: str,
        options: PrintOptions,
        validate: bool = False,
        timeout: int = 300,
    ) -> int | None:
        """Send a Print-Job (or Validate-Job). Returns the printer's job id.

        File objects are streamed from their start, so large PWG documents
        never have to be held in memory.
        """
        host = await self._resolve_host()
        port, path = self.settings.printer_port, self.settings.printer_path
        message = {
            "version": DEFAULT_PROTO_VERSION,
            "operation": IppOperation.VALIDATE_JOB if validate else IppOperation.PRINT_JOB,
            "request-id": None,
            "operation-attributes-tag": {  # charset, language and uri must come first
                "attributes-charset": DEFAULT_CHARSET,
                "attributes-natural-language": DEFAULT_CHARSET_LANGUAGE,
                "printer-uri": f"ipps://{host}:{port}{path}",
                "requesting-user-name": "homeprinter",
                "job-name": job_name[:255],
                # Reject unsupported options instead of silently printing with defaults.
                "ipp-attribute-fidelity": True,
                "document-format": document_format,
            },
            "job-attributes-tag": options.job_attributes(),
        }
        request_head = encode_dict(message)

        async def body() -> AsyncIterator[bytes]:
            yield request_head
            if validate:
                return
            if isinstance(document, bytes):
                yield document
                return
            document.seek(0)
            while chunk := await asyncio.to_thread(document.read, UPLOAD_CHUNK):
                yield chunk

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.post(
                    f"https://{host}:{port}{path}",
                    data=body(),
                    headers={"Content-Type": "application/ipp"},
                    ssl=False,
                ) as resp:
                    if resp.status != 200:
                        raise PrinterError(f"printer answered HTTP {resp.status}")
                    raw = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PrinterError(f"cannot talk to printer: {exc!r}") from exc

        try:
            response = parse_response(raw)
        except Exception as exc:
            raise PrinterError("unreadable response from printer") from exc
        _check_response(response)
        if unsupported := response.get("unsupported-attributes"):
            raise PrinterError(f"printer does not support: {unsupported}", response["status-code"])
        if validate:
            return None
        return response["jobs"][0]["job-id"]

    async def job_progress(self, job_id: int) -> JobProgress:
        response = await self._execute(IppOperation.GET_JOB_ATTRIBUTES, {
            "operation-attributes-tag": {
                "job-id": job_id,
                "requested-attributes": ["job-state", "job-state-reasons", "job-impressions-completed"],
            },
        })
        attrs = response["jobs"][0]
        return JobProgress(
            state=IppJobState(attrs["job-state"]),
            reasons=_as_list(attrs.get("job-state-reasons", [])),
            impressions=attrs.get("job-impressions-completed", 0) or 0,
        )

    async def cancel(self, job_id: int) -> None:
        await self._execute(IppOperation.CANCEL_JOB, {"operation-attributes-tag": {"job-id": job_id}})
