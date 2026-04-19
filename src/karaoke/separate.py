"""Auto-stem a mixed audio file with Demucs (htdemucs_6s by default).

This is the alternative to feeding in an Ableton stems folder: hand
``bin/karaoke`` an MP3/WAV/FLAC/M4A and we run Demucs to split it into
six stems, then continue the existing pipeline.

Output is cached at ``~/.cache/karaoke-demucs/<key>/`` where ``<key>`` is
a fingerprint of (resolved path, mtime, size, model). Re-running on the
same file is instant; modifying or moving the file invalidates the cache.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

from .stems import AUDIO_EXTS, Song

DEFAULT_MODEL = "htdemucs_6s"
DEFAULT_CACHE_DIR = Path("~/.cache/karaoke-demucs").expanduser()

# Demucs's stem names → our internal Song.stems keys. ``Vocals`` is the
# only special name (mix.py treats it as the karaoke vocal); everything
# else is summed into the instrumental backing track.
DEMUCS_STEM_MAP: dict[str, str] = {
    "vocals": "Vocals",
    "drums":  "Drums",
    "bass":   "Bass",
    "guitar": "Guitar",
    "piano":  "Piano",
    "other":  "Other",
}


# Subprocess runner is injectable so tests can avoid actually shelling
# out to Demucs.
Runner = Callable[[list[str]], None]


def _default_runner(cmd: list[str]) -> None:
    # Don't capture — Demucs prints a tqdm progress bar that the user
    # should see while a 4-minute song is being separated.
    subprocess.run(cmd, check=True)


def _cache_key(audio: Path, model: str) -> str:
    stat = audio.stat()
    h = hashlib.sha1()
    h.update(str(audio).encode("utf-8"))
    h.update(str(stat.st_mtime_ns).encode("utf-8"))
    h.update(str(stat.st_size).encode("utf-8"))
    h.update(model.encode("utf-8"))
    return h.hexdigest()[:16]


def _expected_stem_paths(cache_root: Path, model: str, audio: Path) -> dict[str, Path]:
    # Demucs writes <out>/<model>/<input-stem-name>/<stem>.wav
    base = cache_root / model / audio.stem
    return {name: base / f"{name}.wav" for name in DEMUCS_STEM_MAP}


def _clean_title(stem: str) -> str:
    """Strip a leading track number like '01 ' or '1-07 ' from the filename."""
    return re.sub(r"^\d+[-\d]*\s+", "", stem).strip() or stem


def is_audio_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in AUDIO_EXTS


def separate(
    audio: Path,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    model: str = DEFAULT_MODEL,
    runner: Runner = _default_runner,
) -> Song:
    """Stem ``audio`` (cached) and return a Song with mapped stems.

    Parameters
    ----------
    audio:
        Path to the mixed audio file.
    cache_dir:
        Root cache directory; one subfolder per source-file fingerprint.
    model:
        Demucs model name (default ``htdemucs_6s``).
    runner:
        Injectable subprocess runner — tests pass a stub.
    """
    audio = Path(audio).expanduser().resolve()
    if not audio.is_file():
        raise FileNotFoundError(f"audio file not found: {audio}")
    if audio.suffix.lower() not in AUDIO_EXTS:
        raise ValueError(
            f"unsupported audio extension {audio.suffix!r}; "
            f"expected one of {sorted(AUDIO_EXTS)}"
        )

    cache_root = Path(cache_dir).expanduser() / _cache_key(audio, model)
    expected = _expected_stem_paths(cache_root, model, audio)

    if not all(p.exists() for p in expected.values()):
        cache_root.mkdir(parents=True, exist_ok=True)
        # Invoke as `python -m demucs` against the *same* interpreter we're
        # running under. That guarantees we hit the venv's installed copy
        # of Demucs without depending on PATH (the venv's bin/ isn't on
        # the user's PATH unless they've activated the venv manually).
        runner([
            sys.executable, "-m", "demucs",
            "-n", model,
            "-o", str(cache_root),
            str(audio),
        ])
        missing = [str(p) for p in expected.values() if not p.exists()]
        if missing:
            raise RuntimeError(
                "Demucs finished but expected stem files are missing:\n  "
                + "\n  ".join(missing)
            )

    title = _clean_title(audio.stem)
    stems = {DEMUCS_STEM_MAP[name]: path for name, path in expected.items()}
    return Song(base=audio.stem, title=title, stems=stems)
