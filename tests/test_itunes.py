"""iTunes Search API parsing.

We don't actually hit ``itunes.apple.com`` from the test suite — the
network is flaky and the API returns slightly different payloads in
different storefronts. Instead we feed ``_parse_results`` a frozen
sample payload that mirrors the real response shape, and we test the
interactive prompt by injecting a stub ``search_fn``.
"""

from __future__ import annotations

import builtins
import io
from pathlib import Path

import pytest

from karaoke.itunes import (
    _format_track,
    _parse_results,
    combined_search,
    find_local_audio,
    iTunesTrack,
    prompt_pick_metadata,
    prompt_pick_track,
    search_filesystem,
    search_local_library,
)


SAMPLE_PAYLOAD = {
    "resultCount": 3,
    "results": [
        {
            "wrapperType": "track",
            "kind": "song",
            "artistName": "They Might Be Giants",
            "collectionName": "John Henry",
            "trackName": "Doctor Worm",
            "releaseDate": "1994-07-19T07:00:00Z",
        },
        {
            "wrapperType": "track",
            "kind": "song",
            "artistName": "They Might Be Giants",
            "collectionName": "Severe Tire Damage",
            "trackName": "Doctor Worm (Live)",
            "releaseDate": "1998-09-22T07:00:00Z",
        },
        {
            # Music videos and other non-song entries are filtered out.
            "wrapperType": "track",
            "kind": "music-video",
            "artistName": "Some Artist",
            "trackName": "Doctor Worm",
        },
    ],
}


def test_parse_results_keeps_only_songs() -> None:
    tracks = _parse_results(SAMPLE_PAYLOAD)
    assert len(tracks) == 2
    assert all(isinstance(t, iTunesTrack) for t in tracks)


def test_parse_results_extracts_year_from_release_date() -> None:
    tracks = _parse_results(SAMPLE_PAYLOAD)
    years = sorted(t.year for t in tracks)
    assert years == ["1994", "1998"]


def test_parse_results_handles_missing_release_date() -> None:
    payload = {"results": [{
        "kind": "song",
        "trackName": "X", "artistName": "Y", "collectionName": "Z",
    }]}
    track = _parse_results(payload)[0]
    assert track.year is None


def test_parse_results_handles_empty_payload() -> None:
    assert _parse_results({}) == []
    assert _parse_results({"results": []}) == []


# --- prompt_pick_metadata, with stubbed input + search ---------------


@pytest.fixture
def isolated_stdio(monkeypatch):
    """Force isatty()=True and capture prompt outputs for inspection."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    out = io.StringIO()
    return out


def _stub_inputs(monkeypatch, answers: list[str]) -> None:
    queue = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda *_a, **_k: next(queue))


def test_prompt_uses_default_query_when_user_hits_enter(
    monkeypatch, isolated_stdio,
) -> None:
    seen_queries: list[str] = []

    def fake_search(term, limit):
        seen_queries.append(term)
        return _parse_results(SAMPLE_PAYLOAD)

    _stub_inputs(monkeypatch, ["", "1"])  # blank → use default; pick #1

    picked = prompt_pick_metadata(
        "Doctor Worm", search_fn=fake_search, out=isolated_stdio,
    )

    assert seen_queries == ["Doctor Worm"]
    assert picked == {"artist": "They Might Be Giants", "album": "John Henry"}


def test_prompt_pick_zero_returns_none(monkeypatch, isolated_stdio) -> None:
    _stub_inputs(monkeypatch, ["worm", "0"])
    fake_search = lambda *_: _parse_results(SAMPLE_PAYLOAD)  # noqa: E731
    assert prompt_pick_metadata("x", search_fn=fake_search, out=isolated_stdio) is None


def test_prompt_returns_none_when_search_yields_nothing(
    monkeypatch, isolated_stdio,
) -> None:
    _stub_inputs(monkeypatch, ["nonsense"])
    assert prompt_pick_metadata(
        "x", search_fn=lambda *_: [], out=isolated_stdio,
    ) is None


def test_prompt_returns_none_when_search_raises(monkeypatch, isolated_stdio) -> None:
    _stub_inputs(monkeypatch, ["worm"])

    def boom(*_a, **_k):
        raise OSError("network down")

    assert prompt_pick_metadata(
        "x", search_fn=boom, out=isolated_stdio,
    ) is None
    assert "search failed" in isolated_stdio.getvalue()


def test_prompt_skips_silently_when_stdin_is_not_a_tty(
    monkeypatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    out = io.StringIO()
    assert prompt_pick_metadata("x", search_fn=lambda *_: [], out=out) is None
    # The user is told why we skipped, but we don't hang waiting for input.
    assert "stdin is not a terminal" in out.getvalue()


def test_prompt_recovers_from_invalid_index(monkeypatch, isolated_stdio) -> None:
    _stub_inputs(monkeypatch, ["worm", "99", "abc", "1"])
    picked = prompt_pick_metadata(
        "x",
        search_fn=lambda *_: _parse_results(SAMPLE_PAYLOAD),
        out=isolated_stdio,
    )
    assert picked is not None
    assert picked["artist"] == "They Might Be Giants"


def test_prompt_pick_track_returns_full_track_record(
    monkeypatch, isolated_stdio,
) -> None:
    _stub_inputs(monkeypatch, ["worm", "1"])
    picked = prompt_pick_track(
        "Doctor Worm",
        search_fn=lambda *_: _parse_results(SAMPLE_PAYLOAD),
        out=isolated_stdio,
    )
    assert isinstance(picked, iTunesTrack)
    assert picked.title == "Doctor Worm"
    assert picked.artist == "They Might Be Giants"
    assert picked.album == "John Henry"


def test_prompt_pick_track_works_with_no_default_query(
    monkeypatch, isolated_stdio,
) -> None:
    """When --itunes is the input source there's no song title to default to."""
    _stub_inputs(monkeypatch, ["worm", "1"])
    picked = prompt_pick_track(
        "",
        search_fn=lambda *_: _parse_results(SAMPLE_PAYLOAD),
        out=isolated_stdio,
    )
    assert picked is not None and picked.title == "Doctor Worm"


def test_prompt_pick_track_returns_none_on_blank_query_no_default(
    monkeypatch, isolated_stdio,
) -> None:
    _stub_inputs(monkeypatch, [""])  # nothing typed, no default → bail
    picked = prompt_pick_track(
        "", search_fn=lambda *_: [], out=isolated_stdio,
    )
    assert picked is None


# --- find_local_audio ---------------------------------------------


def test_find_local_audio_returns_path_when_runner_yields_file(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    fake_file = tmp_path / "Doctor Worm.m4a"
    fake_file.write_bytes(b"\x00")

    track = iTunesTrack(title="Doctor Worm", artist="TMBG", album="John Henry")
    path = find_local_audio(track, runner=lambda _script: str(fake_file))

    assert path == fake_file


def test_find_local_audio_returns_none_when_track_not_in_library(
    monkeypatch,
) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    track = iTunesTrack(title="x", artist="y", album="z")
    # AppleScript returns "" for both "no match" and "cloud-only/no location".
    assert find_local_audio(track, runner=lambda _s: "") is None


def test_find_local_audio_returns_none_when_runner_raises(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    track = iTunesTrack(title="x", artist="y", album="z")

    def boom(_s):
        raise RuntimeError("osascript timed out")

    assert find_local_audio(track, runner=boom) is None


def test_find_local_audio_skips_entirely_on_non_darwin(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    track = iTunesTrack(title="x", artist="y", album="z")
    # Runner shouldn't even be called.
    called = []
    def runner(_s):
        called.append(True)
        return "/some/path"
    assert find_local_audio(track, runner=runner) is None
    assert called == []


def test_find_local_audio_returns_none_when_path_not_a_file(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    track = iTunesTrack(title="x", artist="y", album="z")
    # AppleScript returned a path, but the file doesn't exist on disk —
    # treat as not found rather than handing the rest of the pipeline a
    # bogus Path.
    assert find_local_audio(
        track, runner=lambda _s: str(tmp_path / "missing.m4a"),
    ) is None


def test_find_local_audio_short_circuits_when_track_already_has_location(
    tmp_path, monkeypatch,
) -> None:
    """Tracks coming back from search_local_library already know their path —
    no need to re-query AppleScript."""
    monkeypatch.setattr("sys.platform", "darwin")
    fake_file = tmp_path / "Ophelia.m4a"
    fake_file.write_bytes(b"\x00")
    track = iTunesTrack(title="x", artist="y", album="z", location=fake_file)

    # Runner that would fail loudly if invoked.
    def boom(_s):
        pytest.fail("AppleScript should not run for already-resolved tracks")

    assert find_local_audio(track, runner=boom) == fake_file


# --- search_local_library + combined_search ----------------------


_LIBRARY_OUTPUT = (
    "Ophelia (Concert Version)\tThe Band\tThe Last Waltz\t"
    "/Users/craig/Music/iTunes/iTunes Media/Music/The Band/The Last Waltz/"
    "2-03 Ophelia (Concert Version).m4a\n"
    "Ophelia\tNatalie Merchant\tTigerlily\t"
    "/Users/craig/Music/Music/Media/Tigerlily/Ophelia.m4a\n"
)


def test_search_local_library_parses_tab_separated_output(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")

    tracks = search_local_library("ophelia", runner=lambda _s: _LIBRARY_OUTPUT)

    assert len(tracks) == 2
    first = tracks[0]
    assert first.title == "Ophelia (Concert Version)"
    assert first.artist == "The Band"
    assert first.album == "The Last Waltz"
    assert first.is_local
    assert first.location.name == "2-03 Ophelia (Concert Version).m4a"


def test_search_local_library_skips_blank_query(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    called = []
    def runner(_s):
        called.append(True)
        return ""
    assert search_local_library("   ", runner=runner) == []
    assert called == []  # no AppleScript fired


def test_search_local_library_returns_empty_on_non_darwin(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    called = []
    def runner(_s):
        called.append(True)
        return _LIBRARY_OUTPUT
    assert search_local_library("ophelia", runner=runner) == []
    assert called == []


def test_search_local_library_swallows_runner_errors(monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    def boom(_s):
        raise RuntimeError("osascript timeout")
    assert search_local_library("ophelia", runner=boom) == []


def test_combined_search_puts_local_results_first() -> None:
    local = [iTunesTrack(
        title="Ophelia (Concert Version)", artist="The Band",
        album="The Last Waltz", location=Path("/tmp/x.m4a"),
    )]
    catalog = [iTunesTrack(
        title="Ophelia", artist="Natalie Merchant", album="Tigerlily", year="1995",
    )]

    merged = combined_search(
        "ophelia",
        catalog_fn=lambda _q, _l: catalog,
        local_fn=lambda _q: local,
        fs_fn=lambda _q: [],
    )

    assert merged[0].is_local
    assert merged[0].artist == "The Band"
    assert merged[1].artist == "Natalie Merchant"


def test_combined_search_dedupes_catalog_against_local() -> None:
    """If iTunes catalog has the exact same title+artist as a local file,
    suppress the catalog entry — the local one already covers it and is
    actually playable."""
    local = [iTunesTrack(
        title="Doctor Worm", artist="They Might Be Giants",
        album="John Henry", location=Path("/tmp/x.m4a"),
    )]
    catalog = [
        iTunesTrack(title="Doctor Worm", artist="They Might Be Giants",
                    album="John Henry", year="1994"),
        iTunesTrack(title="Doctor Worm (Live)", artist="They Might Be Giants",
                    album="Severe Tire Damage", year="1998"),
    ]

    merged = combined_search(
        "worm",
        catalog_fn=lambda _q, _l: catalog,
        local_fn=lambda _q: local,
        fs_fn=lambda _q: [],
    )

    titles = [t.title for t in merged]
    # Only one "Doctor Worm" — the local one. The live track survives.
    assert titles == ["Doctor Worm", "Doctor Worm (Live)"]


def test_combined_search_returns_local_when_catalog_fails() -> None:
    """Offline / TLS error shouldn't make the local-library results
    invisible — that's the whole point of having them locally."""
    local = [iTunesTrack(
        title="Ophelia", artist="The Band", album="The Last Waltz",
        location=Path("/tmp/x.m4a"),
    )]

    def angry_catalog(_q, _l):
        raise OSError("network unreachable")

    merged = combined_search(
        "ophelia",
        catalog_fn=angry_catalog,
        local_fn=lambda _q: local,
        fs_fn=lambda _q: [],
    )

    assert len(merged) == 1
    assert merged[0].is_local


# --- _format_track marker ----------------------------------------


# --- search_filesystem ----------------------------------------------


def _make_music_tree(root: Path) -> Path:
    """Build a tiny iTunes-shaped folder tree for filesystem search tests."""
    root = root / "Music"
    (root / "The Band" / "The Last Waltz").mkdir(parents=True)
    (root / "The Band" / "The Last Waltz" / "2-03 Ophelia (Concert Version).m4a").write_bytes(b"\x00")
    (root / "The Band" / "The Last Waltz" / "1-01 Theme From the Last Waltz.m4a").write_bytes(b"\x00")
    (root / "Natalie Merchant" / "Tigerlily").mkdir(parents=True)
    (root / "Natalie Merchant" / "Tigerlily" / "01 Ophelia.m4a").write_bytes(b"\x00")
    # Non-audio sibling — should be ignored.
    (root / "The Band" / "The Last Waltz" / "cover.jpg").write_bytes(b"\x00")
    return root


def test_search_filesystem_finds_track_by_name(tmp_path) -> None:
    root = _make_music_tree(tmp_path)
    hits = search_filesystem("ophelia", roots=[root])
    titles = [h.title for h in hits]
    assert "Ophelia (Concert Version)" in titles
    assert "Ophelia" in titles


def test_search_filesystem_extracts_artist_and_album_from_path(tmp_path) -> None:
    root = _make_music_tree(tmp_path)
    hits = search_filesystem("concert version", roots=[root])
    assert len(hits) == 1
    track = hits[0]
    assert track.artist == "The Band"
    assert track.album == "The Last Waltz"
    assert track.is_local
    assert track.location.name == "2-03 Ophelia (Concert Version).m4a"


def test_search_filesystem_matches_by_artist_or_album(tmp_path) -> None:
    root = _make_music_tree(tmp_path)
    by_artist = search_filesystem("the band", roots=[root])
    by_album = search_filesystem("tigerlily", roots=[root])
    assert {h.title for h in by_artist} == {
        "Ophelia (Concert Version)", "Theme From the Last Waltz",
    }
    assert [h.title for h in by_album] == ["Ophelia"]


def test_search_filesystem_strips_leading_track_number(tmp_path) -> None:
    root = _make_music_tree(tmp_path)
    hits = search_filesystem("waltz", roots=[root])
    titles = {h.title for h in hits}
    # "1-01 Theme..." and "2-03 Ophelia..." both lose their track-number prefix.
    assert "Theme From the Last Waltz" in titles
    assert "Ophelia (Concert Version)" in titles
    assert not any(t.startswith(("01 ", "1-01 ", "2-03 ")) for t in titles)


def test_search_filesystem_skips_non_audio_files(tmp_path) -> None:
    root = _make_music_tree(tmp_path)
    hits = search_filesystem("cover", roots=[root])
    assert hits == []  # cover.jpg matches name but isn't an audio file


def test_search_filesystem_handles_nonexistent_roots(tmp_path) -> None:
    # A root that doesn't exist is silently skipped — common case is a
    # user without the legacy iTunes folder.
    hits = search_filesystem("ophelia", roots=[tmp_path / "does-not-exist"])
    assert hits == []


def test_search_filesystem_blank_query_returns_nothing(tmp_path) -> None:
    root = _make_music_tree(tmp_path)
    assert search_filesystem("   ", roots=[root]) == []


def test_combined_search_picks_up_filesystem_finds_when_library_is_empty(
    tmp_path,
) -> None:
    """The reported regression: Music.app finds nothing but the file is
    on disk — the picker must still surface it."""
    root = _make_music_tree(tmp_path)

    merged = combined_search(
        "ophelia",
        catalog_fn=lambda _q, _l: [],
        local_fn=lambda _q: [],
        fs_fn=lambda q: search_filesystem(q, roots=[root]),
    )

    assert any(t.is_local and "Concert Version" in t.title for t in merged)


def test_combined_search_prefers_library_over_filesystem_for_same_path(
    tmp_path,
) -> None:
    """If a track is in both Music.app and on disk we use the library
    record (which has cleaner metadata) and drop the filesystem dupe."""
    root = _make_music_tree(tmp_path)
    file_path = root / "The Band" / "The Last Waltz" / "2-03 Ophelia (Concert Version).m4a"

    library_track = iTunesTrack(
        title="Ophelia (Concert Version)",
        artist="The Band",
        album="The Last Waltz",
        location=file_path,
    )

    merged = combined_search(
        "ophelia",
        catalog_fn=lambda _q, _l: [],
        local_fn=lambda _q: [library_track],
        fs_fn=lambda q: search_filesystem(q, roots=[root]),
    )

    # The duplicate (same path) is dropped — only the library version remains.
    matching = [t for t in merged if t.location == file_path]
    assert len(matching) == 1


def test_format_track_marks_downloaded_results() -> None:
    local_track = iTunesTrack(
        title="Ophelia", artist="The Band", album="The Last Waltz",
        location=Path("/tmp/x.m4a"),
    )
    catalog_track = iTunesTrack(
        title="Ophelia", artist="The Band", album="The Last Waltz",
    )

    assert _format_track(local_track).startswith("[downloaded]")
    assert not _format_track(catalog_track).startswith("[downloaded]")
