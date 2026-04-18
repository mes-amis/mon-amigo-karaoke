"""Build an ASS (Advanced SubStation Alpha) karaoke file from lyric timings."""

from __future__ import annotations

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
Style: Karaoke,Impact,104,&H00FF40FF,&H00F5F5F5,&H00200040,&H96000000,-1,0,0,0,100,100,1,0,1,5,3,2,80,80,350,1
Style: Title,Impact,140,&H0000E5FF,&H00FFFFFF,&H00200040,&H96000000,-1,0,0,0,100,100,2,0,1,6,4,8,0,0,80,1

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


def build_ass(
    lines: list[Line],
    out: Path,
    title: str = "",
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
        title_end = title_duration
        if lines:
            title_end = min(title_end, max(0.1, lines[0].start - 0.2))
        if title_end > 0.2:
            events.append(
                f"Dialogue: 0,{_ass_time(0)},{_ass_time(title_end)},Title,,0,0,0,,"
                f"{{\\fad(300,400)}}{_escape(title)}"
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
