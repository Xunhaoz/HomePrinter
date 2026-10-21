"""Thin async wrapper around pyipp for the one printer this service drives."""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any

from pyipp import IPP
from pyipp.enums import IppJobState, IppOperation, IppTag
from pyipp.exceptions import IPPConnectionError, IPPError, IPPParseError, IPPResponseError
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
        return attrs


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
                message = STATUS_MESSAGES.get(status, f"printer returned IPP status {status:#06x}")
                raise PrinterError(message, status) from exc
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
        reasons = attrs.get("printer-state-reasons", [])
        return {
            "model": attrs.get("printer-make-and-model"),
            "state": {3: "idle", 4: "processing", 5: "stopped"}.get(attrs.get("printer-state"), "unknown"),
            "state_reasons": reasons if isinstance(reasons, list) else [reasons],
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
            "operation-attributes-tag": {"requested-attributes": [
                "media-supported", "media-default", "print-color-mode-supported",
                "print-quality-supported", "print-scaling-supported", "copies-supported",
            ]},
        })
        attrs = response["printers"][0]
        quality_names = {v: k for k, v in QUALITY.items()}
        return {
            "media": attrs.get("media-supported", []),
            "media_default": attrs.get("media-default"),
            "color_modes": attrs.get("print-color-mode-supported", []),
            "qualities": [quality_names[int(q)] for q in attrs.get("print-quality-supported", []) if int(q) in quality_names],
            "scaling": attrs.get("print-scaling-supported", []),
            "copies_max": attrs.get("copies-supported", [1, 1])[1],
        }

    async def submit_jpeg(self, data: bytes, job_name: str, options: PrintOptions, validate: bool = False) -> int | None:
        """Send one JPEG as a Print-Job (or Validate-Job). Returns the printer's job id."""
        operation = IppOperation.VALIDATE_JOB if validate else IppOperation.PRINT_JOB
        message: dict[str, Any] = {
            "operation-attributes-tag": {
                "job-name": job_name[:255],
                # Reject unsupported options instead of silently printing with defaults.
                "ipp-attribute-fidelity": True,
                "document-format": "image/jpeg",
            },
            "job-attributes-tag": options.job_attributes(),
        }
        if not validate:
            message["data"] = data
        response = await self._execute(operation, message, timeout=120)
        if unsupported := response.get("unsupported-attributes"):
            raise PrinterError(f"printer does not support: {unsupported}", response["status-code"])
        if validate:
            return None
        return response["jobs"][0]["job-id"]

    async def job_state(self, job_id: int) -> tuple[IppJobState, list[str]]:
        response = await self._execute(IppOperation.GET_JOB_ATTRIBUTES, {
            "operation-attributes-tag": {
                "job-id": job_id,
                "requested-attributes": ["job-state", "job-state-reasons"],
            },
        })
        attrs = response["jobs"][0]
        reasons = attrs.get("job-state-reasons", [])
        return IppJobState(attrs["job-state"]), reasons if isinstance(reasons, list) else [reasons]

    async def cancel(self, job_id: int) -> None:
        await self._execute(IppOperation.CANCEL_JOB, {"operation-attributes-tag": {"job-id": job_id}})
