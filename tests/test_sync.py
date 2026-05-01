"""Tests for sync.py helpers — resolve_destinations and playlist-mode state."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playlist_syncer import db
from playlist_syncer.sync import _bp_url, resolve_destinations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_beatport(playlists: list[dict]) -> MagicMock:
    bp = MagicMock()
    bp.list_my_playlists.return_value = playlists
    bp.create_playlist.side_effect = lambda name: {"id": 9999}
    return bp


# ---------------------------------------------------------------------------
# _bp_url
# ---------------------------------------------------------------------------

class TestBpUrl:
    def test_returns_full_url_when_slug_and_id_present(self):
        match = {"id": 12345, "slug": "some-track-original-mix"}
        assert _bp_url(match) == "https://www.beatport.com/track/some-track-original-mix/12345"

    def test_returns_empty_string_when_slug_missing(self):
        assert _bp_url({"id": 1}) == ""

    def test_returns_empty_string_when_id_missing(self):
        assert _bp_url({"slug": "some-track"}) == ""

    def test_returns_empty_string_for_empty_dict(self):
        assert _bp_url({}) == ""


# ---------------------------------------------------------------------------
# resolve_destinations — name matching
# ---------------------------------------------------------------------------

class TestResolveDestinationsByName:
    def test_exact_name_match(self):
        bp = _make_beatport([{"id": 1, "name": "Melodic House"}])
        result = resolve_destinations(bp, dry_run=False, single_playlist_name="Melodic House")
        assert result == {"Melodic House": 1}

    def test_name_not_found_creates_playlist(self):
        bp = _make_beatport([])
        result = resolve_destinations(bp, dry_run=False, single_playlist_name="New Playlist")
        assert result == {"New Playlist": 9999}
        bp.create_playlist.assert_called_once_with("New Playlist")

    def test_name_not_found_dry_run_does_not_create(self):
        bp = _make_beatport([])
        result = resolve_destinations(bp, dry_run=True, single_playlist_name="Missing")
        assert result == {}
        bp.create_playlist.assert_not_called()


# ---------------------------------------------------------------------------
# resolve_destinations — ID matching (new behaviour)
# ---------------------------------------------------------------------------

class TestResolveDestinationsByID:
    def test_numeric_string_matches_by_id(self):
        bp = _make_beatport([{"id": 42, "name": "Techno Mix"}])
        result = resolve_destinations(bp, dry_run=False, single_playlist_name="42")
        assert result == {"42": 42}
        bp.create_playlist.assert_not_called()

    def test_id_not_in_account_falls_through_to_create(self):
        bp = _make_beatport([{"id": 1, "name": "Some Playlist"}])
        result = resolve_destinations(bp, dry_run=False, single_playlist_name="999")
        assert result == {"999": 9999}
        bp.create_playlist.assert_called_once_with("999")

    def test_numeric_id_dry_run_does_not_create_when_not_found(self):
        bp = _make_beatport([])
        result = resolve_destinations(bp, dry_run=True, single_playlist_name="999")
        assert result == {}
        bp.create_playlist.assert_not_called()

    def test_name_takes_precedence_over_id_for_named_playlist(self):
        # A playlist named "42" should match by name before checking IDs.
        bp = _make_beatport([
            {"id": 100, "name": "42"},
            {"id": 42, "name": "Other"},
        ])
        result = resolve_destinations(bp, dry_run=False, single_playlist_name="42")
        # "42" matches the playlist named "42" (id=100), not the one with id=42.
        assert result == {"42": 100}


# ---------------------------------------------------------------------------
# Playlist-mode state isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def detect_state_db(tmp_path) -> Path:
    path = tmp_path / "detect_sync.db"
    db.init_db(path)
    return path


class TestPlaylistModeStateKeys:
    def test_playlist_key_isolated_from_genre_key(self, detect_state_db):
        genre_key = "detect:foo.db"
        playlist_key = "detect:foo.db:playlist:Detected"

        db.mark_synced("1", genre_key, "added", db_path=detect_state_db)
        db.mark_synced("2", playlist_key, "added", db_path=detect_state_db)

        assert db.load_synced_set(genre_key, detect_state_db) == {"1"}
        assert db.load_synced_set(playlist_key, detect_state_db) == {"2"}

    def test_different_playlists_have_independent_state(self, detect_state_db):
        key_a = "detect:foo.db:playlist:Playlist A"
        key_b = "detect:foo.db:playlist:Playlist B"

        db.mark_synced("10", key_a, "added", db_path=detect_state_db)

        assert db.load_synced_set(key_a, detect_state_db) == {"10"}
        assert db.load_synced_set(key_b, detect_state_db) == set()

    def test_numeric_id_key_isolated_from_name_key(self, detect_state_db):
        id_key = "detect:foo.db:playlist:42"
        name_key = "detect:foo.db:playlist:Detected"

        db.mark_synced("5", id_key, "added", db_path=detect_state_db)

        assert db.load_synced_set(id_key, detect_state_db) == {"5"}
        assert db.load_synced_set(name_key, detect_state_db) == set()

    def test_tracks_skipped_on_subsequent_sync(self, detect_state_db):
        key = "detect:foo.db:playlist:Detected"
        track_ids = ["1", "2", "3"]

        for tid in track_ids:
            db.mark_synced(tid, key, "added", db_path=detect_state_db)

        synced = db.load_synced_set(key, detect_state_db)
        all_tracks = [{"id": int(t)} for t in ["1", "2", "3", "4", "5"]]
        remaining = [t for t in all_tracks if str(t["id"]) not in synced]
        assert [t["id"] for t in remaining] == [4, 5]
