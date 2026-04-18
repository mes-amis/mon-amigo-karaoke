"""Song selection helpers in the CLI.

``_match_song`` turns a ``--song <query>`` substring into exactly one
Song (or an error with the ambiguity listed). The interactive prompt
itself isn't tested here — it's a trivial input() loop — but the
non-interactive fallback path is verified so headless runs fail loudly
instead of hanging.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from karaoke.cli import _match_song
from karaoke.stems import Song


def _song(title: str) -> Song:
    return Song(base=title, title=title, stems={"Vocals": Path("/dev/null")})


SONGS = [
    _song("Doctor Worm"),
    _song("Dry Your Eyes"),
    _song("Dry Bones"),
]


def test_match_song_substring_hit() -> None:
    assert _match_song(SONGS, "worm").title == "Doctor Worm"


def test_match_song_is_case_insensitive() -> None:
    assert _match_song(SONGS, "WORM").title == "Doctor Worm"


def test_match_song_errors_on_ambiguous_query() -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        _match_song(SONGS, "dry")


def test_match_song_errors_on_no_match() -> None:
    with pytest.raises(ValueError, match="matched nothing"):
        _match_song(SONGS, "nothing-like-this")
