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
    # Anything that isn't ``Vocals`` is instrumental — this works for
    # both the Ableton 4-stem layout (Bass/Drums/Others) and Demucs's
    # 6-stem layout (Bass/Drums/Other/Guitar/Piano), without needing a
    # hard-coded name list.
    instrumental_order = sorted(name for name in stems if name != "Vocals")

    order = list(instrumental_order)
    if include_vocals and "Vocals" in stems:
        order.append("Vocals")

    if not order:
        raise ValueError("No instrumental stems available to mix.")

    n = len(order)
    cmd: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]
    for name in order:
        cmd += ["-i", str(stems[name])]

    # amix averages its inputs (output = sum / N) and has no portable way to
    # disable that before ffmpeg 4.4 (`normalize=0`). To keep per-stem levels
    # intact on older ffmpeg builds, we pre-scale each input by N so the
    # implicit /N in amix cancels. The vocal stem also gets its attenuation
    # dB applied in the same chain.
    filters: list[str] = []
    labels: list[str] = []
    for i, name in enumerate(order):
        chain = [f"volume={n}"]
        if name == "Vocals":
            chain.append(f"volume={vocals_db}dB")
        filters.append(f"[{i}:a]{','.join(chain)}[a{i}]")
        labels.append(f"[a{i}]")

    filters.append(f"{''.join(labels)}amix=inputs={n}[out]")

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
