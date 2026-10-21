"""Turn uploaded files into documents the printer accepts.

Photos become a single JPEG. PDFs become one multi-page PWG raster stream,
laid out on the target paper, which is what makes duplex possible.
"""

import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO

import numpy as np
import pymupdf
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from . import pwg

register_heif_opener()

# Printer limits (jpeg-x/y-dimension-supported, jpeg-k-octets-supported).
MAX_JPEG_PIXELS = 15000
MAX_JPEG_BYTES = 16384 * 1024
MM_PER_INCH = 25.4


class UnsupportedFile(ValueError):
    pass


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    if max(img.size) > MAX_JPEG_PIXELS:
        img.thumbnail((MAX_JPEG_PIXELS, MAX_JPEG_PIXELS))
    if img.mode != "RGB":
        img = img.convert("RGB")
    # Step quality down until the printer's size limit is met.
    for q in range(quality, 40, -10):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= MAX_JPEG_BYTES:
            return buf.getvalue()
    raise UnsupportedFile("image is too large to fit the printer's JPEG size limit")


def image_to_jpeg(data: bytes, quality: int) -> bytes:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise UnsupportedFile("not a readable image or PDF") from exc
    # Phones store rotation in EXIF; the printer would ignore it.
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, "white")
        background.paste(img, mask=img.getchannel("A"))
        img = background
    return _encode_jpeg(img, quality)


def pdf_page_count(data: bytes) -> int:
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                raise UnsupportedFile("password-protected PDFs are not supported")
            return doc.page_count
    except UnsupportedFile:
        raise
    except Exception as exc:
        raise UnsupportedFile("not a readable PDF") from exc


def parse_page_ranges(spec: str | None, page_count: int) -> list[int]:
    """'1-3,5' -> [0, 1, 2, 4]. Empty means every page."""
    if not spec or not spec.strip():
        return list(range(page_count))
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        try:
            if "-" in part:
                start, end = (int(x) for x in part.split("-", 1))
            else:
                start = end = int(part)
        except ValueError as exc:
            raise UnsupportedFile(f"bad page range: {part!r}") from exc
        if not 1 <= start <= end <= page_count:
            raise UnsupportedFile(f"page range {part!r} is outside 1-{page_count}")
        pages.extend(range(start - 1, end))
    return pages


_MEDIA_SIZE = re.compile(r"_(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(mm|in)$")


def media_size_mm(media: str) -> tuple[float, float]:
    """Size from a self-describing PWG media name, e.g. iso_a4_210x297mm."""
    match = _MEDIA_SIZE.search(media)
    if not match or media.startswith("custom_"):
        raise UnsupportedFile(f"unknown paper size {media!r}")
    scale = MM_PER_INCH if match[3] == "in" else 1.0
    return float(match[1]) * scale, float(match[2]) * scale


@dataclass
class RasterLayout:
    media: str
    dpi: int
    color: bool
    margin_mm: float  # unprintable border; "fit" keeps content inside it
    scaling: str = "fit"  # fit, fill or none (auto/auto-fit behave like fit)
    sides: str = "one-sided"
    sheet_back: str = "normal"  # pwg-raster-document-sheet-back
    copies: int = 1
    print_quality: int = 0


def _back_side_transform(sheet_back: str, tumble: bool) -> tuple[int, int]:
    """(cross-feed, feed) flips the printer expects on back sides, per PWG 5102.4."""
    if sheet_back == "flipped":
        return (-1, 1) if tumble else (1, -1)
    if sheet_back == "rotated":
        return (1, 1) if tumble else (-1, -1)
    if sheet_back == "manual-tumble":
        return (-1, -1) if tumble else (1, 1)
    return 1, 1


def _target_rect(page: pymupdf.Page, sheet: pymupdf.Rect, layout: RasterLayout, rotate: bool) -> pymupdf.Rect:
    """Where the source page goes on the sheet, in points."""
    width, height = page.rect.width, page.rect.height
    if rotate:
        width, height = height, width
    if layout.scaling == "none":
        scale = 1.0
    elif layout.scaling == "fill":
        scale = max(sheet.width / width, sheet.height / height)
    else:
        margin = layout.margin_mm / MM_PER_INCH * 72
        scale = min((sheet.width - 2 * margin) / width, (sheet.height - 2 * margin) / height)
    w, h = width * scale, height * scale
    x, y = (sheet.width - w) / 2, (sheet.height - h) / 2
    return pymupdf.Rect(x, y, x + w, y + h)


def write_pdf_as_pwg(
    data: bytes,
    pages: list[int],
    layout: RasterLayout,
    out: BinaryIO,
    on_page: Callable[[int], None] = lambda _: None,
) -> int:
    """Render the selected 0-based pages to a PWG raster stream. Returns the page count written.

    on_page(i) is called before each page; raising from it aborts the render.
    """
    width_mm, height_mm = media_size_mm(layout.media)
    width_pt, height_pt = width_mm / MM_PER_INCH * 72, height_mm / MM_PER_INCH * 72
    width_px = round(width_mm / MM_PER_INCH * layout.dpi)
    height_px = round(height_mm / MM_PER_INCH * layout.dpi)
    channels = 3 if layout.color else 1
    colorspace = pymupdf.csRGB if layout.color else pymupdf.csGRAY
    duplex = layout.sides != "one-sided"
    tumble = layout.sides == "two-sided-short-edge"
    back_transform = _back_side_transform(layout.sheet_back, tumble)

    # With duplex, pad odd-length copies with a blank page so each copy starts on a new sheet.
    one_copy: list[int | None] = list(pages)
    if duplex and len(one_copy) % 2:
        one_copy.append(None)
    sequence = one_copy * layout.copies

    with pymupdf.open(stream=data, filetype="pdf") as src, pymupdf.open() as sheet_doc:
        src.bake()  # form fields and annotations into page content, or show_pdf_page drops them
        for index in set(pages):
            # show_pdf_page lays out the unrotated page; bake /Rotate in so page.rect matches what's shown.
            if src[index].rotation:
                src[index].remove_rotation()
        out.write(pwg.SYNC_WORD)

        for i, page_index in enumerate(sequence):
            on_page(i)
            pixels = np.full((height_px, width_px, channels), 255, np.uint8)
            if page_index is not None:
                page = src[page_index]
                sheet = sheet_doc.new_page(width=width_pt, height=height_pt)
                # Turn landscape pages onto portrait paper (and vice versa).
                rotate = (page.rect.width > page.rect.height) != (width_pt > height_pt)
                sheet.show_pdf_page(_target_rect(page, sheet.rect, layout, rotate), src, page_index,
                                    rotate=90 if rotate else 0)
                pix = sheet.get_pixmap(
                    matrix=pymupdf.Matrix(width_px / width_pt, height_px / height_pt),
                    colorspace=colorspace, alpha=False,
                )
                sheet_doc.delete_page(0)
                rendered = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, channels)
                h, w = min(height_px, pix.height), min(width_px, pix.width)
                pixels[:h, :w] = rendered[:h, :w]

            cross_feed, feed = back_transform if duplex and i % 2 else (1, 1)
            if cross_feed == -1:
                pixels = pixels[:, ::-1]
            if feed == -1:
                pixels = pixels[::-1]

            header = pwg.PageHeader(
                width=width_px, height=height_px, dpi=layout.dpi, channels=channels,
                page_size_points=(round(width_pt), round(height_pt)), page_size_name=layout.media,
                total_pages=len(sequence), duplex=duplex, tumble=tumble,
                cross_feed_transform=cross_feed, feed_transform=feed, print_quality=layout.print_quality,
            )
            for chunk in pwg.encode_page(header, pixels):
                out.write(chunk)

    return len(sequence)
