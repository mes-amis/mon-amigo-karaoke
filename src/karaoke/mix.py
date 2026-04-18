"""Mix stems into a single karaoke audio track via ffmpeg."""

from __future__ import annotations

import subprocess
from pathlib import Path


def mix_stems(
    stems: dict[str, Path],
    out: Path,
    include_vocals: bool = False,
    vocals_db: float = -12.0,
) -> None:
    order = [name for name in ("Bass", "Drums", "Others") if name in stems]
    if include_vocals and "Vocals" in stems:
        order.append("Vocals")

    if not order:
        raise ValueError("No instrumental stems available to mix.")

    cmd: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]
    for name in order:
        cmd += ["-i", str(stems[name])]

    filters: list[str] = []
    labels: list[str] = []
    for i, name in enumerate(order):
        gain = f"{vocals_db}dB" if name == "Vocals" else "0dB"
        filters.append(f"[{i}:a]volume={gain}[a{i}]")
        labels.append(f"[a{i}]")

    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest[out]"
    )

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-ar", "48000",
        "-ac", "2",
        "-c:a", "pcm_s16le",
        str(out),
    ]
    subprocess.run(cmd, check=True)


def audio_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())
