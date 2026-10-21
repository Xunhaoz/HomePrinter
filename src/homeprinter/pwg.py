"""PWG Raster (PWG 5102.4) encoder.

A stream is the sync word "RaS2" followed, per page, by a 1796-byte header
and the compressed pixels. Each line starts with a line-repeat byte (extra
copies of the line, 0-255) and is made of runs: control byte 0..127 means
repeat the next pixel n+1 times, 129..255 means 257-n literal pixels follow.
"""

import struct
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

SYNC_WORD = b"RaS2"
HEADER_SIZE = 1796
CSPACE_SGRAY = 18
CSPACE_SRGB = 19
_MAX_RUN = 128
_MAX_LINE_REPEAT = 256
_LINES_PER_BATCH = 128  # bounds the size of the index arrays below


@dataclass
class PageHeader:
    width: int
    height: int
    dpi: int
    channels: int  # 1 = sgray_8, 3 = srgb_8
    page_size_points: tuple[int, int]
    page_size_name: str = ""  # PWG media name, e.g. iso_a4_210x297mm
    total_pages: int = 0
    duplex: bool = False
    tumble: bool = False
    # +1 normal, -1 reversed; describes how a back side was transformed.
    cross_feed_transform: int = 1
    feed_transform: int = 1
    print_quality: int = 0

    def pack(self) -> bytes:
        buf = bytearray(HEADER_SIZE)

        def put_str(offset: int, value: str) -> None:
            raw = value.encode("ascii")[:63]
            buf[offset:offset + len(raw)] = raw

        def put_u32(offset: int, *values: int) -> None:
            struct.pack_into(f">{len(values)}I", buf, offset, *(v & 0xFFFFFFFF for v in values))

        put_str(0, "PwgRaster")                       # MediaClass
        put_u32(272, int(self.duplex))                # Duplex
        put_u32(276, self.dpi, self.dpi)              # HWResolution
        put_u32(340, 1)                               # NumCopies
        put_u32(352, *self.page_size_points)          # PageSize
        put_u32(368, int(self.tumble))                # Tumble
        put_u32(372, self.width, self.height)         # cupsWidth, cupsHeight
        put_u32(384, 8, 8 * self.channels)            # cupsBitsPerColor, cupsBitsPerPixel
        put_u32(392, self.width * self.channels)      # cupsBytesPerLine
        put_u32(396, 0)                               # cupsColorOrder: chunky
        put_u32(400, CSPACE_SRGB if self.channels == 3 else CSPACE_SGRAY)
        put_u32(420, self.channels)                   # cupsNumColors
        put_u32(
            452,
            self.total_pages,                         # TotalPageCount
            self.cross_feed_transform,                # CrossFeedTransform
            self.feed_transform,                      # FeedTransform
            0, 0, self.width, self.height,            # ImageBox left, top, right, bottom
            0x00FFFFFF,                               # AlternatePrimary
            self.print_quality,                       # PrintQuality
        )
        put_str(1732, self.page_size_name)            # cupsPageSizeName
        return bytes(buf)


def _pixel_keys(pixels: np.ndarray) -> np.ndarray:
    """One comparable integer per pixel, shape (h, w)."""
    if pixels.shape[2] == 1:
        return pixels[:, :, 0]
    p = pixels.astype(np.uint32)
    return (p[:, :, 0] << 16) | (p[:, :, 1] << 8) | p[:, :, 2]


def _encode_lines(pixels: np.ndarray, keys: np.ndarray, line_repeats: np.ndarray) -> bytes:
    """Encode distinct lines (n, w, c); line_repeats[i] is the repeat byte for line i."""
    n, width, channels = pixels.shape
    total = n * width
    flat_keys = keys.ravel()
    src = pixels.reshape(-1)

    # Runs of equal pixels, never crossing a line boundary.
    run_break = np.empty(total, dtype=bool)
    run_break[0] = True
    np.not_equal(flat_keys[1:], flat_keys[:-1], out=run_break[1:])
    run_break[::width] = True
    run_start = np.flatnonzero(run_break)
    run_len = np.diff(np.append(run_start, total))

    # Runs of 2+ become repeat segments; adjacent single pixels merge into literal segments.
    is_repeat = run_len >= 2
    prev_repeat = np.concatenate(([True], is_repeat[:-1]))
    seg_flag = is_repeat | prev_repeat | (run_start % width == 0)
    seg_start = run_start[seg_flag]
    seg_repeat = is_repeat[seg_flag]
    seg_len = np.diff(np.append(seg_start, total))

    # Split segments into tokens of at most 128 pixels.
    tok_count = (seg_len + _MAX_RUN - 1) // _MAX_RUN
    tok_seg = np.repeat(np.arange(len(seg_start)), tok_count)
    tok_index = np.arange(len(tok_seg)) - np.repeat(np.cumsum(tok_count) - tok_count, tok_count)
    tok_px = seg_start[tok_seg] + tok_index * _MAX_RUN
    tok_len = np.minimum(_MAX_RUN, seg_len[tok_seg] - tok_index * _MAX_RUN)
    # A one-pixel literal can't be expressed (257-n >= 2), so send it as a repeat of 1.
    tok_repeat = seg_repeat[tok_seg] | (tok_len == 1)

    line_first = tok_px % width == 0
    header_bytes = 1 + line_first.astype(np.int64)
    data_px = np.where(tok_repeat, 1, tok_len)
    data_bytes = data_px * channels
    tok_size = header_bytes + data_bytes
    tok_offset = np.cumsum(tok_size) - tok_size

    out = np.empty(int(tok_size.sum()), dtype=np.uint8)
    out[tok_offset[line_first]] = line_repeats
    out[tok_offset + header_bytes - 1] = np.where(tok_repeat, tok_len - 1, 257 - tok_len)

    # Gather every token's pixel bytes into place in one shot.
    data_cum = np.cumsum(data_bytes) - data_bytes
    ramp = np.arange(int(data_bytes.sum()))
    dst = np.repeat(tok_offset + header_bytes - data_cum, data_bytes) + ramp
    src_idx = np.repeat(tok_px * channels - data_cum, data_bytes) + ramp
    out[dst] = src[src_idx]
    return out.tobytes()


def encode_page_data(pixels: np.ndarray) -> Iterator[bytes]:
    """Compress an (h, w, c) uint8 image, yielding chunks of the encoded stream."""
    height = pixels.shape[0]
    keys = _pixel_keys(pixels)

    # Collapse runs of identical lines, at most 256 per repeat byte.
    same_as_prev = np.zeros(height, dtype=bool)
    same_as_prev[1:] = np.all(keys[1:] == keys[:-1], axis=1)
    group_starts = np.flatnonzero(~same_as_prev)
    position = np.arange(height) - group_starts[np.cumsum(~same_as_prev) - 1]
    line_starts = np.flatnonzero(position % _MAX_LINE_REPEAT == 0)
    line_repeats = np.diff(np.append(line_starts, height)) - 1

    for i in range(0, len(line_starts), _LINES_PER_BATCH):
        rows = line_starts[i:i + _LINES_PER_BATCH]
        yield _encode_lines(pixels[rows], keys[rows], line_repeats[i:i + _LINES_PER_BATCH])


def encode_page(header: PageHeader, pixels: np.ndarray) -> Iterator[bytes]:
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != header.channels:
        raise ValueError("pixels must be uint8 with shape (height, width, channels)")
    if pixels.shape[:2] != (header.height, header.width):
        raise ValueError("pixel dimensions don't match the header")
    yield header.pack()
    yield from encode_page_data(np.ascontiguousarray(pixels))
