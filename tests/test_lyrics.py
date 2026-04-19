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


def test_align_words_corrects_a_misheard_word() -> None:
    """Whisper heard 'sea' but the lyric is 'see'."""
    words = [w("I", 1.0, 1.2), w("sea", 1.3, 1.7), w("you", 1.8, 2.1)]

    out = align_words(words, "I see you")

    assert [o.text for o in out] == ["I", "see", "you"]
    # Timing for the corrected word matches the original's timing.
    assert out[1].start == 1.3 and out[1].end == 1.7


def test_align_words_handles_n_to_m_replacement_by_dividing_time() -> None:
    """Whisper said 'wanna' (1 word); Genius says 'want to' (2)."""
    words = [
        w("I", 0.0, 0.4),
        w("wanna", 0.5, 1.5),  # 1.0s span will be split across two Genius words
        w("dance", 1.6, 2.0),
    ]

    out = align_words(words, "I want to dance")

    assert [o.text for o in out] == ["I", "want", "to", "dance"]
    # The 'wanna' span 0.5-1.5 splits in half: 0.5-1.0 and 1.0-1.5.
    assert out[1].start == pytest.approx(0.5, abs=1e-6)
    assert out[1].end == pytest.approx(1.0, abs=1e-6)
    assert out[2].start == pytest.approx(1.0, abs=1e-6)
    assert out[2].end == pytest.approx(1.5, abs=1e-6)


def test_align_words_drops_whisper_hallucinations() -> None:
    """Whisper inserted a phantom 'uh' that isn't in the lyric."""
    words = [
        w("hello", 1.0, 1.4),
        w("uh", 1.5, 1.7),     # not in Genius
        w("world", 1.8, 2.2),
    ]

    out = align_words(words, "hello world")

    assert [o.text for o in out] == ["hello", "world"]


def test_align_words_inserts_missed_genius_words_with_interpolated_time() -> None:
    """Whisper missed a word in the middle. Insert it between neighbors."""
    words = [
        w("hello", 1.0, 1.4),
        w("world", 2.4, 2.8),  # 1.0s gap where the missed word lives
    ]

    out = align_words(words, "hello bright world")

    assert [o.text for o in out] == ["hello", "bright", "world"]
    bright = out[1]
    # Bright is interpolated between hello.end (1.4) and world.start (2.4).
    assert bright.start == pytest.approx(1.4, abs=1e-6)
    assert bright.end == pytest.approx(2.4, abs=1e-6)


def test_align_words_inserts_missed_word_at_the_end() -> None:
    """Whisper cut off before the last word."""
    words = [w("hello", 1.0, 1.4)]
    out = align_words(words, "hello world", fallback_word_seconds=0.5)
    assert [o.text for o in out] == ["hello", "world"]
    # The trailing word gets fallback_word_seconds of duration starting
    # right after the last Whisper word.
    assert out[1].start == pytest.approx(1.4, abs=1e-6)
    assert out[1].end == pytest.approx(1.9, abs=1e-6)


def test_align_words_preserves_punctuation_from_genius() -> None:
    """Whisper rarely has punctuation; Genius does — and we want to keep it
    (downstream group_into_lines uses it as a phrase boundary)."""
    words = [w("hello", 1.0, 1.4), w("world", 1.5, 1.9)]

    out = align_words(words, "Hello, world!")

    assert out[0].text == "Hello,"
    assert out[1].text == "world!"


def test_align_words_combined_replace_and_insert(monkeypatch=None) -> None:
    """A more realistic mix: typo + missed word."""
    words = [
        w("I", 0.0, 0.2),
        w("dont", 0.3, 0.6),
        w("know", 0.7, 1.0),
        w("anymore", 1.5, 2.0),  # Whisper missed "what to do"
    ]
    canonical = "I don't know what to do anymore"

    out = align_words(words, canonical)

    assert [o.text for o in out] == [
        "I", "don't", "know", "what", "to", "do", "anymore",
    ]
    # The Whisper words that did match keep their original timing.
    assert out[0].start == 0.0 and out[2].end == 1.0
    # The inserted "what to do" sits between know.end (1.0) and
    # anymore.start (1.5).
    inserted = out[3:6]
    assert all(1.0 <= word.start < word.end <= 1.5 for word in inserted)