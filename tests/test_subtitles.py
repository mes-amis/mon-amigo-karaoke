"""ASS karaoke subtitle generation.

The ASS format is finicky — bad timing or a rogue '{' can silently blank
out a whole line in libass — so these tests pin the shape of the output.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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


def test_title_card_splits_into_title_and_credit_events(tmp_path: Path) -> None:
    """When artist + album are present, the card emits two dialogues.

    The title sits in the left-side Title style and the artist/album
    sits in the right-side Credit style, so long titles don't collide
    with the credit text in the middle of the frame.
    """
    out = tmp_path / "lyrics.ass"
    build_ass(
        [_line(("a", 5.0, 5.5))],
        out,
        title="Doctor Worm",
        artist="They Might Be Giants",
        album="John Henry",
    )

    lines = out.read_text().splitlines()
    title_events = [l for l in lines if ",Title,," in l]
    credit_events = [l for l in lines if ",Credit,," in l]
    assert len(title_events) == 1
    assert len(credit_events) == 1

    title_body = title_events[0].split(",,", 1)[1]
    assert "Doctor Worm" in title_body

    credit_body = credit_events[0].split(",,", 1)[1]
    assert "They Might Be Giants" in credit_body
    assert "John Henry" in credit_body
    # Artist stacks above an italic album via \N + \i1.
    assert "\\N{\\i1}" in credit_body


def test_title_card_with_only_artist_omits_album_line(tmp_path: Path) -> None:
    out = tmp_path / "lyrics.ass"
    build_ass(
        [_line(("a", 5.0, 5.5))],
        out,
        title="Doctor Worm",
        artist="They Might Be Giants",
    )

    lines = out.read_text().splitlines()
    credit_body = [l for l in lines if ",Credit,," in l][0].split(",,", 1)[1]
    assert "They Might Be Giants" in credit_body
    # Only one credit line → no line break for an album.
    assert "\\N" not in credit_body


def test_title_card_without_credits_has_no_credit_event(tmp_path: Path) -> None:
    out = tmp_path / "lyrics.ass"
    build_ass([_line(("a", 5.0, 5.5))], out, title="Doctor Worm")

    lines = out.read_text().splitlines()
    assert [l for l in lines if ",Credit,," in l] == []


def test_long_title_gets_shrunk_font_override(tmp_path: Path) -> None:
    """A title far wider than the left-half area gets a smaller \\fs."""
    out = tmp_path / "lyrics.ass"
    long_title = "Dry Your Eyes (Concert Version) [feat. Neil Diamond]"
    build_ass([_line(("a", 5.0, 5.5))], out, title=long_title)

    title_body = [
        l for l in out.read_text().splitlines() if ",Title,," in l
    ][0].split(",,", 1)[1]
    match = re.search(r"\\fs(\d+)", title_body)
    assert match is not None, f"expected a \\fs override in long-title body: {title_body!r}"
    assert int(match.group(1)) < 120  # shrunk below the style default


def test_short_title_keeps_base_font(tmp_path: Path) -> None:
    """A short title fits at the default size — no \\fs override needed."""
    out = tmp_path / "lyrics.ass"
    build_ass([_line(("a", 5.0, 5.5))], out, title="Doctor Worm")

    title_body = [
        l for l in out.read_text().splitlines() if ",Title,," in l
    ][0].split(",,", 1)[1]
    assert "\\fs" not in title_body


def test_title_card_holds_through_long_instrumental_intro(tmp_path: Path) -> None:
    """Long intros keep the title on screen right up to the first lyric.

    Previously the title card ended at ``min(title_duration, first_lyric
    - 0.2)`` which meant it vanished 3 s in even for songs with a 15 s
    intro. The fix: hold until the first lyric's event appears.
    """
    out = tmp_path / "lyrics.ass"
    build_ass(
        [_line(("a", 15.0, 15.4))],
        out,
        title="Doctor Worm",
        lead_in=0.6,
        title_duration=3.0,
        crossfade=0.3,
    )

    title_event = [l for l in out.read_text().splitlines() if ",Title,," in l][0]
    end_time = _ass_time_to_seconds(title_event.split(",")[2])
    # First lyric event appears at 15 - 0.6 = 14.4s; title extends to 14.4 + 0.3.
    assert end_time == pytest.approx(14.7, abs=0.05)


def test_title_card_respects_min_duration_when_intro_is_short(tmp_path: Path) -> None:
    """If the intro is shorter than title_duration the card still gets its floor."""
    out = tmp_path / "lyrics.ass"
    build_ass(
        [_line(("a", 1.0, 1.5))],
        out,
        title="Doctor Worm",
        lead_in=0.6,
        title_duration=3.0,
    )

    title_event = [l for l in out.read_text().splitlines() if ",Title,," in l][0]
    end_time = _ass_time_to_seconds(title_event.split(",")[2])
    assert end_time >= 3.0


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


def _ass_time_to_seconds(t: str) -> float:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def test_lines_stay_visible_until_the_next_line_appears(tmp_path: Path) -> None:
    """No dark gap between consecutive karaoke lines.

    Before the crossfade change, the outgoing line vanished at
    ``line.end + 0.3s`` and the singer then stared at empty space until
    the next line appeared. Now the outgoing event is stretched until
    the incoming line's event_start, with a small overlap so the two
    crossfade instead of cutting.
    """
    # 3-second instrumental gap between two lines — this is where the
    # abrupt cut used to happen.
    lines = [
        _line(("a", 1.0, 1.5)),
        _line(("b", 5.0, 5.5)),
    ]
    out = tmp_path / "lyrics.ass"

    build_ass(lines, out, title="", lead_in=0.6, crossfade=0.3)

    dialogues = [
        l for l in out.read_text().splitlines() if l.startswith("Dialogue:") and ",Karaoke," in l
    ]
    first_end = _ass_time_to_seconds(dialogues[0].split(",")[2])
    second_start = _ass_time_to_seconds(dialogues[1].split(",")[1])
    # Line 1 stays on screen into Line 2's appearance — that's the
    # crossfade, not a bug.
    assert first_end > second_start
    # But it doesn't linger ridiculously past the crossfade window.
    assert first_end - second_start <= 0.35


def test_min_hold_after_last_syllable_is_respected(tmp_path: Path) -> None:
    """When lines are nearly back-to-back, min_hold_after still applies."""
    lines = [
        _line(("first", 1.0, 1.4)),
        _line(("second", 1.5, 1.9)),  # 0.1s gap — next event_start
                                       # would normally precede this end
    ]
    out = tmp_path / "lyrics.ass"

    build_ass(lines, out, title="", lead_in=0.6, min_hold_after=0.3)

    dialogues = [
        l for l in out.read_text().splitlines() if l.startswith("Dialogue:") and ",Karaoke," in l
    ]
    first_end = _ass_time_to_seconds(dialogues[0].split(",")[2])
    # line.end was 1.4; floor says event_end >= 1.7
    assert first_end >= 1.7 - 1e-3
