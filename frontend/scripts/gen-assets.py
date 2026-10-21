"""Generate the PWA icons (run with the backend's uv env)."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

out = Path(sys.argv[1])

def icon(size: int) -> Image.Image:
    s = size / 64
    img = Image.new("RGB", (size, size), "#171717")
    d = ImageDraw.Draw(img)
    w = max(2, round(4 * s))
    fg = "#fafafa"
    d.line([(22*s, 26*s), (22*s, 14*s), (42*s, 14*s), (42*s, 26*s)], fill=fg, width=w, joint="curve")
    d.rounded_rectangle([14*s, 26*s, 50*s, 42*s], radius=4*s, outline=fg, width=w)
    d.rectangle([22*s, 36*s, 42*s, 50*s], fill="#171717", outline=fg, width=w)
    return img

icon(180).save(out / "apple-touch-icon.png")
icon(192).save(out / "icon-192.png")
icon(512).save(out / "icon-512.png")
