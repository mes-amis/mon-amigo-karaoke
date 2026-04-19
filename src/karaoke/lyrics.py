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
    similarity_threshold: float = 0.6,
) -> list[Word]:
    """Apply Genius as a *copy-edit* pass over Whisper's transcription.

    The earlier wholesale-replacement version pushed Whisper's text
    fully onto the Genius lyric, which broke live performances —
    ad-libs got dropped, repeated choruses lost their repetitions,
    and rephrased lines were forced back to the studio version.

    This version is gentler:

    - **equal** runs swap to Genius's spelling/casing/punctuation
      (e.g. ``dont`` -> ``don't``), keeping Whisper's exact timing;
    - **replace** runs of equal length swap each pair only when the
      two words are *phonetically close* (sequence-similarity above
      ``similarity_threshold``) — that's a typo fix
      (``sea`` -> ``see``), not a meaning change;
    - **replace** runs of unequal length are kept as Whisper heard
      them (``wanna`` stays ``wanna``, even if Genius wrote
      ``want to``);
    - **delete** runs (Whisper words Genius doesn't have) are kept
      verbatim — these are usually live banter, ad-libs, or repeats;
    - **insert** runs (Genius words Whisper didn't pick up) are
      *skipped* — we don't fabricate words the singer didn't sing.

    Returns the input unchanged when either side is empty.
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
            for k in range(i2 - i1):
                w = whisper_words[i1 + k]
                out.append(Word(
                    text=genius_tokens[j1 + k],
                    start=w.start,
                    end=w.end,
                ))

        elif tag == "replace":
            n_w = i2 - i1
            n_g = j2 - j1
            if n_w == n_g:
                # 1:1 — swap when the pair looks like a typo, keep
                # otherwise. Threshold deliberately on the conservative
                # side so unrelated word swaps are left alone.
                for k in range(n_w):
                    w = whisper_words[i1 + k]
                    similarity = SequenceMatcher(
                        None, w_norm[i1 + k], g_norm[j1 + k],
                    ).ratio()
                    if similarity >= similarity_threshold:
                        out.append(Word(
                            text=genius_tokens[j1 + k],
                            start=w.start,
                            end=w.end,
                        ))
                    else:
                        out.append(w)
            else:
                # N:M — likely a genuine wording difference (live
                # rephrasing, contractions, dropped/added syllables).
                # Keep Whisper's words verbatim.
                for k in range(n_w):
                    out.append(whisper_words[i1 + k])

        elif tag == "delete":
            # Whisper sang words Genius doesn't list. KEEP them —
            # live performances are full of "alright!", "here we go!",
            # repeated choruses, etc. that should still appear in the
            # karaoke.
            for k in range(i2 - i1):
                out.append(whisper_words[i1 + k])

        elif tag == "insert":
            # Genius has words Whisper didn't catch. SKIP — we can't
            # know if the singer actually performed them.
            continue

    return out
