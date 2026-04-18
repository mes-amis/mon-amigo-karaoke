"""Build an ASS (Advanced SubStation Alpha) karaoke file from lyric timings."""

from __future__ import annotations

import math
from pathlib import Path

from .transcribe import Line


# ASS colours are &HAABBGGRR (alpha-blue-green-red).
#   PrimaryColour   = sung / highlighted text (neon magenta)
#   SecondaryColour = unsung text           (soft white)
#   OutlineColour   = heavy dark-purple border for readability on bright bg
#   BackColour      = shadow (semi-transparent black)
ASS_HEADER_TEMPLATE = """[Script Info]
Title: {title}
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes
WrapStyle: 2
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Impact,104,&H00FF40FF,&H00F5F5F5,&H00200040,&H96000000,-1,0,0,0,100,100,1,0,1,5,3,2,80,80,150,1
Style: Title,Impact,120,&H0000E5FF,&H00FFFFFF,&H00200040,&H96000000,-1,0,0,0,100,100,2,0,1,6,4,7,80,1020,80,1
Style: Credit,Impact,64,&H00E5FFFF,&H00FFFFFF,&H00200040,&H96000000,-1,0,0,0,100,100,0,0,1,5,3,9,1020,80,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape(text: str) -> str:
    # libass control chars: backslash, braces, newline
    return (
        text.replace("\\", "")
            .replace("{", "(")
            .replace("}", ")")
            .replace("\n", " ")
    )


def _line_text(line: Line, event_start: float) -> str:
    tokens: list[str] = []

    lead_cs = int(round((line.start - event_start) * 100))
    if lead_cs > 0:
        # Invisible leading syllable — lets the line appear on screen
        # before the first word is highlighted.
        tokens.append(f"{{\\alpha&HFF&\\kf{lead_cs}}}.{{\\alpha&H00&}}")

    words = line.words
    for i, w in enumerate(words):
        if i < len(words) - 1:
            dur = words[i + 1].start - w.start
        else:
            dur = max(0.1, w.end - w.start)
        cs = max(1, int(round(dur * 100)))
        suffix = " " if i < len(words) - 1 else ""
        tokens.append(f"{{\\kf{cs}}}{_escape(w.text)}{suffix}")

    return "".join(tokens)


TITLE_BASE_SIZE = 120
TITLE_MIN_SIZE = 48
TITLE_AREA_WIDTH_PX = 820   # 1920 - MarginL(80) - MarginR(1020) from the Title style
TITLE_AREA_HEIGHT_PX = 260  # room for two wrapped lines before we bump the sun


def _fit_title_font_size(
    text: str,
    base_size: int = TITLE_BASE_SIZE,
    min_size: int = TITLE_MIN_SIZE,
    max_width_px: int = TITLE_AREA_WIDTH_PX,
    max_height_px: int = TITLE_AREA_HEIGHT_PX,
    char_width_ratio: float = 0.5,
    line_height_ratio: float = 1.25,
) -> int:
    """Largest font size where the wrapped title fits the top-left card area.

    Impact is a condensed display face, so ~0.5 × font_size is a fair
    rule-of-thumb character width (bold adds a hair but not enough to
    matter here). The search steps down in 4 pt increments and stops
    as soon as the estimated rendering fits in `max_width_px × max_height_px`.
    """
    if not text:
        return base_size
    for size in range(base_size, min_size - 1, -4):
        chars_per_line = max(1, int(max_width_px / (size * char_width_ratio)))
        num_lines = math.ceil(len(text) / chars_per_line)
        total_height = num_lines * size * line_height_ratio
        if total_height <= max_height_px:
            return size
    return min_size


def _title_dialogue_body(title: str) -> str:
    """Dialogue body for the Title (left side) card, with inline wrap + shrink."""
    fs = _fit_title_font_size(title)
    # \q0 = smart wrap for this event only (the file-wide WrapStyle is 2
    # so lyric lines stay on a single line each).
    prefix = f"{{\\q0\\fad(300,400)"
    if fs != TITLE_BASE_SIZE:
        prefix += f"\\fs{fs}"
    prefix += "}"
    return prefix + _escape(title)


def _credit_dialogue_body(artist: str, album: str) -> str | None:
    """Dialogue body for the Credit (right side) card — artist over album."""
    parts = [p for p in (artist.strip(), album.strip()) if p]
    if not parts:
        return None
    body = "{\\q0\\fad(300,400)}"
    # Artist on line 1 (regular weight), album on line 2 in italics so
    # the two stack clearly even when both are short.
    if len(parts) == 2:
        body += f"{_escape(parts[0])}\\N{{\\i1}}{_escape(parts[1])}"
    else:
        body += _escape(parts[0])
    return body


def build_ass(
    lines: list[Line],
    out: Path,
    title: str = "",
    artist: str = "",
    album: str = "",
    lead_in: float = 0.6,
    title_duration: float = 3.0,
    crossfade: float = 0.3,
    min_hold_after: float = 0.3,
    final_linger: float = 1.5,
) -> None:
    """Write an ASS karaoke file.

    Args:
        lead_in: seconds the line appears on screen before its first word
            is sung — the singer's reading-ahead preview.
        title_duration: minimum seconds to show the title card. The
            card normally stays up for the entire performance and fades
            out with the last lyric — this floor just keeps very short
            songs (or lyric-less renders) from flashing the title.
        crossfade: seconds of overlap between the outgoing line and the
            incoming line. During this window both lines are visible, with
            the outgoing one fading out while the incoming one fades in,
            so the singer never sees an empty screen between lines.
        min_hold_after: minimum seconds the line stays visible after its
            last syllable, even if the next line starts almost on top.
        final_linger: seconds the last line stays on screen before fading.
    """
    header = ASS_HEADER_TEMPLATE.format(title=_escape(title))
    events: list[str] = []

    if title:
        if lines:
            # The title sits at top-left and lyrics sit at bottom-center,
            # so there's no positional conflict — we keep the title up for
            # the whole performance and fade it out with the last lyric.
            # This gives the singer a persistent reference of what song
            # they're performing.
            title_end = max(title_duration, lines[-1].end + final_linger)
        else:
            title_end = title_duration

        if title_end > 0.2:
            # Title on the left (wraps around the sun), credits on the right.
            events.append(
                f"Dialogue: 0,{_ass_time(0)},{_ass_time(title_end)},Title,,0,0,0,,"
                f"{_title_dialogue_body(title)}"
            )
            credit = _credit_dialogue_body(artist, album)
            if credit:
                events.append(
                    f"Dialogue: 0,{_ass_time(0)},{_ass_time(title_end)},Credit,,0,0,0,,"
                    f"{credit}"
                )

    fade_out_ms = int(round(crossfade * 1000))

    for i, line in enumerate(lines):
        event_start = max(0.0, line.start - lead_in)

        if i + 1 < len(lines):
            # Stretch this event all the way to the next line's appearance
            # (plus a crossfade window), instead of cutting it at
            # line.end + min_hold_after. That dead gap is what made the
            # transitions feel abrupt.
            next_event_start = max(0.0, lines[i + 1].start - lead_in)
            event_end = next_event_start + crossfade
        else:
            event_end = line.end + final_linger

        # Never let the line vanish right after its last syllable — give
        # the singer a beat, even when the next line's event_start beats
        # us to it (close-together phrases).
        event_end = max(event_end, line.end + min_hold_after)

        if event_end <= event_start:
            event_end = event_start + 0.2

        text = _line_text(line, event_start)
        # Fade-out duration is matched to the crossfade window so the
        # outgoing line is ~fully transparent by the time the incoming
        # line has finished fading in.
        text = f"{{\\fad(200,{fade_out_ms})}}" + text
        events.append(
            f"Dialogue: 0,{_ass_time(event_start)},{_ass_time(event_end)},Karaoke,,0,0,0,,{text}"
        )

    out.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
