#!/usr/bin/env python3
"""Render the Store/tile PNGs from docs/store/logo.svg (Pillow only).
Run:  python windows/make_icons.py     -> windows/Assets/*.png  (already generated; rerun only if the logo changes)"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SVG = os.path.join(HERE, "..", "docs", "store", "logo.svg")
OUT = os.path.join(HERE, "Assets")
BG = (17, 17, 20, 255)
# name -> (width, height)
SQUARE = {"StoreLogo": 50, "Square44x44Logo": 44, "Square71x71Logo": 71, "Square150x150Logo": 150, "Square310x310Logo": 310}
WIDE = {"Wide310x150Logo": (310, 150), "SplashScreen": (620, 300)}
SCALES = (100, 125, 150, 200, 400)


def logo_square(px):
    """Draw the NYXOR mark with Pillow (same shapes as docs/store/logo.svg): rounded square, open ring, N."""
    s = px * 4
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0)); d = ImageDraw.Draw(im); k = s / 32
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=7 * k, fill=BG)
    d.arc([2.2 * k, 2.2 * k, 29.8 * k, 29.8 * k], start=0, end=270, fill="white", width=max(1, int(1.9 * k)))
    d.polygon([(10.3 * k, 21.8 * k), (10.3 * k, 10.2 * k), (13.1 * k, 10.2 * k), (18.8 * k, 17.5 * k), (18.8 * k, 10.2 * k), (21.6 * k, 10.2 * k), (21.6 * k, 21.8 * k), (18.8 * k, 21.8 * k), (13.1 * k, 14.5 * k), (13.1 * k, 21.8 * k)], fill="white")
    return im.resize((px, px), Image.LANCZOS)


def save(name, w, h, scale):
    W, H = round(w * scale / 100), round(h * scale / 100)
    im = Image.new("RGBA", (W, H), BG if w != h or name == "SplashScreen" else (0, 0, 0, 0))
    m = min(W, H)
    mark = logo_square(max(16, int(m * (0.62 if w != h else 1.0))))
    im.alpha_composite(mark, ((W - mark.width) // 2, (H - mark.height) // 2))
    fn = "%s.scale-%d.png" % (name, scale)
    im.save(os.path.join(OUT, fn)); return fn


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    made = []
    for n, p in SQUARE.items():
        for sc in SCALES: made.append(save(n, p, p, sc))
    for n, (w, h) in WIDE.items():
        for sc in SCALES: made.append(save(n, w, h, sc))
    for sz in (16, 24, 32, 48, 256):       # unplated targetsize icons for taskbar/Start
        im = logo_square(sz); im.save(os.path.join(OUT, "Square44x44Logo.targetsize-%d_altform-unplated.png" % sz)); made.append(sz)
    logo_square(256).save(os.path.join(OUT, "app.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)])
    print("wrote %d images to %s" % (len(made), OUT))
