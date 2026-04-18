"""Locate Ableton-style stem files inside a folder."""

from __future__ import annotations

import re
from pathlib import Path

STEM_NAMES = ["Vocals", "Bass", "Drums", "Others"]
AUDIO_EXTS = {".aif", ".aiff", ".wav", ".flac", ".mp3", ".m4a"}


def find_stems(folder: Path) -> dict[str, Path]:
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Not a directory: {folder}")

    candidates = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    ]

    stems: dict[str, Path] = {}
    for stem in STEM_NAMES:
        suffix = f"({stem})"
        matches = [f for f in candidates if f.stem.endswith(suffix)]
        if len(matches) > 1:
            raise ValueError(
                f"Multiple {stem} stems in {folder}: " + ", ".join(m.name for m in matches)
            )
        if matches:
            stems[stem] = matches[0]

    if "Vocals" not in stems:
        raise ValueError(f"No (Vocals) stem found in {folder}")

    return stems


def song_title(stems: dict[str, Path]) -> str:
    name = stems["Vocals"].stem
    if name.endswith(" (Vocals)"):
        name = name[: -len(" (Vocals)")]
    # Strip trailing Ableton timestamp like " [2026-04-18 054729]"
    name = re.sub(r"\s*\[\d{4}-\d{2}-\d{2} \d{6}\]\s*$", "", name)
    # Strip leading track number like "1-07 "
    name = re.sub(r"^\d+[-\d]*\s+", "", name)
    return name.strip()
