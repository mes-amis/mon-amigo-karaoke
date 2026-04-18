"""Shared pytest setup.

Puts ``src/`` on ``sys.path`` so tests can ``from karaoke import ...``
without needing the package to be pip-installed, and exposes a couple of
skip markers for optional heavy tools (ffmpeg, Pillow).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _has(tool: str) -> bool:
    return shutil.which(tool) is not None


def _has_pillow() -> bool:
    try:
        import PIL  # noqa: F401
    except ModuleNotFoundError:
        return False
    return True


needs_ffmpeg = pytest.mark.skipif(
    not _has("ffmpeg") or not _has("ffprobe"),
    reason="ffmpeg / ffprobe not installed on PATH",
)
needs_pillow = pytest.mark.skipif(
    not _has_pillow(),
    reason="Pillow is not installed (run bin/setup)",
)
