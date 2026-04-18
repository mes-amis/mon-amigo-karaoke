"""Resolve artist / album metadata for a song.

Ableton's stem exports strip metadata tags, so we can't count on the
audio files themselves. The best-effort strategy is:

    CLI flags  >  macOS Music.app library lookup  >  empty

The lookup is optional (non-macOS systems and machines where Music.app
isn't running simply get nothing back — no error).
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Callable


MetaLookup = Callable[[str], "dict[str, str] | None"]


def _applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _title_variants(title: str) -> list[str]:
    """Broaden a title into likely library matches.

    ``Dry Your Eyes (Concert Version) [feat. Neil Diamond]`` is
    unlikely to match a Music.app track named plainly
    ``Dry Your Eyes``, so we also try a parenthetical-free form.
    """
    variants = [title]
    stripped = re.sub(r"\s*[\(\[].*?[\)\]]", "", title).strip()
    if stripped and stripped != title:
        variants.append(stripped)
    return variants


def apple_music_lookup(title: str, timeout: float = 3.0) -> dict[str, str] | None:
    """Return ``{"artist", "album"}`` from Music.app, or None.

    Guards with ``System Events`` so Music.app is never auto-launched
    just to service a lookup. Silent on any error — this is an
    "if available" feature, never a hard dependency.
    """
    if sys.platform != "darwin":
        return None

    for variant in _title_variants(title):
        script = (
            'tell application "System Events" to '
            'set musicRunning to (exists process "Music")\n'
            'if not musicRunning then return ""\n'
            'tell application "Music"\n'
            f'    set matched to (every track whose name is "{_applescript_escape(variant)}")\n'
            '    if (count of matched) = 0 then return ""\n'
            '    set t to first item of matched\n'
            '    return (artist of t) & "\\t" & (album of t)\n'
            'end tell\n'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            continue
        out = result.stdout.strip()
        if not out or "\t" not in out:
            continue
        artist, album = out.split("\t", 1)
        artist = artist.strip()
        album = album.strip()
        if artist or album:
            return {"artist": artist, "album": album}

    return None


def resolve_metadata(
    title: str,
    *,
    artist_override: str | None = None,
    album_override: str | None = None,
    lookup: MetaLookup = apple_music_lookup,
) -> dict[str, str]:
    """Pick artist / album from overrides first, then the lookup.

    ``lookup`` is injectable so tests can avoid shelling out to
    AppleScript.
    """
    result = {"artist": "", "album": ""}
    if artist_override:
        result["artist"] = artist_override.strip()
    if album_override:
        result["album"] = album_override.strip()

    if result["artist"] and result["album"]:
        return result

    found = lookup(title) or {}
    if not result["artist"]:
        result["artist"] = found.get("artist", "").strip()
    if not result["album"]:
        result["album"] = found.get("album", "").strip()
    return result
