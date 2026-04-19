"""Genius-based correction pass for Whisper transcriptions.

Whisper hears the music well enough to time individual words, but it
frequently mishears them — homophones, mumbled lines, syllables eaten
by the band. Genius.com hosts the canonical lyrics for most songs, so
we fetch them, then re-align Whisper's word-level timestamps onto the
Genius word sequence:

  - "equal" runs keep Whisper's timing exactly,
  - "replace" runs swap text but redistribute the time bounds,
  - "delete" runs drop Whisper words Genius doesn't have,
  - "insert" runs add Genius words Whisper missed, interpolating their
    start/end between the surrounding timestamps.

The Genius API needs an access token. Set ``GENIUS_ACCESS_TOKEN`` in the
environment (preferably via 1Password / ``op``) to enable the feature;
without it we just return the input words unchanged.
"""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher

from .transcribe import Word


def _normalize(text: str) -> str:
    """Lowercase, drop punctuation — used only for sequence alignment."""
    return re.sub(r"[^\w]", "", text.lower())


def _tokenize(lyrics: str) -> list[str]:
    """Split lyric text into whitespace-delimited tokens, dropping empty
    lines, section markers like ``[Chorus]``, and (optional) annotation
    blocks like ``(x2)`` that lyricsgenius doesn't already strip."""
    cleaned: list[str] = []
    for line in lyrics.splitlines():
        line = line.strip()
        if not line:
            continue
        # Defensive: skip section headers if lyricsgenius didn't already.
        if line.startswith("[") and line.endswith("]"):
            continue
        cleaned.append(line)
    return " ".join(cleaned).split()


def fetch_lyrics(
    title: str,
    artist: str,
    *,
    token: str | None = None,
    timeout: float = 8.0,
) -> str | None:
    """Fetch the lyrics for ``title`` by ``artist`` from Genius.

    Returns ``None`` on any failure (no token, no match, network error,
    rate limit). Designed to be safe to call inside the karaoke pipeline
    — no exception ever bubbles up.
    """
    token = token or os.environ.get("GENIUS_ACCESS_TOKEN")
    if not token:
        return None

    try:
        import lyricsgenius  # type: ignore
    except ModuleNotFoundError:
        return None

    try:
        genius = lyricsgenius.Genius(
            token,
            remove_section_headers=True,
            skip_non_songs=True,
            timeout=int(timeout),
            retries=1,
        )
        # Older lyricsgenius versions had a noisy `verbose` flag; newer
        # ones removed the constructor kwarg but still respect the attr.
        if hasattr(genius, "verbose"):
            genius.verbose = False
        song = genius.search_song(title, artist)
    except Exception:
        return None

    if song is None:
        return None
    lyrics = (song.lyrics or "").strip()
    return lyrics or None


def align_words(
    whisper_words: list[Word],
    lyrics_text: str,
    *,
    fallback_word_seconds: float = 0.4,
) -> list[Word]:
    """Map Whisper's word-level timings onto the canonical Genius lyrics.

    Falls back to the input list unchanged if either side is empty.
    """
    if not whisper_words or not lyrics_text:
        return whisper_words

    genius_tokens = _tokenize(lyrics_text)
    if not genius_tokens:
        return whisper_words

    w_norm = [_normalize(w.text) for w in whisper_words]
    g_norm = [_normalize(t) for t in genius_tokens]

    matcher = SequenceMatcher(a=w_norm, b=g_norm, autojunk=False)
    out: list[Word] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                w = whisper_words[i1 + offset]
                # Use the Genius spelling/casing/punctuation, keep timing.
                out.append(Word(
                    text=genius_tokens[j1 + offset],
                    start=w.start,
                    end=w.end,
                ))

        elif tag == "replace":
            n_w = i2 - i1
            n_g = j2 - j1
            if n_w == 0 or n_g == 0:
                continue
            t_start = whisper_words[i1].start
            t_end = whisper_words[i2 - 1].end
            if n_w == n_g:
                # Same count — keep each Whisper word's individual timing.
                for k in range(n_g):
                    w = whisper_words[i1 + k]
                    out.append(Word(
                        text=genius_tokens[j1 + k],
                        start=w.start,
                        end=w.end,
                    ))
            else:
                # N:M — divide the total span evenly across the new words.
                duration = max(0.05, t_end - t_start)
                step = duration / n_g
                for k in range(n_g):
                    out.append(Word(
                        text=genius_tokens[j1 + k],
                        start=t_start + k * step,
                        end=t_start + (k + 1) * step,
                    ))

        elif tag == "delete":
            # Whisper had words Genius doesn't — most likely hallucinated
            # filler. Drop them; their time is absorbed by the gap.
            continue

        elif tag == "insert":
            # Genius has words Whisper missed. Interpolate between the
            # last emitted word and the next Whisper word.
            n_g = j2 - j1
            t_start = (
                out[-1].end if out
                else (whisper_words[i1 - 1].end if i1 > 0 else 0.0)
            )
            if i1 < len(whisper_words):
                t_end = whisper_words[i1].start
            else:
                t_end = t_start + n_g * fallback_word_seconds
            duration = max(0.05, t_end - t_start)
            step = duration / n_g
            for k in range(n_g):
                out.append(Word(
                    text=genius_tokens[j1 + k],
                    start=t_start + k * step,
                    end=t_start + (k + 1) * step,
                ))

    return out
