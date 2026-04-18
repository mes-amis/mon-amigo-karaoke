"""Stem detection, multi-song grouping, and title parsing.

The real world input is an Ableton 'Samples/Processed/Stems' folder that
may contain several songs' worth of stems. We don't want to ship real
audio in the test suite, so we fake the folder by creating empty files
with the right names.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from karaoke.stems import Song, find_songs


DRY = "1-07 Dry Your Eyes (Concert Version) [feat. Neil Diamond] [2026-04-18 054729]"
WORM = "1-03 Doctor Worm [2026-04-19 101112]"


def _mkstems(folder: Path, base: str, parts: list[str], ext: str = ".aif") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for part in parts:
        (folder / f"{base} ({part}){ext}").touch()
        # Ableton always drops a sidecar .asd analysis file next to each stem.
        (folder / f"{base} ({part}){ext}.asd").touch()


def test_finds_single_song_with_all_four_stems(tmp_path: Path) -> None:
    _mkstems(tmp_path, DRY, ["Vocals", "Bass", "Drums", "Others"])

    songs = find_songs(tmp_path)

    assert len(songs) == 1
    song = songs[0]
    assert isinstance(song, Song)
    assert set(song.stems.keys()) == {"Vocals", "Bass", "Drums", "Others"}
    for path in song.stems.values():
        assert path.exists()


def test_finds_two_songs_in_one_folder(tmp_path: Path) -> None:
    _mkstems(tmp_path, DRY, ["Vocals", "Bass", "Drums", "Others"])
    _mkstems(tmp_path, WORM, ["Vocals", "Bass", "Drums", "Others"])

    songs = find_songs(tmp_path)

    titles = [s.title for s in songs]
    # Sorted alphabetically so the prompt is stable.
    assert titles == [
        "Doctor Worm",
        "Dry Your Eyes (Concert Version) [feat. Neil Diamond]",
    ]
    # Each song's stems point at its own files — no cross-contamination.
    for song in songs:
        assert all(song.base in p.name for p in song.stems.values())


def test_skips_song_missing_vocals(tmp_path: Path) -> None:
    _mkstems(tmp_path, DRY, ["Vocals", "Bass"])            # complete enough
    _mkstems(tmp_path, WORM, ["Bass", "Drums", "Others"])  # no vocals → skipped

    songs = find_songs(tmp_path)

    assert [s.title for s in songs] == [
        "Dry Your Eyes (Concert Version) [feat. Neil Diamond]",
    ]


def test_ignores_asd_sidecars(tmp_path: Path) -> None:
    _mkstems(tmp_path, DRY, ["Vocals", "Bass"])
    (tmp_path / "random.asd").touch()  # stray garbage

    songs = find_songs(tmp_path)

    for path in songs[0].stems.values():
        assert not path.name.endswith(".asd")


def test_empty_folder_returns_empty_list(tmp_path: Path) -> None:
    assert find_songs(tmp_path) == []


def test_nonexistent_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        find_songs(tmp_path / "does-not-exist")


def test_duplicate_stem_for_one_song_raises(tmp_path: Path) -> None:
    _mkstems(tmp_path, DRY, ["Vocals"])
    # A second Vocals file with the *same* base — a real (rare) export mistake.
    (tmp_path / f"{DRY} (Vocals).wav").touch()

    with pytest.raises(ValueError, match="Duplicate Vocals"):
        find_songs(tmp_path)


def test_accepts_wav_and_flac(tmp_path: Path) -> None:
    (tmp_path / "My Song (Vocals).wav").touch()
    (tmp_path / "My Song (Bass).flac").touch()

    songs = find_songs(tmp_path)

    assert songs[0].stems["Vocals"].suffix == ".wav"
    assert songs[0].stems["Bass"].suffix == ".flac"


def test_title_strips_track_number_and_timestamp(tmp_path: Path) -> None:
    _mkstems(tmp_path, DRY, ["Vocals"])

    songs = find_songs(tmp_path)

    assert songs[0].title == "Dry Your Eyes (Concert Version) [feat. Neil Diamond]"


def test_title_on_plain_name(tmp_path: Path) -> None:
    (tmp_path / "My Song (Vocals).aif").touch()

    songs = find_songs(tmp_path)

    assert songs[0].title == "My Song"
