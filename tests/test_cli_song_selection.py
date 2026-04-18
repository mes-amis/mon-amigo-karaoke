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

from karaoke.cli import DEFAULT_OUTPUT_DIR, _match_song, _output_path_for
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


def test_output_path_default_single_song() -> None:
    song = _song("Doctor Worm")
    path = _output_path_for(song, output_arg=None, all_mode=False)
    assert path == (DEFAULT_OUTPUT_DIR / "Doctor Worm.mp4").resolve()


def test_output_path_single_song_with_explicit_file(tmp_path: Path) -> None:
    song = _song("Doctor Worm")
    target = tmp_path / "custom.mp4"
    path = _output_path_for(song, output_arg=target, all_mode=False)
    # In single-song mode --output is treated as the exact file path.
    assert path == target.resolve()


def test_output_path_all_mode_treats_output_as_directory(tmp_path: Path) -> None:
    song = _song("Doctor Worm")
    path = _output_path_for(song, output_arg=tmp_path, all_mode=True)
    # In --all mode --output is a directory: filename is derived per song.
    assert path == (tmp_path / "Doctor Worm.mp4").resolve()


def test_output_path_sanitises_song_title() -> None:
    # Slashes in a title would otherwise produce a directory traversal.
    song = _song("AC/DC Thunder")
    path = _output_path_for(song, output_arg=None, all_mode=False)
    assert path.name == "AC_DC Thunder.mp4"
