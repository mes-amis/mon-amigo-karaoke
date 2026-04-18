"""Stem detection and title parsing.

The real world input is an Ableton 'Samples/Processed/Stems' folder. We
don't want to ship real audio in the test suite, so we fake the folder by
creating empty files with the right names.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from karaoke.stems import find_stems, song_title


BASENAME = "1-07 Dry Your Eyes (Concert Version) [feat. Neil Diamond] [2026-04-18 054729]"


def _mkstems(folder: Path, parts: list[str], ext: str = ".aif") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for part in parts:
        (folder / f"{BASENAME} ({part}){ext}").touch()
        # Ableton always drops a sidecar .asd analysis file next to each stem.
        (folder / f"{BASENAME} ({part}){ext}.asd").touch()


def test_finds_all_four_stems(tmp_path: Path) -> None:
    _mkstems(tmp_path, ["Vocals", "Bass", "Drums", "Others"])

    stems = find_stems(tmp_path)

    assert set(stems.keys()) == {"Vocals", "Bass", "Drums", "Others"}
    for path in stems.values():
        assert path.suffix == ".aif"
        assert path.exists()


def test_ignores_asd_sidecars(tmp_path: Path) -> None:
    _mkstems(tmp_path, ["Vocals", "Bass"])
    # Extra .asd-only garbage should not cause confusion.
    (tmp_path / "random.asd").touch()

    stems = find_stems(tmp_path)

    for path in stems.values():
        assert not path.name.endswith(".asd")


def test_missing_vocals_is_an_error(tmp_path: Path) -> None:
    _mkstems(tmp_path, ["Bass", "Drums", "Others"])  # no Vocals

    with pytest.raises(ValueError, match="No .Vocals. stem"):
        find_stems(tmp_path)


def test_nonexistent_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        find_stems(tmp_path / "does-not-exist")


def test_duplicate_stem_raises(tmp_path: Path) -> None:
    _mkstems(tmp_path, ["Vocals", "Bass"])
    # Two files both ending in "(Vocals)" — a real (rare) mistake.
    (tmp_path / "Other Song (Vocals).aif").touch()

    with pytest.raises(ValueError, match="Multiple Vocals"):
        find_stems(tmp_path)


def test_accepts_wav_and_flac(tmp_path: Path) -> None:
    (tmp_path / "Song (Vocals).wav").touch()
    (tmp_path / "Song (Bass).flac").touch()

    stems = find_stems(tmp_path)

    assert stems["Vocals"].suffix == ".wav"
    assert stems["Bass"].suffix == ".flac"


def test_song_title_strips_track_number_and_timestamp(tmp_path: Path) -> None:
    _mkstems(tmp_path, ["Vocals"])

    stems = find_stems(tmp_path)

    assert song_title(stems) == "Dry Your Eyes (Concert Version) [feat. Neil Diamond]"


def test_song_title_on_plain_name(tmp_path: Path) -> None:
    (tmp_path / "My Song (Vocals).aif").touch()

    stems = find_stems(tmp_path)

    assert song_title(stems) == "My Song"
