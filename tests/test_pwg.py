import struct

import numpy as np
import pytest

from homeprinter.pwg import HEADER_SIZE, PageHeader, encode_page, encode_page_data


def decode(data: bytes, width: int, height: int, channels: int) -> np.ndarray:
    """Straightforward PWG 5102.4 decoder, written independently of the encoder."""
    out = np.empty((height, width, channels), dtype=np.uint8)
    pos = y = 0
    while y < height:
        repeat = data[pos] + 1
        pos += 1
        line = bytearray()
        while len(line) < width * channels:
            control = data[pos]
            pos += 1
            if control == 128:
                raise AssertionError("0x80 control byte is never emitted")
            if control < 128:
                line += data[pos:pos + channels] * (control + 1)
                pos += channels
            else:
                count = (257 - control) * channels
                line += data[pos:pos + count]
                pos += count
        assert len(line) == width * channels, "run crossed the end of a line"
        row = np.frombuffer(bytes(line), dtype=np.uint8).reshape(width, channels)
        for _ in range(repeat):
            out[y] = row
            y += 1
    assert pos == len(data), "trailing bytes after the last line"
    return out


def roundtrip(pixels: np.ndarray) -> None:
    h, w, c = pixels.shape
    encoded = b"".join(encode_page_data(pixels))
    np.testing.assert_array_equal(decode(encoded, w, h, c), pixels)


rng = np.random.default_rng(0)


@pytest.mark.parametrize("channels", [1, 3])
@pytest.mark.parametrize("name, make", [
    ("noise", lambda h, w, c: rng.integers(0, 256, (h, w, c), dtype=np.uint8)),
    ("white", lambda h, w, c: np.full((h, w, c), 255, np.uint8)),
    ("few colors", lambda h, w, c: rng.integers(0, 2, (h, w, c), dtype=np.uint8) * 255),
    ("vertical stripes", lambda h, w, c: np.broadcast_to(
        (np.arange(w) // 3 % 2 * 255).astype(np.uint8)[None, :, None], (h, w, c)).copy()),
    ("long runs", lambda h, w, c: np.broadcast_to(
        (np.arange(w) // 300 * 40).astype(np.uint8)[None, :, None], (h, w, c)).copy()),
])
def test_roundtrip_patterns(name, make, channels):
    roundtrip(make(37, 1000, channels))


@pytest.mark.parametrize("width", [1, 2, 127, 128, 129, 257])
def test_roundtrip_run_boundaries(width):
    pixels = rng.integers(0, 2, (5, width, 3), dtype=np.uint8) * 255
    roundtrip(pixels)
    roundtrip(np.zeros((5, width, 3), np.uint8))


def test_line_repeats_over_256_and_batches():
    # 600 identical lines need three repeat groups; >128 distinct lines span batches.
    block = np.full((600, 50, 1), 200, np.uint8)
    noise = rng.integers(0, 256, (300, 50, 1), dtype=np.uint8)
    roundtrip(np.concatenate([block, noise, block]))


def test_white_page_is_tiny():
    encoded = b"".join(encode_page_data(np.full((3508, 2480, 3), 255, np.uint8)))
    # 14 repeat groups x (repeat byte + 20 tokens x 4 bytes)
    assert len(encoded) < 2000


def test_header_fields():
    header = PageHeader(
        width=2480, height=3508, dpi=300, channels=3, page_size_points=(595, 842),
        page_size_name="iso_a4_210x297mm", total_pages=4, duplex=True,
        cross_feed_transform=-1, feed_transform=-1, print_quality=5,
    )
    raw = header.pack()
    assert len(raw) == HEADER_SIZE
    u32 = lambda off: struct.unpack_from(">I", raw, off)[0]  # noqa: E731
    assert raw[:10] == b"PwgRaster\0"
    assert u32(272) == 1 and u32(368) == 0
    assert (u32(276), u32(280)) == (300, 300)
    assert (u32(352), u32(356)) == (595, 842)
    assert (u32(372), u32(376), u32(384), u32(388), u32(392)) == (2480, 3508, 8, 24, 7440)
    assert (u32(400), u32(420)) == (19, 3)
    assert u32(452) == 4
    assert (u32(456), u32(460)) == (0xFFFFFFFF, 0xFFFFFFFF)
    assert (u32(464), u32(468), u32(472), u32(476)) == (0, 0, 2480, 3508)
    assert u32(480) == 0x00FFFFFF and u32(484) == 5
    assert raw[1732:1748] == b"iso_a4_210x297mm"


def test_encode_page_rejects_mismatched_pixels():
    header = PageHeader(width=10, height=10, dpi=300, channels=1, page_size_points=(1, 1))
    with pytest.raises(ValueError):
        list(encode_page(header, np.zeros((10, 10, 3), np.uint8)))
