"""Interactive iTunes Search API picker for artist / album metadata.

The public Search API at https://itunes.apple.com/search returns track
records as JSON. We don't need the audio — we just want the curated
``artistName`` and ``collectionName`` so our karaoke title card can
credit the right band and album.

Usage from the CLI: ``--itunes`` flips :func:`prompt_pick_metadata` on,
which:
  1. asks the user for a search term (defaulting to the song title),
  2. shows up to 10 matches in a numbered list,
  3. returns the picked track's ``{"artist", "album"}`` (or ``None`` if
     the user skips, the search yields nothing, or stdin isn't a TTY).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
DEFAULT_LIMIT = 10
DEFAULT_TIMEOUT = 8.0


@dataclass(frozen=True)
class iTunesTrack:
    title: str
    artist: str
    album: str
    year: str | None = None
    # When the track was discovered in the local Music.app library,
    # ``location`` is the on-disk path. ``None`` means catalog-only.
    location: Path | None = None

    @property
    def is_local(self) -> bool:
        return self.location is not None


# Injectable for tests so we can stub out the HTTP request.
SearchFn = Callable[[str, int], list[iTunesTrack]]
LocalSearchFn = Callable[[str], list[iTunesTrack]]


def _enable_system_trust_store() -> None:
    """Same trick used in transcribe.py — respect the macOS Keychain so
    corporate TLS proxies don't break the API call."""
    try:
        import truststore  # type: ignore
        truststore.inject_into_ssl()
    except Exception:
        pass


def _parse_results(payload: dict) -> list[iTunesTrack]:
    tracks: list[iTunesTrack] = []
    for r in payload.get("results", []):
        if r.get("kind") != "song":
            continue
        title = (r.get("trackName") or "").strip()
        artist = (r.get("artistName") or "").strip()
        album = (r.get("collectionName") or "").strip()
        if not title and not artist:
            continue
        release = (r.get("releaseDate") or "")[:4]
        tracks.append(iTunesTrack(
            title=title, artist=artist, album=album,
            year=release if release.isdigit() else None,
        ))
    return tracks


def search(term: str, limit: int = DEFAULT_LIMIT) -> list[iTunesTrack]:
    """Hit the iTunes Search API and return parsed song matches."""
    _enable_system_trust_store()
    params = {
        "term": term,
        "entity": "song",
        "limit": str(limit),
        "media": "music",
    }
    url = f"{ITUNES_SEARCH_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "custom-karaoke/0.1 (+local)"},
    )
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
        payload = json.load(resp)
    return _parse_results(payload)


def _format_track(t: iTunesTrack) -> str:
    bits = [t.title or "(untitled)"]
    if t.artist:
        bits.append(t.artist)
    if t.album:
        bits.append(t.album)
    line = " — ".join(bits)
    if t.year:
        line += f"  ({t.year})"
    if t.is_local:
        # ASCII marker so it renders the same in any terminal/font.
        line = "[downloaded] " + line
    return line


def search_local_library(
    query: str,
    *,
    runner: "ScriptRunner | None" = None,
    limit: int = 20,
) -> list[iTunesTrack]:
    """Match ``query`` against name/artist/album in the local Music.app library.

    Returns only tracks that have a real on-disk file (cloud-only tracks
    are filtered out — they can't be karaoke'd anyway). Skipped silently
    on non-macOS, when Music.app isn't running, or when the AppleScript
    bridge errors.
    """
    if sys.platform != "darwin":
        return []
    if not query.strip():
        return []
    runner = runner or _osascript

    q = _applescript_escape(query.strip())
    script = (
        f'set q to "{q}"\n'
        'tell application "System Events" to '
        'set musicRunning to (exists process "Music")\n'
        'if not musicRunning then return ""\n'
        'tell application "Music"\n'
        '    set matched to (every track of library playlist 1 whose '
        '(name contains q) or (artist contains q) or (album contains q))\n'
        '    set total to count of matched\n'
        f'    set lim to {int(limit)}\n'
        '    if total < lim then set lim to total\n'
        '    set output to ""\n'
        '    repeat with i from 1 to lim\n'
        '        set t to item i of matched\n'
        '        try\n'
        '            set loc to POSIX path of (location of t as alias)\n'
        '        on error\n'
        '            set loc to ""\n'
        '        end try\n'
        '        if loc is not "" then\n'
        '            set output to output & (name of t) & "\\t" & '
        '(artist of t) & "\\t" & (album of t) & "\\t" & loc & linefeed\n'
        '        end if\n'
        '    end repeat\n'
        '    return output\n'
        'end tell\n'
    )

    try:
        out = runner(script)
    except Exception:
        return []
    if not out:
        return []

    tracks: list[iTunesTrack] = []
    for line in out.splitlines():
        line = line.rstrip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        title, artist, album, loc = parts[0], parts[1], parts[2], parts[3]
        tracks.append(iTunesTrack(
            title=title.strip(),
            artist=artist.strip(),
            album=album.strip(),
            location=Path(loc),
        ))
    return tracks


# Standard places macOS keeps audio files. Both the legacy iTunes layout
# (~/Music/iTunes/iTunes Media/Music) and the modern Music.app one
# (~/Music/Music/Media…) are checked — users frequently have one but
# not the other, or both side-by-side after migrating.
DEFAULT_FILESYSTEM_ROOTS: tuple[Path, ...] = (
    Path("~/Music/iTunes/iTunes Media/Music").expanduser(),
    Path("~/Music/Music/Media/Music").expanduser(),
    Path("~/Music/Music/Media.localized/Music").expanduser(),
)


def search_filesystem(
    query: str,
    *,
    roots: Iterable[Path] = DEFAULT_FILESYSTEM_ROOTS,
    limit: int = 30,
) -> list[iTunesTrack]:
    """Walk standard music folders for files whose path contains ``query``.

    This is the safety net for files Music.app doesn't know about — e.g.
    a legacy iTunes folder where the user never re-imported the library
    into Music.app, or files dragged in but never indexed.

    Convention assumed: ``<root>/<Artist>/<Album>/<Track>.<ext>``.
    """
    q = query.lower().strip()
    if not q:
        return []

    # Local import avoids a circular dep (stems.py is fine to import from
    # here, but doing it lazily keeps the import graph cleaner).
    from .stems import AUDIO_EXTS

    results: list[iTunesTrack] = []
    seen_paths: set[Path] = set()

    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in AUDIO_EXTS:
                continue
            if path in seen_paths:
                continue
            try:
                rel = path.relative_to(root).parts
            except ValueError:
                continue

            # Standard layout: Artist / Album / Track.ext
            artist = rel[0] if len(rel) >= 3 else ""
            album = (
                rel[1] if len(rel) >= 3
                else (rel[0] if len(rel) == 2 else "")
            )
            title = re.sub(r"^\d+[-\d]*\s+", "", path.stem).strip() or path.stem

            haystack = f"{title} {album} {artist}".lower()
            if q not in haystack:
                continue

            results.append(iTunesTrack(
                title=title, artist=artist, album=album, location=path,
            ))
            seen_paths.add(path)
            if len(results) >= limit:
                return results

    return results


def combined_search(
    query: str,
    limit: int = DEFAULT_LIMIT,
    *,
    catalog_fn: SearchFn = search,
    local_fn: LocalSearchFn = search_local_library,
    fs_fn: LocalSearchFn = search_filesystem,
) -> list[iTunesTrack]:
    """Music.app library matches → filesystem matches → iTunes catalog.

    Each source is best-effort (Music.app off, no internet, no legacy
    iTunes folder — any of those just contributes 0 results). Local
    sources are deduped by file path; the catalog is deduped against
    locals by (title, artist) so a downloaded version always wins over
    its catalog twin.
    """
    library = local_fn(query)
    fs_hits = fs_fn(query)

    library_paths = {t.location for t in library if t.location is not None}
    fs_unique = [t for t in fs_hits if t.location not in library_paths]
    local = library + fs_unique

    try:
        catalog = catalog_fn(query, limit)
    except Exception:
        catalog = []

    seen_keys = {(t.title.lower(), t.artist.lower()) for t in local}
    catalog_unique = [
        c for c in catalog
        if (c.title.lower(), c.artist.lower()) not in seen_keys
    ]
    return local + catalog_unique


def prompt_pick_track(
    default_query: str = "",
    *,
    search_fn: SearchFn = combined_search,
    out=sys.stderr,
    skip_label: str = "skip — keep current credits",
) -> iTunesTrack | None:
    """Interactive iTunes picker; returns the chosen ``iTunesTrack`` or None.

    Empty ``default_query`` means there's no fallback if the user hits
    enter — prompts again. With a default, a blank reply re-uses it.
    """
    if not sys.stdin.isatty():
        print(
            "[itunes] stdin is not a terminal — can't show an interactive "
            "picker. Pass --artist/--album (or an explicit input file) "
            "for non-interactive runs.",
            file=out,
        )
        return None

    prompt = (
        f"Search iTunes [{default_query}]: " if default_query
        else "Search iTunes: "
    )
    print(file=out)
    try:
        raw = input(prompt).strip()
    except EOFError:
        print(file=out)
        return None
    query = raw or default_query
    if not query:
        return None

    try:
        results = search_fn(query, DEFAULT_LIMIT)
    except Exception as exc:  # network / parse / TLS / timeout
        print(f"[itunes] search failed: {exc}", file=out)
        return None

    if not results:
        print(f"[itunes] no results for {query!r}", file=out)
        return None

    local_count = sum(1 for t in results if t.is_local)
    catalog_count = len(results) - local_count
    print(
        f"\n[itunes] {len(results)} result(s) for {query!r} "
        f"({local_count} from your library, {catalog_count} from iTunes catalog):",
        file=out,
    )
    for i, track in enumerate(results, 1):
        print(f"  [{i}] {_format_track(track)}", file=out)
    print(f"  [0] {skip_label}", file=out)
    if local_count == 0 and sys.platform == "darwin":
        print(
            "\n[itunes] tip: no local matches. If you expected a downloaded "
            "song, run `./bin/karaoke-diagnose-itunes` to see what your "
            "Terminal can read in ~/Music.",
            file=out,
        )
    print(file=out)

    while True:
        try:
            raw = input(f"Pick [0-{len(results)}]: ").strip()
        except EOFError:
            print(file=out)
            return None
        if not raw:
            continue
        try:
            idx = int(raw)
        except ValueError:
            print(f"  not a number: {raw!r}", file=out)
            continue
        if idx == 0:
            return None
        if 1 <= idx <= len(results):
            return results[idx - 1]
        print(f"  out of range; pick 0-{len(results)}", file=out)


def prompt_pick_metadata(
    default_query: str,
    *,
    search_fn: SearchFn = search,
    out=sys.stderr,
) -> dict[str, str] | None:
    """Thin wrapper around :func:`prompt_pick_track` that returns just credits."""
    track = prompt_pick_track(default_query, search_fn=search_fn, out=out)
    if track is None:
        return None
    return {"artist": track.artist, "album": track.album}


# --- Music.app library lookup ---------------------------------------

ScriptRunner = Callable[[str], str]


def _osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        check=True, capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip()


def _applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def find_local_audio(
    track: iTunesTrack,
    *,
    runner: ScriptRunner = _osascript,
) -> Path | None:
    """Look up an iTunes track in the local Music.app library.

    Returns the on-disk path of the matching file, or None if Music.app
    doesn't have this track downloaded (cloud-only tracks have no
    location). Non-macOS systems also return None.
    """
    # Fast path: combined_search already resolved the location for tracks
    # found in the local library, so we don't need a second AppleScript.
    if track.location is not None and track.location.is_file():
        return track.location

    if sys.platform != "darwin":
        return None

    script = (
        'tell application "System Events" to '
        'set musicRunning to (exists process "Music")\n'
        'if not musicRunning then return ""\n'
        'tell application "Music"\n'
        f'    set matched to (every track of library playlist 1 '
        f'whose name is "{_applescript_escape(track.title)}" '
        f'and artist is "{_applescript_escape(track.artist)}")\n'
        '    if (count of matched) = 0 then return ""\n'
        '    set t to first item of matched\n'
        '    try\n'
        '        return POSIX path of (location of t as alias)\n'
        '    on error\n'
        '        return ""\n'
        '    end try\n'
        'end tell\n'
    )
    try:
        path_text = runner(script)
    except Exception:
        return None
    if not path_text:
        return None
    path = Path(path_text)
    return path if path.is_file() else None
