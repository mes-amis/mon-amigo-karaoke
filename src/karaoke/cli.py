"""CLI entry point for the karaoke video builder."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

from .background import create_synthwave_background
from .metadata import resolve_metadata
from .mix import mix_stems
from .render import render_video
from .stems import Song, find_songs
from .subtitles import build_ass
from .transcribe import group_into_lines, transcribe


DEFAULT_OUTPUT_DIR = Path("~/Desktop/mon-amigo-karaoke").expanduser()


def _safe_filename(name: str) -> str:
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in name).strip() or "karaoke"


def _output_path_for(song: Song, output_arg: Path | None, all_mode: bool) -> Path:
    """Pick an output path for one song.

    - Single song, no ``--output``: default dir + ``<title>.mp4``.
    - Single song, with ``--output``: that exact file path.
    - ``--all``, no ``--output``: default dir + ``<title>.mp4`` per song.
    - ``--all``, with ``--output``: ``--output`` is treated as a directory.
    """
    if output_arg is not None:
        base = Path(output_arg).expanduser()
        if all_mode:
            return (base / f"{_safe_filename(song.title)}.mp4").resolve()
        return base.resolve()
    return (DEFAULT_OUTPUT_DIR / f"{_safe_filename(song.title)}.mp4").resolve()


def _match_song(songs: list[Song], query: str) -> Song:
    q = query.strip().lower()
    matches = [s for s in songs if q in s.title.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(
            f"--song {query!r} matched nothing. Available:\n"
            + "\n".join(f"  - {s.title}" for s in songs)
        )
    raise ValueError(
        f"--song {query!r} is ambiguous — {len(matches)} songs match:\n"
        + "\n".join(f"  - {s.title}" for s in matches)
    )


def _prompt_for_song(songs: list[Song]) -> Song:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "multiple songs found in the stems folder but stdin isn't a "
            "terminal. Re-run with --song <name_substring> to pick one.\n"
            "Available:\n"
            + "\n".join(f"  - {s.title}" for s in songs)
        )

    print(f"\nFound {len(songs)} songs in the stems folder:", file=sys.stderr)
    for i, song in enumerate(songs, 1):
        print(f"  [{i}] {song.title}", file=sys.stderr)
    print(file=sys.stderr)

    while True:
        try:
            raw = input(f"Select a song [1-{len(songs)}]: ").strip()
        except EOFError:
            print("\naborted", file=sys.stderr)
            sys.exit(130)
        if not raw:
            continue
        try:
            idx = int(raw) - 1
        except ValueError:
            print(f"  not a number: {raw!r}", file=sys.stderr)
            continue
        if 0 <= idx < len(songs):
            return songs[idx]
        print(f"  out of range; please enter 1-{len(songs)}", file=sys.stderr)


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
        "--song", default=None,
        help="When the stems folder holds more than one song, pick one by "
             "a case-insensitive substring of its title. If omitted and "
             "several songs are present, you'll be prompted to choose.",
    )
    ap.add_argument(
        "--artist", default=None,
        help="Override the artist credit shown on the title card. "
             "If omitted, we try to look it up in Music.app (macOS only).",
    )
    ap.add_argument(
        "--album", default=None,
        help="Override the album credit shown on the title card.",
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
    ap.add_argument(
        "--all", action="store_true",
        help="Render every song found in the stems folder. Any song whose "
             "output MP4 already exists is skipped (use --rebuild to force). "
             "Incompatible with --song. When combined with --output, the "
             "argument is treated as the output directory rather than a file.",
    )
    ap.add_argument(
        "--rebuild", action="store_true",
        help="Overwrite existing output MP4s that would otherwise be skipped "
             "(only meaningful with --all; single-song runs always overwrite).",
    )
    args = ap.parse_args()

    if args.all and args.song:
        print("error: --all and --song are mutually exclusive", file=sys.stderr)
        sys.exit(2)

    try:
        songs = find_songs(args.folder)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    if not songs:
        print(
            f"error: no complete stem sets found in {args.folder}.\n"
            "Expected files named like 'Song (Vocals).aif', '... (Bass).aif', etc.",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.all:
        target_songs = songs
    else:
        try:
            if args.song:
                song = _match_song(songs, args.song)
            elif len(songs) == 1:
                song = songs[0]
            else:
                song = _prompt_for_song(songs)
        except (ValueError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(2)
        target_songs = [song]

    if args.all:
        print(f"[karaoke] batch mode: {len(target_songs)} song(s) in folder")

    processed = 0
    skipped = 0
    for song in target_songs:
        out_path = _output_path_for(song, args.output, args.all)

        # Skip already-rendered songs in batch mode unless the user asked
        # for a rebuild. Single-song runs always overwrite (matches the
        # iterate-on-visuals workflow the tool was originally built for).
        if args.all and out_path.exists() and not args.rebuild:
            print(
                f"[karaoke] skip:    {song.title} — already rendered at "
                f"{out_path} (pass --rebuild to force)"
            )
            skipped += 1
            continue

        _process_song(song, args, out_path)
        processed += 1

    if args.all:
        print(
            f"[karaoke] batch done: {processed} rendered, {skipped} skipped"
        )


def _process_song(song: Song, args: argparse.Namespace, out_path: Path) -> None:
    """Run the full mix → transcribe → subtitles → render pipeline for one song."""
    stems = song.stems
    title = song.title
    meta = resolve_metadata(
        title,
        artist_override=args.artist,
        album_override=args.album,
    )

    print(f"[karaoke] song:    {title}")
    if meta["artist"] or meta["album"]:
        credit = " — ".join(p for p in (meta["artist"], meta["album"]) if p)
        print(f"[karaoke] credit:  {credit}")
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
        build_ass(
            lines, ass_path,
            title=title, artist=meta["artist"], album=meta["album"],
        )

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
    print(f"[karaoke] done in {dt:.1f}s -> {out_path}\n")


if __name__ == "__main__":
    main()
