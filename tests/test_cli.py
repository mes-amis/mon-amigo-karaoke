"""CLI helpers and argument parsing."""

from __future__ import annotations

from pathlib import Path

from karaoke.cli import DEFAULT_OUTPUT_DIR, _safe_filename


def test_default_output_dir_is_on_desktop() -> None:
    assert DEFAULT_OUTPUT_DIR == Path("~/Desktop/mon-amigo-karaoke").expanduser()


def test_safe_filename_strips_path_separators() -> None:
    # Song titles contain punctuation; forward/back slashes would create
    # sub-directories on Path construction.
    assert _safe_filename("AC/DC") == "AC_DC"
    assert _safe_filename(r"a\b") == "a_b"


def test_safe_filename_strips_windows_reserved_chars() -> None:
    assert _safe_filename('a:b?c*d"e|f<g>h') == "a_b_c_d_e_f_g_h"


def test_safe_filename_falls_back_for_empty_input() -> None:
    # An empty / whitespace-only title shouldn't produce an empty filename.
    assert _safe_filename("") == "karaoke"
    assert _safe_filename("   ") == "karaoke"


def test_safe_filename_keeps_spaces_and_brackets() -> None:
    assert (
        _safe_filename("Dry Your Eyes (Concert Version) [feat. Neil Diamond]")
        == "Dry Your Eyes (Concert Version) [feat. Neil Diamond]"
    )
