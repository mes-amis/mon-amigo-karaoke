"""Lyrics correction-pass alignment.

The Genius fetch is a thin wrapper around ``lyricsgenius`` and we don't
exercise it from tests (network + auth). What's worth pinning is the
``align_words`` algorithm: given Whisper word-timings + canonical lyric
text, does it produce the right text in the right place?
"""

from __future__ import annotations

import pytest

from karaoke.lyrics import _normalize, _tokenize, align_words
from karaoke.transcribe import Word


def w(text: str, start: float, end: float) -> Word:
    return Word(text=text, start=start, end=end)


# --- helpers --------------------------------------------------------


def test_normalize_strips_punctuation_and_lowercases() -> None:
    assert _normalize("Don't") == "dont"
    assert _normalize("Hello,") == "hello"
    assert _normalize("...") == ""


def test_tokenize_drops_blank_lines_and_section_headers() -> None:
    text = """[Chorus]

Hello world

Goodbye now"""
    assert _tokenize(text) == ["Hello", "world", "Goodbye", "now"]


# --- align_words: identity & happy paths ---------------------------


def test_align_words_identity_when_lyrics_match_exactly() -> None:
    words = [w("hello", 1.0, 1.4), w("world", 1.5, 1.9)]

    out = align_words(words, "Hello world")

    # Same count, same timings — but the casing is the Genius spelling now.
    assert [(o.text, o.start, o.end) for o in out] == [
        ("Hello", 1.0, 1.4), ("world", 1.5, 1.9),
    ]


def test_align_words_returns_input_when_lyrics_empty() -> None:
    words = [w("hello", 1.0, 1.4)]
    assert align_words(words, "") == words
    assert align_words(words, "   \n  ") == words


def test_align_words_returns_input_when_no_whisper_words() -> None:
    assert align_words([], "Hello world") == []


# --- corrections ---------------------------------------------------


def test_align_words_swaps_a_close_typo() -> None:
    """1:1 replace where the words are similar enough — typo fix.
    'sea' vs 'see' both normalize to 3 chars sharing 'se' — high enough
    similarity to assume the singer meant 'see'.
    """
    words = [w("I", 1.0, 1.2), w("sea", 1.3, 1.7), w("you", 1.8, 2.1)]

    out = align_words(words, "I see you")

    assert [o.text for o in out] == ["I", "see", "you"]
    # Timing on the corrected word is unchanged.
    assert out[1].start == 1.3 and out[1].end == 1.7


def test_align_words_keeps_whisper_when_replacement_is_dissimilar() -> None:
    """1:1 replace where the words are NOT similar — different word
    entirely. We keep Whisper to avoid changing what the singer sang.
    """
    words = [
        w("hello", 1.0, 1.4),
        w("kafka", 1.5, 1.9),       # totally unrelated to Genius's 'world'
    ]

    out = align_words(words, "hello world")

    assert [o.text for o in out] == ["hello", "kafka"]


def test_align_words_keeps_whisper_for_uneven_replacement() -> None:
    """N:M replacement (counts differ) is treated as live rephrasing
    rather than a typo. 'wanna' stays even though Genius writes
    'want to' — the singer probably said 'wanna'."""
    words = [
        w("I", 0.0, 0.4),
        w("wanna", 0.5, 1.5),
        w("dance", 1.6, 2.0),
    ]

    out = align_words(words, "I want to dance")

    assert [o.text for o in out] == ["I", "wanna", "dance"]
    # Timing untouched too.
    assert out[1].start == 0.5 and out[1].end == 1.5


def test_align_words_keeps_whisper_extras_for_live_adlibs() -> None:
    """Whisper picked up a word Genius doesn't have ('uh', 'yeah',
    crowd banter). KEEP it — live performances need this preserved."""
    words = [
        w("hello", 1.0, 1.4),
        w("yeah", 1.5, 1.7),       # not in Genius
        w("world", 1.8, 2.2),
    ]

    out = align_words(words, "hello world")

    assert [o.text for o in out] == ["hello", "yeah", "world"]


def test_align_words_does_not_fabricate_missed_genius_words() -> None:
    """Genius has a word Whisper didn't catch. SKIP — we won't add words
    the singer didn't perform (or that we have no timing for)."""
    words = [
        w("hello", 1.0, 1.4),
        w("world", 2.4, 2.8),
    ]

    out = align_words(words, "hello bright world")

    assert [o.text for o in out] == ["hello", "world"]
    # Original timings come through untouched.
    assert out[0].end == 1.4 and out[1].start == 2.4


def test_align_words_does_not_append_trailing_genius_word() -> None:
    """Last Whisper word is the last we render — no fabricated tail."""
    words = [w("hello", 1.0, 1.4)]
    out = align_words(words, "hello world")
    assert [o.text for o in out] == ["hello"]


def test_align_words_preserves_punctuation_from_genius() -> None:
    """Equal-block path: Whisper rarely has punctuation; Genius does.
    group_into_lines uses commas/periods as phrase boundaries, so it
    matters that we pick them up here."""
    words = [w("hello", 1.0, 1.4), w("world", 1.5, 1.9)]

    out = align_words(words, "Hello, world!")

    assert out[0].text == "Hello,"
    assert out[1].text == "world!"


def test_align_words_full_realistic_mix() -> None:
    """One of each kind of difference at once.

    - "dont" -> "don't" (1:1 close swap)
    - missing "what to do" (insert -> skipped)
    - extra "yeah" (delete -> kept)
    - "anymore" matches verbatim (equal)
    """
    words = [
        w("I", 0.0, 0.2),
        w("dont", 0.3, 0.6),
        w("know", 0.7, 1.0),
        w("anymore", 1.5, 2.0),
        w("yeah", 2.1, 2.3),       # Whisper extra; not in Genius
    ]
    canonical = "I don't know what to do anymore"

    out = align_words(words, canonical)

    assert [o.text for o in out] == [
        "I", "don't", "know", "anymore", "yeah",
    ]
    # Original timings preserved everywhere.
    assert out[1].start == 0.3 and out[1].end == 0.6
    assert out[3].start == 1.5 and out[3].end == 2.0
    assert out[4].start == 2.1 and out[4].end == 2.3