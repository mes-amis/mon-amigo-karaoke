"""Synthwave backdrop rendering.

Only light checks — we don't pin the exact pixels (would be brittle), but
we verify the PNG exists, is the requested size, and actually contains
colour (not just a black rectangle if something silently breaks).
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import needs_pillow


@needs_pillow
def test_background_png_has_requested_size(tmp_path: Path) -> None:
    from PIL import Image

    from karaoke.background import create_synthwave_background

    out = tmp_path / "bg.png"
    create_synthwave_background(out, size=(320, 180))

    assert out.exists()
    img = Image.open(out)
    assert img.size == (320, 180)


@needs_pillow
def test_background_has_neon_pixels(tmp_path: Path) -> None:
    from PIL import Image

    from karaoke.background import create_synthwave_background

    out = tmp_path / "bg.png"
    create_synthwave_background(out, size=(320, 180))

    img = Image.open(out).convert("RGB")
    pixels = list(img.getdata())
    # A synthwave background with no "hot" pixels means the gradient /
    # sun / grid rendering broke.
    hot = [p for p in pixels if p[0] > 200 and p[2] > 150]
    assert len(hot) > 100, "expected at least some bright pink/magenta pixels"
