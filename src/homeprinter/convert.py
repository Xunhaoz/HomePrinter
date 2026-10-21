"""Turn uploaded files into JPEG pages the printer accepts."""

import io

import pymupdf
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

# Printer limits (jpeg-x/y-dimension-supported, jpeg-k-octets-supported).
MAX_JPEG_PIXELS = 15000
MAX_JPEG_BYTES = 16384 * 1024


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
            return doc.page_count
    except Exception as exc:
        raise UnsupportedFile("not a readable PDF") from exc


def pdf_page_to_jpeg(data: bytes, index: int, dpi: int, quality: int) -> bytes:
    """Render one 0-based page. Called per page so only one is in memory at a time."""
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        pix = doc[index].get_pixmap(dpi=dpi, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return _encode_jpeg(img, quality)


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
