# custom-karaoke

Turn an Ableton Live stems folder into an 80s-synthwave karaoke video.

`bin/karaoke <stems_folder>` mixes the instrumental stems, transcribes the vocal
stem locally with Whisper to get word-level timing, and renders a 1080p MP4 with
a neon grid + sunset backdrop and per-word highlighted lyrics.

## Requirements

- macOS (the default encoder is Apple VideoToolbox; `libx264` also works)
- Python 3.10+
- [Homebrew](https://brew.sh) — used by `bin/setup` to install ffmpeg

## Setup

Run the bootstrap script once:

```sh
./bin/setup
```

It verifies Python 3.10+, installs ffmpeg via Homebrew if missing, creates a
local `.venv`, installs `openai-whisper` + `Pillow` (PyTorch comes along for
the ride, ~1 GB one-time), and pre-downloads the default Whisper model.
Re-running is a no-op for anything already in place.

Useful flags:

```sh
./bin/setup --no-model          # skip the Whisper weight download
./bin/setup --model small.en    # pre-download a different model
```

`bin/karaoke` automatically re-execs under `.venv/bin/python3`, so you never
have to `source .venv/bin/activate`.

The default model is `medium.en` (~1.5 GB download, one-time); it's a big
quality jump over `small.en` for song lyrics. Pass `--model small.en` for
faster iteration, or `--model large` (~2.9 GB) when you need the best
accuracy.

## Stems layout

The script expects files named like the ones Ableton Live exports in
`Samples/Processed/Stems`:

```
1-07 Dry Your Eyes (Concert Version) [2026-04-18 054729] (Vocals).aif
1-07 Dry Your Eyes (Concert Version) [2026-04-18 054729] (Bass).aif
1-07 Dry Your Eyes (Concert Version) [2026-04-18 054729] (Drums).aif
1-07 Dry Your Eyes (Concert Version) [2026-04-18 054729] (Others).aif
```

Detection keys on the `(Vocals)`, `(Bass)`, `(Drums)`, `(Others)` suffix
immediately before the file extension. `.wav`, `.flac`, `.m4a`, `.mp3` and
`.aiff` also work.

## Usage

```sh
# From the project root
./bin/karaoke "/Users/craig/Music/Ableton/Live Recordings/.../Stems"

# Faster iteration with a smaller model
./bin/karaoke /path/to/stems --model small.en

# Best accuracy (multilingual, ~2.9 GB)
./bin/karaoke /path/to/stems --model large

# Keep vocals quietly in the mix so the singer can follow the melody
./bin/karaoke /path/to/stems --with-vocals --vocals-db -14

# Save the intermediate mix / subtitles / background alongside the video
./bin/karaoke /path/to/stems --keep-intermediate

# Specific output path
./bin/karaoke /path/to/stems -o ~/Desktop/dry_your_eyes.mp4
```

## What it does under the hood

1. **Stems** — `src/karaoke/stems.py` finds the four stems and derives a clean
   song title from the vocal filename (stripping Ableton's timestamp).
2. **Mix** — `src/karaoke/mix.py` ffmpeg-mixes Bass + Drums + Others (plus
   attenuated Vocals if `--with-vocals`) into a 48 kHz stereo WAV.
3. **Transcribe** — `src/karaoke/transcribe.py` runs Whisper with
   `word_timestamps=True` on the vocal stem and groups words into short
   readable lines (line breaks on punctuation, gaps, or character budget).
4. **Subtitles** — `src/karaoke/subtitles.py` emits an ASS file with per-word
   `\kf` fills so the current syllable sweeps from cool white to neon magenta.
5. **Background** — `src/karaoke/background.py` uses Pillow to paint a 1920×1080
   synthwave still: gradient sky, glowing sun with horizontal slice cutouts,
   star field, perspective neon grid, horizon glow, scanlines.
6. **Render** — `src/karaoke/render.py` ffmpeg-encodes a looping-image video at
   30 fps with the ASS burned in via the `subtitles` filter; audio as AAC;
   `+faststart` for quick preview on the Mac.

## Tuning

- Whisper is trained on speech, not music. Default `--model medium.en`
  already handles most lyrics; bump to `--model large` (slowest, best) when
  vocals are mumbled or heavily effected.
- Line grouping lives in `group_into_lines()` inside
  `src/karaoke/transcribe.py` — tweak `max_chars`, `max_duration`, `max_gap`.
- Font / colours / margins live in the `ASS_HEADER_TEMPLATE` string in
  `src/karaoke/subtitles.py`. ASS colours are `&HAABBGGRR`.
- The synthwave palette lives at the top of `background.py`.
