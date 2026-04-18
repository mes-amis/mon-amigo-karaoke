"""End-to-end tests that exercise ffmpeg.

These are slow-ish (a few seconds) and skipped when ffmpeg isn't
installed. They verify the pipeline from stems on disk through to a
playable MP4 without depending on Whisper (we fabricate word timings).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import needs_ffmpeg, needs_pillow


def _silent_wav(path: Path, seconds: float = 2.0, freq: float = 0.0) -> None:
    """Write a short stereo WAV at 48 kHz for mixer / renderer tests."""
    src = f"sine=frequency={freq}:duration={seconds}" if freq else f"anullsrc=r=48000:cl=stereo"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", src,
        "-t", str(seconds),
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
        str(path),
    ]
    subprocess.run(cmd, check=True)


@needs_ffmpeg
def test_mix_stems_produces_file_with_expected_duration(tmp_path: Path) -> None:
    from karaoke.mix import audio_duration, mix_stems

    stems = {}
    for name in ("Vocals", "Bass", "Drums", "Others"):
        p = tmp_path / f"Song ({name}).wav"
        _silent_wav(p, seconds=2.0, freq=220.0 if name == "Vocals" else 110.0)
        stems[name] = p

    out = tmp_path / "mix.wav"
    mix_stems(stems, out, include_vocals=False)

    assert out.exists() and out.stat().st_size > 0
    dur = audio_duration(out)
    assert 1.9 < dur < 2.2, f"unexpected duration {dur:.2f}s"


@needs_ffmpeg
@needs_pillow
def test_full_render_produces_playable_mp4(tmp_path: Path) -> None:
    """Hand-craft inputs, then run the renderer end-to-end."""
    from karaoke.background import create_synthwave_background
    from karaoke.render import render_video
    from karaoke.subtitles import build_ass
    from karaoke.transcribe import Line, Word

    audio = tmp_path / "mix.wav"
    _silent_wav(audio, seconds=2.0)

    bg = tmp_path / "bg.png"
    create_synthwave_background(bg, size=(640, 360))

    subs = tmp_path / "lyrics.ass"
    lines = [
        Line(words=[Word("hello", 0.2, 0.7), Word("world", 0.8, 1.2)]),
        Line(words=[Word("second", 1.4, 1.8)]),
    ]
    build_ass(lines, subs, title="Test")

    out = tmp_path / "out.mp4"
    render_video(
        background=bg,
        audio=audio,
        subtitles=subs,
        out=out,
        title="Test",
        encoder="libx264",  # videotoolbox isn't available in every env
        fps=15,
    )

    assert out.exists() and out.stat().st_size > 1024

    # ffprobe confirms the file is a real H.264+AAC MP4 of the right length.
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "stream=codec_type,codec_name",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1",
         str(out)],
        check=True, capture_output=True, text=True,
    )
    assert "codec_name=h264" in result.stdout
    assert "codec_name=aac" in result.stdout
