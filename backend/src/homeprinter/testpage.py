"""Maintenance test page: printer info, the time, and enough of every ink to keep the nozzles clear.

An inkjet that sits unused dries ink in its print head. Printing this page
regularly (e.g. from a weekly schedule) pushes each colour through every
nozzle: solid bands use a fair amount of each ink, and the fine-line and
gradient patterns make gaps from clogged nozzles easy to spot.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pymupdf

from .config import Settings
from .printer import PrinterClient, PrinterError

log = logging.getLogger(__name__)

A4 = (595, 842)  # points
MARGIN = 40
INK = {"cyan": (0, 1, 1), "magenta": (1, 0, 1), "yellow": (1, 1, 0), "black": (0, 0, 0)}
INK_NAMES = {"cyan": "青 Cyan", "magenta": "洋紅 Magenta", "yellow": "黃 Yellow", "black": "黑 Black"}
STATE_NAMES = {"idle": "待機", "processing": "工作中", "stopped": "已停止", "unknown": "未知"}
GREY = (0.35, 0.35, 0.35)


def _hex(color: str | None) -> tuple[float, float, float] | None:
    if not color or not color.startswith("#") or len(color) != 7:
        return None
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def render(status: dict[str, Any] | None, now: datetime, printer_host: str) -> bytes:
    """One A4 page as PDF. `status` is PrinterClient.status(), or None if the printer didn't answer."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4[0], height=A4[1])
    # Embed a CJK font so the PDF also reads correctly outside PyMuPDF (e.g. in a browser preview).
    page.insert_font(fontname="cjk", fontbuffer=pymupdf.Font("china-t").buffer)
    width = A4[0] - 2 * MARGIN

    def text(x: float, y: float, s: str, size: float = 10, color=(0, 0, 0)) -> None:
        page.insert_text((x, y), s, fontname="cjk", fontsize=size, color=color)

    # --- header and printer info -------------------------------------------------
    text(MARGIN, 62, "HomePrinter 測試頁", 22)
    text(MARGIN, 82, "定期列印這一頁，讓每個顏色的墨水流過噴頭，避免噴頭乾掉堵塞。", 10, GREY)

    tz_name = now.tzinfo.key if isinstance(now.tzinfo, ZoneInfo) else str(now.tzinfo)
    rows = [("列印時間", f"{now:%Y-%m-%d %H:%M:%S} ({tz_name})"), ("印表機位址", printer_host)]
    if status:
        reasons = [r for r in status.get("state_reasons", []) if r != "none"]
        media = status.get("media_ready")
        rows += [
            ("型號", status.get("model") or "未知"),
            ("狀態", STATE_NAMES.get(status.get("state"), status.get("state") or "未知")),
            ("狀態訊息", ", ".join(reasons) or status.get("message") or "正常"),
            ("紙匣", ", ".join(media) if isinstance(media, list) else media or "未知"),
        ]
    else:
        rows.append(("狀態", "產生測試頁時無法取得印表機資訊"))
    y = 112
    for label, value in rows:
        text(MARGIN, y, label, 10, GREY)
        text(MARGIN + 80, y, str(value), 10)
        y += 17

    # Ink levels as reported by the printer, before this page was printed.
    ink = (status or {}).get("ink") or []
    if ink:
        y += 6
        text(MARGIN, y, "墨水量（列印前）", 10, GREY)
        y += 8
        bar_w = (width - 80) / len(ink)
        for i, marker in enumerate(ink):
            x = MARGIN + 80 + i * bar_w
            level = marker.get("level", -1)
            box = pymupdf.Rect(x, y, x + bar_w - 14, y + 14)
            page.draw_rect(box, color=GREY, width=0.5)
            if level > 0:
                filled = pymupdf.Rect(box.x0, box.y0, box.x0 + box.width * min(level, 100) / 100, box.y1)
                page.draw_rect(filled, color=None, fill=_hex(marker.get("color")) or (0, 0, 0))
            text(x, y + 27, f"{marker.get('name', '')}  {level if level >= 0 else '?'}%", 8, GREY)
        y += 36
    y += 14

    # --- solid bands: the bulk of the ink -------------------------------------------
    text(MARGIN, y, "實心色塊", 10, GREY)
    y += 8
    band = 46
    for key, rgb in INK.items():
        page.draw_rect(pymupdf.Rect(MARGIN, y, MARGIN + width, y + band), color=None, fill=rgb)
        label_color = (1, 1, 1) if key in ("black", "magenta") else (0, 0, 0)
        text(MARGIN + 8, y + band - 9, INK_NAMES[key], 9, label_color)
        y += band + 6
    # Mixed colours use two inks each.
    third = width / 3
    for i, (name, rgb) in enumerate([("紅 Red", (1, 0, 0)), ("綠 Green", (0, 0.65, 0.3)), ("藍 Blue", (0, 0.2, 0.85))]):
        page.draw_rect(pymupdf.Rect(MARGIN + i * third, y, MARGIN + (i + 1) * third - 6, y + 34), color=None, fill=rgb)
        text(MARGIN + i * third + 8, y + 24, name, 9, (1, 1, 1))
    y += 50

    # --- nozzle check: fine lines in each ink; a missing line means a clogged nozzle --------
    text(MARGIN, y, "噴嘴檢查：線條應連續、沒有缺口", 10, GREY)
    y += 8
    quarter = width / 4
    for i, rgb in enumerate(INK.values()):
        x0 = MARGIN + i * quarter
        for k in range(int((quarter - 10) / 3)):
            x = x0 + k * 3
            page.draw_line((x, y), (x, y + 44), color=rgb, width=0.7)
        for k in range(8):
            yy = y + 52 + k * 3
            page.draw_line((x0, yy), (x0 + quarter - 10, yy), color=rgb, width=0.7)
    y += 86

    # --- gradients: show banding and weak nozzles -----------------------------------
    text(MARGIN, y, "漸層", 10, GREY)
    y += 8
    steps = 20
    step_w = width / steps
    for row, rgb in enumerate(INK.values()):
        for s in range(steps):
            t = (s + 1) / steps
            mixed = tuple(1 - t * (1 - c) for c in rgb)  # from white to the full ink
            page.draw_rect(
                pymupdf.Rect(MARGIN + s * step_w, y + row * 16, MARGIN + (s + 1) * step_w, y + row * 16 + 14),
                color=None, fill=mixed,
            )
    y += 4 * 16 + 18

    text(MARGIN, min(y, A4[1] - 30), "如果色塊或線條有缺口、斷線或顏色不對，請在印表機上執行「噴頭清潔」。", 9, GREY)

    doc.subset_fonts()
    data = doc.tobytes(garbage=4, deflate=True)
    doc.close()
    return data


async def generate(printer: PrinterClient, settings: Settings) -> bytes:
    """Fetch the printer's current status and render a fresh test page."""
    try:
        status = await printer.status()
    except PrinterError as exc:
        log.warning("test page without printer info: %s", exc)
        status = None
    return await asyncio.to_thread(render, status, now(settings), settings.printer_host)


def now(settings: Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.default_timezone))


def filename(when: datetime) -> str:
    return f"test-page-{when:%Y%m%d-%H%M}.pdf"
