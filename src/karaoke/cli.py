"""CLI entry point for the karaoke video builder."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

from .background import create_synthwave_background
from .mix import mix_stems
from .render import render_video
from .stems import find_stems, song_title
from .subtitles import build_ass
from .transcribe import group_into_lines, transcribe


DEFAULT_OUTPUT_DIR = Path("~/Desktop/mon-amigo-karaoke").expanduser()


def _safe_filename(name: str) -> str:
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in name).strip() or "karaoke"


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="karaoke",
        description="Build a synthwave karaoke video from a folder of Ableton Live stems.",
    )
    ap.add_argument(
        "folder", type=Path,
        help="Folder containing stems named like 'Song (Vocals).aif', '... (Bass).aif', etc.",
    )
    ap.add_argument(
        "-o", "--output", type=Path,
        help=f"Output video path (default: {DEFAULT_OUTPUT_DIR}/<song-title>.mp4)",
    )
    ap.add_argument(
        "--model", default="medium.en",
        help="Whisper model size: tiny.en, base.en, small.en, medium.en, large. "
             "Default medium.en (~1.5 GB) balances quality and speed for song "
             "lyrics; drop to small.en for faster iteration, large for best "
             "accuracy on messy vocals.",
    )
    ap.add_argument(
        "--language", default=None,
        help="Transcription language hint (e.g. 'en'). Default: auto-detect.",
    )
    ap.add_argument(
        "--with-vocals", action="store_true",
        help="Include the vocal stem (attenuated) in the karaoke mix.",
    )
    ap.add_argument(
        "--vocals-db", type=float, default=-12.0,
        help="Gain applied to the vocal stem when --with-vocals is set (dB).",
    )
    ap.add_argument(
        "--encoder", default=None,
        choices=["libx264", "h264_videotoolbox"],
        help="Force a specific video encoder (default: videotoolbox on macOS).",
    )
    ap.add_argument(
        "--keep-intermediate", action="store_true",
        help="Save the generated background PNG, mix WAV, and ASS file alongside the video.",
    )
    args = ap.parse_args()

    try:
        stems = find_stems(args.folder)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    title = song_title(stems)
    out_path = (
        args.output or DEFAULT_OUTPUT_DIR / f"{_safe_filename(title)}.mp4"
    ).expanduser().resolve()

    print(f"[karaoke] song:    {title}")
    print(f"[karaoke] stems:   {', '.join(stems.keys())}")
    print(f"[karaoke] output:  {out_path}")

    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="karaoke-") as td:
        work = Path(td)

        mix_path = work / "mix.wav"
        print("[karaoke] mixing instrumental stems...")
        mix_stems(stems, mix_path, include_vocals=args.with_vocals, vocals_db=args.vocals_db)

        print(f"[karaoke] transcribing vocals (whisper {args.model})...")
        words = transcribe(stems["Vocals"], model_name=args.model, language=args.language)
        lines = group_into_lines(words)
        print(f"[karaoke] {len(words)} words grouped into {len(lines)} lines")

        ass_path = work / "lyrics.ass"
        build_ass(lines, ass_path, title=title)

        bg_path = work / "background.png"
        print("[karaoke] painting synthwave backdrop...")
        create_synthwave_background(bg_path)

        print("[karaoke] rendering video (ffmpeg)...")
        render_video(
            background=bg_path,
            audio=mix_path,
            subtitles=ass_path,
            out=out_path,
            title=title,
            encoder=args.encoder,
        )

        if args.keep_intermediate:
            artifacts = out_path.parent / f"{out_path.stem}_artifacts"
            artifacts.mkdir(parents=True, exist_ok=True)
            for p in (bg_path, mix_path, ass_path):
                shutil.copy2(p, artifacts / p.name)
            print(f"[karaoke] artifacts kept in {artifacts}")

    dt = time.monotonic() - t0
    print(f"[karaoke] done in {dt:.1f}s -> {out_path}")


if __name__ == "__main__":
    main()
