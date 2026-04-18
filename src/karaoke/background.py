"""Generate an 80s synthwave background as a PNG using Pillow."""

from __future__ import annotations

from pathlib import Path


def create_synthwave_background(out: Path, size: tuple[int, int] = (1920, 1080)) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    w, h = size
    # Horizon at 50% of frame height — keeps the sun in the upper half so
    # the lower half stays a dark, mostly-even zone for karaoke lyrics.
    horizon_y = int(h * 0.50)

    img = Image.new("RGB", (w, h), (6, 2, 24))
    draw = ImageDraw.Draw(img, "RGBA")

    # --- Sky gradient: deep indigo at top, hot magenta near horizon -------------
    sky_top = (8, 4, 46)
    sky_mid = (60, 14, 110)
    sky_bot = (250, 70, 160)
    for y in range(horizon_y):
        t = y / max(1, horizon_y - 1)
        if t < 0.5:
            k = t / 0.5
            r = int(sky_top[0] + (sky_mid[0] - sky_top[0]) * k)
            g = int(sky_top[1] + (sky_mid[1] - sky_top[1]) * k)
            b = int(sky_top[2] + (sky_mid[2] - sky_top[2]) * k)
        else:
            k = (t - 0.5) / 0.5
            r = int(sky_mid[0] + (sky_bot[0] - sky_mid[0]) * k)
            g = int(sky_mid[1] + (sky_bot[1] - sky_mid[1]) * k)
            b = int(sky_mid[2] + (sky_bot[2] - sky_mid[2]) * k)
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    # --- Stars ------------------------------------------------------------------
    import random
    rng = random.Random(1981)
    for _ in range(180):
        sx = rng.randint(0, w - 1)
        sy = rng.randint(0, int(horizon_y * 0.6))
        a = rng.randint(120, 255)
        draw.point((sx, sy), fill=(255, 255, 255, a))

    # --- Sun: vertical gradient disk with horizontal bar cutouts ---------------
    sun_r = int(h * 0.24)
    sun_cx = w // 2
    sun_cy = int(horizon_y - h * 0.04)

    sun = Image.new("RGBA", (sun_r * 2, sun_r * 2), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sun)
    top_col = (255, 235, 90)
    bot_col = (255, 45, 160)
    for y in range(sun_r * 2):
        t = y / (sun_r * 2 - 1)
        r = int(top_col[0] + (bot_col[0] - top_col[0]) * t)
        g = int(top_col[1] + (bot_col[1] - top_col[1]) * t)
        b = int(top_col[2] + (bot_col[2] - top_col[2]) * t)
        sd.line([(0, y), (sun_r * 2, y)], fill=(r, g, b, 255))

    mask = Image.new("L", (sun_r * 2, sun_r * 2), 0)
    mdraw = ImageDraw.Draw(mask)
    mdraw.ellipse([0, 0, sun_r * 2 - 1, sun_r * 2 - 1], fill=255)
    # Horizontal cutouts across the lower half
    for i in range(6):
        y0 = int(sun_r * 2 * (0.55 + i * 0.07))
        bar_h = max(2, int(sun_r * 0.04))
        mdraw.rectangle([0, y0, sun_r * 2, y0 + bar_h], fill=0)
    sun.putalpha(mask)

    # Glow halo behind sun
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for rr in range(sun_r + 120, sun_r, -6):
        a = int(40 * (1 - (rr - sun_r) / 120))
        gd.ellipse(
            [sun_cx - rr, sun_cy - rr, sun_cx + rr, sun_cy + rr],
            fill=(255, 120, 200, a),
        )
    glow = glow.filter(ImageFilter.GaussianBlur(20))
    img.paste(glow, (0, 0), glow)
    img.paste(sun, (sun_cx - sun_r, sun_cy - sun_r), sun)

    # --- Ground: perspective neon grid ------------------------------------------
    draw.rectangle([0, horizon_y, w, h], fill=(4, 0, 14))

    grid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grid)
    grid_color = (255, 40, 200)

    # Horizontal lines recede toward horizon with quadratic spacing
    lines = 22
    for i in range(1, lines):
        t = i / lines
        y = horizon_y + int((h - horizon_y) * (t ** 2))
        alpha = int(230 * (0.35 + 0.65 * t))
        gdraw.line([(0, y), (w, y)], fill=(*grid_color, alpha), width=2)

    # Vertical lines converge to a vanishing point on the horizon
    vanishing_x = w // 2
    spread = int(w * 0.9)
    count = 26
    for i in range(-count, count + 1):
        x_bottom = vanishing_x + int(i * (spread / count))
        gdraw.line(
            [(vanishing_x, horizon_y), (x_bottom, h)],
            fill=(*grid_color, 200),
            width=2,
        )

    grid_glow = grid.filter(ImageFilter.GaussianBlur(3))
    img.paste(grid_glow, (0, 0), grid_glow)
    img.paste(grid, (0, 0), grid)

    # --- Horizon glow band ------------------------------------------------------
    band = Image.new("RGBA", (w, 60), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    for y in range(60):
        a = int(180 * (1 - abs(y - 30) / 30))
        bd.line([(0, y), (w, y)], fill=(255, 80, 200, a))
    band = band.filter(ImageFilter.GaussianBlur(6))
    img.paste(band, (0, horizon_y - 30), band)

    # --- Subtle scanlines for retro CRT feel -----------------------------------
    scan = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sdraw2 = ImageDraw.Draw(scan)
    for y in range(0, h, 3):
        sdraw2.line([(0, y), (w, y)], fill=(0, 0, 0, 28))
    img.paste(scan, (0, 0), scan)

    img.save(out, "PNG", optimize=True)
