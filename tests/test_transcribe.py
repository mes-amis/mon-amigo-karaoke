"""Line-grouping logic for Whisper word timings.

Whisper itself is exercised in integration (it's slow and requires model
weights), but ``group_into_lines`` is pure and is where almost all the
subtitle readability decisions live, so we test it thoroughly.
"""

from __future__ import annotations

from karaoke.transcribe import Line, Word, group_into_lines


def w(text: str, start: float, end: float) -> Word:
    return Word(text=text, start=start, end=end)


def test_single_line_when_words_are_close(tmp_path) -> None:
    words = [w("hello", 0.0, 0.4), w("world", 0.5, 0.9)]

    lines = group_into_lines(words)

    assert len(lines) == 1
    assert lines[0].text == "hello world"
    assert lines[0].start == 0.0
    assert lines[0].end == 0.9


def test_breaks_on_large_gap() -> None:
    # Gap of 1.5s between "one" and "two" exceeds default max_gap=1.0
    words = [w("one", 0.0, 0.3), w("two", 1.8, 2.1)]

    lines = group_into_lines(words)

    assert [l.text for l in lines] == ["one", "two"]


def test_breaks_after_sentence_end() -> None:
    # '.', '!' or '?' always close a line in karaoke, regardless of gap.
    words = [w("done.", 0.0, 0.4), w("next", 0.45, 0.8)]

    lines = group_into_lines(words)

    assert [l.text for l in lines] == ["done.", "next"]


def test_breaks_on_comma_even_with_no_gap() -> None:
    # Singers breathe on commas — they must close a karaoke line even when
    # the next word comes in immediately after.
    words = [
        w("hello,", 0.0, 0.4),
        w("world", 0.4, 0.8),   # zero gap
    ]

    lines = group_into_lines(words)

    assert [l.text for l in lines] == ["hello,", "world"]


def test_breaks_on_semicolon_and_colon() -> None:
    words = [
        w("stop;", 0.0, 0.3),
        w("listen:", 0.35, 0.7),
        w("now", 0.75, 1.0),
    ]

    lines = group_into_lines(words)

    assert [l.text for l in lines] == ["stop;", "listen:", "now"]


def test_does_not_break_inside_sentence_with_tiny_gap() -> None:
    words = [
        w("i", 0.0, 0.1),
        w("am", 0.15, 0.25),
        w("here", 0.3, 0.5),
    ]

    lines = group_into_lines(words)

    assert len(lines) == 1
    assert lines[0].text == "i am here"


def test_breaks_on_character_budget() -> None:
    # max_chars=10 makes this overflow after "twelve" -> new line before "thirteen"
    words = [
        w("twelve", 0.0, 0.3),
        w("thirteen", 0.35, 0.7),
    ]

    lines = group_into_lines(words, max_chars=10)

    assert [l.text for l in lines] == ["twelve", "thirteen"]


def test_breaks_on_duration_budget() -> None:
    # max_duration=1.0 is exceeded before "third" joins the first line.
    words = [
        w("first", 0.0, 0.3),
        w("second", 0.6, 0.9),
        w("third", 1.2, 1.5),
    ]

    lines = group_into_lines(words, max_duration=1.0)

    assert len(lines) >= 2
    assert lines[0].text == "first second"


def test_empty_input() -> None:
    assert group_into_lines([]) == []
