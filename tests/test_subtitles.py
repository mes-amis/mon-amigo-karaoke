"""ASS karaoke subtitle generation.

The ASS format is finicky — bad timing or a rogue '{' can silently blank
out a whole line in libass — so these tests pin the shape of the output.
"""

from __future__ import annotations

import re
from pathlib import Path

from karaoke.subtitles import _ass_time, _escape, build_ass
from karaoke.transcribe import Line, Word


def _line(*words: tuple[str, float, float]) -> Line:
    return Line(words=[Word(text=t, start=s, end=e) for t, s, e in words])


def test_ass_time_formats_hours_minutes_centiseconds() -> None:
    assert _ass_time(0.0) == "0:00:00.00"
    assert _ass_time(65.5) == "0:01:05.50"
    assert _ass_time(3661.23) == "1:01:01.23"


def test_ass_time_clamps_negative_to_zero() -> None:
    # Event-starts with lead-in could go negative near t=0; the helper must
    # clamp to avoid libass parse errors.
    assert _ass_time(-0.8) == "0:00:00.00"


def test_escape_neutralises_libass_control_chars() -> None:
    # Curly braces start an override block — one stray '{' in lyrics and
    # everything up to the next '}' disappears. Replace them.
    assert _escape("two {curly} braces") == "two (curly) braces"
    # Backslashes start override tags.
    assert _escape("back\\slash") == "backslash"
    # Newlines are flattened to spaces.
    assert _escape("line\nbreak") == "line break"


def test_build_ass_writes_header_and_one_event_per_line(tmp_path: Path) -> None:
    lines = [
        _line(("hello", 1.0, 1.4), ("world", 1.5, 1.9)),
        _line(("second", 3.0, 3.4), ("line", 3.5, 3.8)),
    ]
    out = tmp_path / "lyrics.ass"

    build_ass(lines, out, title="My Song")

    text = out.read_text()
    assert "[Script Info]" in text
    assert "[V4+ Styles]" in text
    assert "[Events]" in text

    dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
    # One title card + one event per lyric line
    assert len(dialogues) == 1 + len(lines)

    karaoke_events = [d for d in dialogues if ",Karaoke," in d]
    assert len(karaoke_events) == len(lines)


def test_each_lyric_line_has_one_kf_per_word(tmp_path: Path) -> None:
    lines = [_line(("one", 1.0, 1.3), ("two", 1.4, 1.7), ("three", 1.8, 2.1))]
    out = tmp_path / "lyrics.ass"

    build_ass(lines, out, title="")

    ev = [l for l in out.read_text().splitlines() if ",Karaoke," in l][0]
    # Count \kf tags. There should be one per word, plus potentially one
    # invisible lead-in token. So at least `len(words)`.
    kf_count = len(re.findall(r"\\kf\d+", ev))
    assert kf_count >= 3


def test_events_are_chronologically_non_overlapping(tmp_path: Path) -> None:
    lines = [
        _line(("a", 1.0, 1.2), ("b", 1.3, 1.5)),
        _line(("c", 2.0, 2.2), ("d", 2.3, 2.5)),
    ]
    out = tmp_path / "lyrics.ass"

    build_ass(lines, out, title="")

    dialogues = [
        l for l in out.read_text().splitlines() if l.startswith("Dialogue:") and ",Karaoke," in l
    ]
    # The event-end of line 1 must not exceed the event-start of line 2.
    first_end = dialogues[0].split(",")[2]
    second_start = dialogues[1].split(",")[1]
    assert first_end <= second_start, (
        f"karaoke lines overlap: line1 ends {first_end}, line2 starts {second_start}"
    )
