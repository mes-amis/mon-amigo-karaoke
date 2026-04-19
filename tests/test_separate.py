"""Demucs auto-stem path.

We don't actually shell out to Demucs in unit tests — the runner is
injectable so we can:

  - exercise the cache-hit path by pre-creating expected output files,
  - exercise the cache-miss path by stubbing the runner and watching
    what it gets called with.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from karaoke.separate import (
    DEFAULT_MODEL,
    DEMUCS_STEM_MAP,
    _cache_key,
    _expected_stem_paths,
    is_audio_file,
    separate,
)


def _touch_audio(path: Path) -> Path:
    path.write_bytes(b"\x00" * 16)
    return path


def _write_demucs_outputs(cache_root: Path, model: str, audio: Path) -> None:
    """Pretend a previous Demucs run cached its 6 stems here."""
    paths = _expected_stem_paths(cache_root, model, audio)
    for p in paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00" * 16)


def test_is_audio_file_recognises_common_extensions(tmp_path: Path) -> None:
    assert is_audio_file(_touch_audio(tmp_path / "song.mp3"))
    assert is_audio_file(_touch_audio(tmp_path / "song.WAV"))
    assert is_audio_file(_touch_audio(tmp_path / "song.flac"))
    assert not is_audio_file(_touch_audio(tmp_path / "notes.txt"))
    assert not is_audio_file(tmp_path)  # directory, not a file


def test_separate_returns_song_when_cache_already_populated(tmp_path: Path) -> None:
    audio = _touch_audio(tmp_path / "Doctor Worm.mp3")
    cache = tmp_path / "cache"
    cache_root = cache / _cache_key(audio.resolve(), DEFAULT_MODEL)
    _write_demucs_outputs(cache_root, DEFAULT_MODEL, audio)

    def fail_runner(_cmd: list[str]) -> None:
        pytest.fail("runner should not be called when cache is hit")

    song = separate(audio, cache_dir=cache, runner=fail_runner)

    assert set(song.stems.keys()) == set(DEMUCS_STEM_MAP.values())
    for path in song.stems.values():
        assert path.exists()
    # Vocals must be one of the keys — the rest of the pipeline depends on it.
    assert "Vocals" in song.stems


def test_separate_invokes_runner_on_cache_miss(tmp_path: Path) -> None:
    audio = _touch_audio(tmp_path / "Doctor Worm.mp3")
    cache = tmp_path / "cache"
    captured: list[list[str]] = []

    def fake_runner(cmd: list[str]) -> None:
        captured.append(cmd)
        # Pretend Demucs did its job by writing the expected outputs.
        cache_root = cache / _cache_key(audio.resolve(), DEFAULT_MODEL)
        _write_demucs_outputs(cache_root, DEFAULT_MODEL, audio)

    song = separate(audio, cache_dir=cache, runner=fake_runner)

    assert len(captured) == 1
    cmd = captured[0]
    # Invoked as `<venv-python> -m demucs ...` rather than the bare
    # `demucs` script, so we don't depend on PATH.
    import sys as _sys
    assert cmd[0] == _sys.executable
    assert cmd[1:3] == ["-m", "demucs"]
    assert "-n" in cmd and DEFAULT_MODEL in cmd
    assert str(audio.resolve()) in cmd
    assert song.title == "Doctor Worm"


def test_separate_strips_track_number_from_title(tmp_path: Path) -> None:
    audio = _touch_audio(tmp_path / "01 Doctor Worm.mp3")
    cache = tmp_path / "cache"
    cache_root = cache / _cache_key(audio.resolve(), DEFAULT_MODEL)
    _write_demucs_outputs(cache_root, DEFAULT_MODEL, audio)

    song = separate(audio, cache_dir=cache, runner=lambda _c: None)

    assert song.title == "Doctor Worm"


def test_separate_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        separate(tmp_path / "does-not-exist.mp3", cache_dir=tmp_path / "cache")


def test_separate_raises_on_unsupported_extension(tmp_path: Path) -> None:
    p = _touch_audio(tmp_path / "notes.txt")
    with pytest.raises(ValueError, match="unsupported"):
        separate(p, cache_dir=tmp_path / "cache")


def test_separate_errors_when_runner_does_not_produce_outputs(tmp_path: Path) -> None:
    audio = _touch_audio(tmp_path / "Doctor Worm.mp3")
    cache = tmp_path / "cache"

    # Runner that "succeeds" but writes nothing — the wrapper must catch
    # this rather than silently returning a Song with phantom paths.
    with pytest.raises(RuntimeError, match="missing"):
        separate(audio, cache_dir=cache, runner=lambda _c: None)


def test_cache_key_is_stable_for_same_file(tmp_path: Path) -> None:
    audio = _touch_audio(tmp_path / "song.mp3")
    k1 = _cache_key(audio.resolve(), DEFAULT_MODEL)
    k2 = _cache_key(audio.resolve(), DEFAULT_MODEL)
    assert k1 == k2


def test_cache_key_changes_when_model_changes(tmp_path: Path) -> None:
    audio = _touch_audio(tmp_path / "song.mp3")
    assert _cache_key(audio.resolve(), "htdemucs") != _cache_key(audio.resolve(), "htdemucs_6s")


def test_cache_key_changes_when_file_content_changes(tmp_path: Path) -> None:
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"\x00" * 16)
    k1 = _cache_key(audio.resolve(), DEFAULT_MODEL)
    audio.write_bytes(b"\x01" * 16)  # modified — mtime + size differ
    k2 = _cache_key(audio.resolve(), DEFAULT_MODEL)
    assert k1 != k2
