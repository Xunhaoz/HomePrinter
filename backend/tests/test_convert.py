import io

import numpy as np
import pymupdf
import pytest

from homeprinter.convert import RasterLayout, UnsupportedFile, media_size_mm, parse_page_ranges, write_pdf_as_pwg
from homeprinter.pwg import HEADER_SIZE
from test_pwg import decode

DPI = 50  # A4 = 413x585 px, plenty to check layout


@pytest.fixture(scope="module")
def pdf() -> bytes:
    doc = pymupdf.open()
    for i, (w, h, rotation) in enumerate([(595, 842, 0), (842, 595, 0), (595, 842, 90)]):
        page = doc.new_page(width=w, height=h)
        page.draw_rect(page.rect + (20, 20, -20, -20), color=(0, 0, 0), width=4)
        page.insert_text((50, 90), f"PAGE {i + 1}", fontsize=36)
        page.set_rotation(rotation)
    return doc.tobytes()


def render(pdf: bytes, **layout) -> list[tuple[bytes, np.ndarray]]:
    out = io.BytesIO()
    count = write_pdf_as_pwg(
        pdf, [0, 1, 2], RasterLayout(media="iso_a4_210x297mm", dpi=DPI, color=False, margin_mm=3, **layout), out
    )
    data, pos, pages = out.getvalue(), 4, []
    assert data[:4] == b"RaS2"
    for _ in range(count):
        header = data[pos:pos + HEADER_SIZE]
        width, height = (int.from_bytes(header[o:o + 4], "big") for o in (372, 376))
        end = _page_end(data, pos + HEADER_SIZE, width, height)
        pages.append((header, decode(data[pos + HEADER_SIZE:end], width, height, 1)))
        pos = end
    assert pos == len(data)
    return pages


def _page_end(data: bytes, pos: int, width: int, height: int) -> int:
    y = 0
    while y < height:
        y += data[pos] + 1
        pos += 1
        x = 0
        while x < width:
            control = data[pos]
            n = control + 1 if control < 128 else 257 - control
            pos += 2 if control < 128 else 1 + n
            x += n
    return pos


def test_fit_keeps_every_page_inside_the_margins(pdf):
    margin_px = int(3 / 25.4 * DPI)
    for _, px in render(pdf):
        assert px.shape == (585, 413, 1)
        assert px.min() < 255, "page is blank"
        assert px[:margin_px].min() == 255 and px[-margin_px:].min() == 255
        assert px[:, :margin_px].min() == 255 and px[:, -margin_px:].min() == 255


def test_long_edge_duplex_rotates_backs_and_pads_copies(pdf):
    single = [px for _, px in render(pdf)]
    duplex = render(pdf, sides="two-sided-long-edge", sheet_back="rotated", copies=2)
    order = [0, 1, 2, None] * 2  # odd page count is padded with a blank back per copy
    assert len(duplex) == len(order)
    for i, ((header, px), source) in enumerate(zip(duplex, order)):
        assert header[272:276] == b"\0\0\0\1"  # Duplex
        if source is None:
            assert px.min() == 255
        elif i % 2:
            np.testing.assert_array_equal(px, single[source][::-1, ::-1])
            assert header[456:464] == b"\xff" * 8  # CrossFeed/FeedTransform = -1
        else:
            np.testing.assert_array_equal(px, single[source])


def test_short_edge_duplex_leaves_backs_alone_on_rotated_printers(pdf):
    single = [px for _, px in render(pdf)]
    tumble = render(pdf, sides="two-sided-short-edge", sheet_back="rotated")
    assert tumble[1][0][368:372] == b"\0\0\0\1"  # Tumble
    np.testing.assert_array_equal(tumble[1][1], single[1])


def test_media_size_mm():
    assert media_size_mm("iso_a4_210x297mm") == (210, 297)
    assert media_size_mm("na_index-4x6_4x6in") == pytest.approx((101.6, 152.4))
    with pytest.raises(UnsupportedFile):
        media_size_mm("custom_max_215.9x1200mm")
    with pytest.raises(UnsupportedFile):
        media_size_mm("bogus")


def test_parse_page_ranges():
    assert parse_page_ranges("1-3,5", 5) == [0, 1, 2, 4]
    assert parse_page_ranges(None, 2) == [0, 1]
    with pytest.raises(UnsupportedFile):
        parse_page_ranges("2-9", 3)
