"""Generate the PWA icons and the nozzle-check test page (run with the backend's uv env)."""
import sys
from pathlib import Path

import pymupdf
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

# A4 colour test page: solid CMYK + RGB bars and fine lines, so every nozzle gets ink.
doc = pymupdf.open()
page = doc.new_page(width=595, height=842)
page.insert_text((50, 70), "HomePrinter nozzle check", fontsize=20)
page.insert_text((50, 92), "Weekly test page: keeps the ink flowing so the print head doesn't clog.", fontsize=10, color=(0.4, 0.4, 0.4))
colors = [(0, 1, 1), (1, 0, 1), (1, 1, 0), (0, 0, 0), (1, 0, 0), (0, 0.6, 0), (0, 0, 1)]
for i, c in enumerate(colors):
    page.draw_rect(pymupdf.Rect(50, 120 + i * 55, 545, 165 + i * 55), color=None, fill=c)
for i, c in enumerate(colors[:4]):
    x0 = 50 + i * 125
    for k in range(40):
        x = x0 + k * 2.8
        page.draw_line((x, 520), (x, 640), color=c, width=0.6)
for i in range(11):
    g = i / 10
    page.draw_rect(pymupdf.Rect(50 + i * 45, 670, 95 + i * 45, 720), color=None, fill=(g, g, g))
page.insert_text((50, 760), "Look for gaps or missing lines in the bars above. If any, run a head cleaning from the printer.", fontsize=9, color=(0.4, 0.4, 0.4))
doc.save(out / "nozzle-check.pdf", garbage=4, deflate=True)
