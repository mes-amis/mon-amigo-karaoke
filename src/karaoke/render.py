"""Render the final karaoke video with ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _pick_video_encoder(preferred: str | None) -> list[str]:
    if preferred:
        return _encoder_args(preferred)
    if sys.platform == "darwin":
        return _encoder_args("h264_videotoolbox")
    return _encoder_args("libx264")


def _encoder_args(name: str) -> list[str]:
    if name == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", "6M", "-pix_fmt", "yuv420p"]
    # libx264 default
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]


def render_video(
    background: Path,
    audio: Path,
    subtitles: Path,
    out: Path,
    title: str = "",
    encoder: str | None = None,
    fps: int = 30,
) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH. Install with: brew install ffmpeg")

    # Run ffmpeg from the subtitle's directory so we can reference the .ass
    # file by basename and avoid filter-path escaping pitfalls.
    work = subtitles.parent
    bg_name = background.name
    subs_name = subtitles.name

    if background.parent != work:
        shutil.copy2(background, work / bg_name)
    audio_path = audio if audio.parent == work else (work / audio.name)
    if audio.parent != work:
        shutil.copy2(audio, audio_path)

    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-loop", "1", "-framerate", str(fps), "-i", bg_name,
        "-i", audio_path.name,
        "-vf", f"subtitles={subs_name}",
        *_pick_video_encoder(encoder),
        "-r", str(fps),
        "-c:a", "aac", "-b:a", "256k",
        "-shortest",
        "-movflags", "+faststart",
    ]
    if title:
        cmd += ["-metadata", f"title={title}"]
    cmd.append(str(out))

    subprocess.run(cmd, check=True, cwd=str(work))
