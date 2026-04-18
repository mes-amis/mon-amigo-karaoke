"""Artist / album resolution.

We don't run AppleScript in tests — the lookup is stubbed via the
``lookup`` parameter so we can pin the "flags beat discovery, then
discovery, then empty" precedence in isolation.
"""

from __future__ import annotations

from karaoke.metadata import _title_variants, resolve_metadata


def test_cli_flags_take_precedence_over_lookup() -> None:
    def lookup(_title: str):
        return {"artist": "Wrong Band", "album": "Wrong Album"}

    meta = resolve_metadata(
        "Doctor Worm",
        artist_override="They Might Be Giants",
        album_override="John Henry",
        lookup=lookup,
    )

    assert meta == {"artist": "They Might Be Giants", "album": "John Henry"}


def test_flags_fill_gaps_from_lookup() -> None:
    # Only artist overridden; album should come from lookup.
    def lookup(_title: str):
        return {"artist": "wrong", "album": "John Henry"}

    meta = resolve_metadata(
        "Doctor Worm",
        artist_override="They Might Be Giants",
        lookup=lookup,
    )

    assert meta == {"artist": "They Might Be Giants", "album": "John Henry"}


def test_lookup_used_when_no_flags() -> None:
    def lookup(_title: str):
        return {"artist": "TMBG", "album": "John Henry"}

    meta = resolve_metadata("Doctor Worm", lookup=lookup)

    assert meta == {"artist": "TMBG", "album": "John Henry"}


def test_empty_when_nothing_available() -> None:
    meta = resolve_metadata("Unknown Song", lookup=lambda _t: None)
    assert meta == {"artist": "", "album": ""}


def test_title_variants_strips_parentheticals() -> None:
    variants = _title_variants("Dry Your Eyes (Concert Version) [feat. Neil Diamond]")
    assert "Dry Your Eyes (Concert Version) [feat. Neil Diamond]" in variants
    assert "Dry Your Eyes" in variants


def test_title_variants_no_duplicate_for_plain_title() -> None:
    variants = _title_variants("Doctor Worm")
    assert variants == ["Doctor Worm"]
