"""Wrapper around the Swift musickit_bridge — auto-compile, run, and stream tracks."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

_BRIDGE_SRC = Path(__file__).resolve().parent / "bridge" / "musickit_bridge.swift"
_CACHE_DIR = Path.home() / ".cache" / "playlist-syncer"


def _bridge_binary() -> Path:
    """Compile musickit_bridge.swift if needed. Cache by source hash."""
    src = _BRIDGE_SRC
    src_hash = hashlib.md5(src.read_bytes()).hexdigest()[:8]
    binary = _CACHE_DIR / f"musickit_bridge_{src_hash}"
    if not binary.exists():
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for old in _CACHE_DIR.glob("musickit_bridge_*"):
            old.unlink(missing_ok=True)
        result = subprocess.run(
            ["swiftc", str(src), "-o", str(binary)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Swift compile failed:\n{result.stderr}")
    return binary


def run_bridge(args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    binary = _bridge_binary()
    return subprocess.run(
        [str(binary)] + args,
        capture_output=True, text=True, timeout=timeout,
    )


def check_musickit() -> tuple[bool, str]:
    """Return (authorized, message). Exit code 2 = not authorized."""
    try:
        result = run_bridge(["--check"], timeout=30)
        if result.returncode == 0:
            return True, "MusicKit authorized"
        if result.returncode == 2:
            return False, (
                "MusicKit not authorized.\n"
                "Open the Music app, then re-run this command to grant access."
            )
        return False, f"MusicKit check failed (exit {result.returncode}): {result.stderr.strip()}"
    except Exception as e:
        return False, f"MusicKit check error: {e}"


def list_playlists() -> list[str]:
    """Return Apple Music playlist names via MusicKit."""
    result = run_bridge(["--list-playlists"])
    if result.returncode != 0:
        raise RuntimeError(f"MusicKit error: {result.stderr.strip()}")
    return json.loads(result.stdout.strip())


def _stream_bridge(args: list[str]):
    """Run the MusicKit bridge with given args and yield track dicts from NDJSON stdout."""
    binary = _bridge_binary()
    proc = subprocess.Popen(
        [str(binary)] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
    finally:
        proc.stdout.close()

    exit_code = proc.wait()
    if exit_code != 0:
        stderr = proc.stderr.read()
        raise RuntimeError(
            f"MusicKit bridge exited with code {exit_code}. "
            f"Sync may be incomplete.\n{stderr.strip()}"
        )


def stream_playlist_tracks(playlist_name: str):
    yield from _stream_bridge(["--playlist", playlist_name])


def stream_library_tracks():
    """Yield track dicts for songs with libraryAddedDate set (Music app 'Songs' tab)."""
    yield from _stream_bridge(["--library-songs"])


def stream_favorite_tracks():
    """Yield track dicts for songs in the 'Favourite Songs' playlist."""
    yield from _stream_bridge(["--favorites"])


def stream_library_and_favorites_tracks():
    """Yield track dicts for songs that are in library OR in Favourite Songs."""
    yield from _stream_bridge(["--library-and-favorites"])


def stream_all_tracks():
    """Yield all track dicts from MusicLibraryRequest<Song> with no filter."""
    yield from _stream_bridge([])


def _escape_as(s: str) -> str:
    """Escape a string for AppleScript double-quoted context."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _search_and_add_to_playlist(pl_name: str, tracks: list[dict]) -> dict[str, str]:
    """Search local Music library for each track and duplicate into playlist.

    Returns dict of str(track["id"]) → status ("added" | "not_in_library" | "error").
    pl_name must already be AppleScript-escaped.
    """
    statuses: dict[str, str] = {}
    for track in tracks:
        title = _escape_as(track.get("title", ""))
        artist = _escape_as(track.get("artist", ""))
        track_id = str(track["id"])
        script = (
            f'tell application "Music"\n'
            f'  set found to (tracks of library playlist 1 '
            f'whose name is "{title}" and artist is "{artist}")\n'
            f'  if length of found > 0 then\n'
            f'    duplicate item 1 of found to user playlist "{pl_name}"\n'
            f'    return "added"\n'
            f'  else\n'
            f'    return "not_in_library"\n'
            f'  end if\n'
            f'end tell\n'
        )
        try:
            r = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=30,
            )
            statuses[track_id] = r.stdout.strip() if r.returncode == 0 else "error"
        except Exception:
            statuses[track_id] = "error"
    return statuses


def add_tracks_to_playlist(name: str, tracks: list[dict]) -> list[dict]:
    """Add tracks to a named Music.app playlist.

    Two-pass strategy:
      Pass 1 — search local library by title+artist, add found tracks.
      For tracks not yet in library — open URL scheme to add them (macOS 13+).
      Pass 2 — re-search and add the newly-added tracks.

    Each track dict must have: id, title, artist. apple_music_id is used for
    the URL-scheme library-add step when present.
    Returns list of {id, status} dicts — status: added | not_in_library | error.
    """
    import time

    pl_name = _escape_as(name)

    ensure_script = (
        f'tell application "Music"\n'
        f'  if not (exists user playlist "{pl_name}") then\n'
        f'    make new user playlist with properties {{name:"{pl_name}"}}\n'
        f'  end if\n'
        f'end tell\n'
    )
    try:
        r = subprocess.run(
            ["osascript", "-e", ensure_script],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
    except Exception as e:
        return [{"id": str(t["id"]), "status": "error", "error": str(e)} for t in tracks]

    # Pass 1: add tracks already in library
    statuses = _search_and_add_to_playlist(pl_name, tracks)

    missing = [t for t in tracks if statuses.get(str(t["id"])) == "not_in_library"]
    if not missing:
        return [{"id": str(t["id"]), "status": statuses[str(t["id"])]} for t in tracks]

    # Open URL scheme to add missing tracks to library (macOS 13+, best-effort)
    for track in missing:
        am_id = track.get("apple_music_id", "")
        if am_id:
            subprocess.run(
                ["open", f"music://music.apple.com/add?ids={am_id}"],
                capture_output=True, text=True, timeout=5,
            )

    # Wait for Music to process — 0.3 s per track, capped at 15 s
    time.sleep(min(len(missing) * 0.3, 15))

    # Pass 2: retry missing tracks
    retry_statuses = _search_and_add_to_playlist(pl_name, missing)
    statuses.update(retry_statuses)

    return [{"id": str(t["id"]), "status": statuses.get(str(t["id"]), "not_in_library")} for t in tracks]


def add_tracks_to_library(tracks: list[dict]) -> list[dict]:
    """Open Music.app to each track's catalog page via URL scheme (best-effort).

    MusicLibrary.shared write APIs are unavailable on macOS. This opens the
    Music.app song page; the track may be added silently on macOS 13+.
    Each track dict must have: id, apple_music_id.
    Returns list of {id, status} dicts — status: opened | no_id | error.
    """
    results = []
    for track in tracks:
        am_id = track.get("apple_music_id", "")
        track_id = str(track["id"])
        if not am_id:
            results.append({"id": track_id, "status": "no_id"})
            continue
        try:
            subprocess.run(
                ["open", f"music://music.apple.com/add?ids={am_id}"],
                check=True, capture_output=True, text=True, timeout=10,
            )
            results.append({"id": track_id, "status": "opened"})
        except Exception as e:
            results.append({"id": track_id, "status": "error", "error": str(e)})
    return results
