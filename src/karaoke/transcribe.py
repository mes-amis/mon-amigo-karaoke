"""Transcribe a vocals stem into word-timestamped lyrics via local Whisper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Line:
    words: list[Word]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def _enable_system_trust_store() -> None:
    """Make urllib (used by whisper.load_model) honour the macOS Keychain.

    Corporate TLS proxies (Zscaler, Netskope, company firewalls) inject a
    self-signed root CA. The system keychain trusts it; Python's bundled
    ssl module does not. ``truststore`` bridges the two so model downloads
    succeed. No-op if the package isn't installed.
    """
    try:
        import truststore  # type: ignore
    except ModuleNotFoundError:
        return
    try:
        truststore.inject_into_ssl()
    except Exception:
        # truststore refuses on unsupported platforms; fall back silently.
        pass


def transcribe(
    vocals: Path,
    model_name: str = "small.en",
    language: str | None = None,
) -> list[Word]:
    _enable_system_trust_store()
    import whisper  # type: ignore

    model = whisper.load_model(model_name)
    result = model.transcribe(
        str(vocals),
        word_timestamps=True,
        language=language,
        condition_on_previous_text=False,
        initial_prompt="A song with lyrics.",
    )

    words: list[Word] = []
    for seg in result["segments"]:
        for w in seg.get("words", []) or []:
            text = str(w.get("word", "")).strip()
            if not text:
                continue
            start = float(w.get("start", seg["start"]))
            end = float(w.get("end", seg["end"]))
            if end <= start:
                end = start + 0.05
            words.append(Word(text=text, start=start, end=end))
    return words


# Any of these at the end of a word closes a phrase in karaoke — commas
# matter as much as periods because singers breathe on them.
PHRASE_END = (".", "!", "?", ",", ";", ":", "—", "–")


def group_into_lines(
    words: list[Word],
    max_chars: int = 42,
    max_duration: float = 5.0,
    max_gap: float = 1.0,
) -> list[Line]:
    lines: list[list[Word]] = []
    cur: list[Word] = []
    cur_chars = 0

    for w in words:
        if cur:
            gap = w.start - cur[-1].end
            duration = w.end - cur[0].start
            ends_phrase = cur[-1].text.endswith(PHRASE_END)
            too_wide = cur_chars + 1 + len(w.text) > max_chars
            too_long = duration > max_duration

            if ends_phrase or gap > max_gap or too_wide or too_long:
                lines.append(cur)
                cur = []
                cur_chars = 0

        cur.append(w)
        cur_chars += len(w.text) + (1 if cur_chars else 0)

    if cur:
        lines.append(cur)

    return [Line(words=ws) for ws in lines]
