"""Locate Ableton-style stem files inside a folder.

A folder can contain stems for multiple songs at once. We group audio
files by the shared filename prefix that sits before the ``(Vocals)`` /
``(Bass)`` / ``(Drums)`` / ``(Others)`` suffix, and treat each group with
a vocal stem as a :class:`Song`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

STEM_NAMES = ["Vocals", "Bass", "Drums", "Others"]
AUDIO_EXTS = {".aif", ".aiff", ".wav", ".flac", ".mp3", ".m4a"}

# Matches " (Vocals)" / "(Bass)" etc. at the END of a filename stem.
# The leading whitespace is optional because Ableton always inserts one
# but other exporters might not.
_STEM_SUFFIX_RE = re.compile(r"\s*\((Vocals|Bass|Drums|Others)\)$")


@dataclass(frozen=True)
class Song:
    """One song's worth of stems found inside a folder."""

    base: str                   # raw filename prefix, exactly as on disk
    title: str                  # cleaned-up, human-facing title
    stems: dict[str, Path]      # {"Vocals": Path, "Bass": Path, ...}


def _clean_title(base: str) -> str:
    name = base
    # Strip trailing Ableton timestamp like " [2026-04-18 054729]".
    name = re.sub(r"\s*\[\d{4}-\d{2}-\d{2} \d{6}\]\s*$", "", name)
    # Strip leading track numbers like "1-07 ".
    name = re.sub(r"^\d+[-\d]*\s+", "", name)
    return name.strip()


def find_songs(folder: Path) -> list[Song]:
    """Return every song with at least a vocal stem in *folder*.

    Songs are sorted alphabetically by title so the CLI prompt is
    deterministic. A "song" here is any set of files that share a base
    name and include a ``(Vocals)`` stem; incomplete sets (e.g. vocals
    only) still count — Whisper just won't have instrumental backing.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Not a directory: {folder}")

    candidates = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    ]

    groups: dict[str, dict[str, Path]] = {}
    for path in candidates:
        match = _STEM_SUFFIX_RE.search(path.stem)
        if not match:
            continue
        base = path.stem[: match.start()]
        stem_name = match.group(1)
        bucket = groups.setdefault(base, {})
        if stem_name in bucket:
            raise ValueError(
                f"Duplicate {stem_name} stem for {base!r}: "
                f"{bucket[stem_name].name} and {path.name}"
            )
        bucket[stem_name] = path

    songs = [
        Song(base=base, title=_clean_title(base), stems=stems)
        for base, stems in groups.items()
        if "Vocals" in stems
    ]
    songs.sort(key=lambda s: s.title.lower())
    return songs
