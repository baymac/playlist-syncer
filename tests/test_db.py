"""Tests for SQLite persistence layer."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatport_sync import db


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    path = tmp_path / "test_sync.db"
    db.init_db(path)
    return path


class TestInitDb:
    def test_creates_tables(self, tmp_db):
        import sqlite3
        con = sqlite3.connect(str(tmp_db))
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        con.close()
        assert {"synced_tracks", "sync_runs", "auth_cache", "cursors"} <= tables

    def test_idempotent(self, tmp_db):
        db.init_db(tmp_db)  # second call should not raise


class TestLoadSyncedSet:
    def test_empty_on_fresh_db(self, tmp_db):
        result = db.load_synced_set("My Playlist", tmp_db)
        assert result == set()

    def test_returns_only_for_given_playlist(self, tmp_db):
        db.mark_synced("123", "Playlist A", "added", db_path=tmp_db)
        db.mark_synced("456", "Playlist B", "added", db_path=tmp_db)
        result = db.load_synced_set("Playlist A", tmp_db)
        assert result == {"123"}

    def test_returns_terminal_outcomes(self, tmp_db):
        db.mark_synced("1", "P", "added", db_path=tmp_db)
        db.mark_synced("2", "P", "no_search_results", db_path=tmp_db)
        db.mark_synced("3", "P", "no_classify", db_path=tmp_db)
        result = db.load_synced_set("P", tmp_db)
        assert result == {"1", "2", "3"}

    def test_legacy_no_catalog_match_blocked(self, tmp_db):
        db.mark_synced("99", "P", "no_catalog_match", db_path=tmp_db)
        result = db.load_synced_set("P", tmp_db)
        assert "99" in result


class TestMarkSynced:
    def test_write_and_read_back(self, tmp_db):
        db.mark_synced("cat123", "Melodic", "added",
                       beatport_track_id=999, dest_playlist="Melodic House",
                       db_path=tmp_db)
        synced = db.load_synced_set("Melodic", tmp_db)
        assert "cat123" in synced

    def test_upsert_replaces_existing(self, tmp_db):
        db.mark_synced("cat123", "P", "no_catalog_match", db_path=tmp_db)
        db.mark_synced("cat123", "P", "added", beatport_track_id=42, db_path=tmp_db)
        import sqlite3
        con = sqlite3.connect(str(tmp_db))
        rows = con.execute("SELECT outcome FROM synced_tracks WHERE catalog_id = ?", ("cat123",)).fetchall()
        con.close()
        assert len(rows) == 1
        assert rows[0][0] == "added"


class TestSyncRuns:
    def test_start_and_finish(self, tmp_db):
        run_id = db.start_sync_run("My Playlist", tmp_db)
        assert isinstance(run_id, int)
        db.finish_sync_run(run_id, 100, 80, 15, 5, "done", tmp_db)
        import sqlite3
        con = sqlite3.connect(str(tmp_db))
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,)).fetchone()
        con.close()
        assert row["tracks_added"] == 80
        assert row["tracks_seen"] == 100
        assert row["status"] == "done"


class TestTokenCache:
    def test_miss_on_empty(self, tmp_db):
        assert db.get_token("beatport", tmp_db) is None

    def test_round_trip(self, tmp_db):
        db.set_token("beatport", "Bearer xyz", db_path=tmp_db)
        assert db.get_token("beatport", tmp_db) == "Bearer xyz"

    def test_expired_token_returns_none(self, tmp_db):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        db.set_token("beatport", "Bearer old", expires_at=past, db_path=tmp_db)
        assert db.get_token("beatport", tmp_db) is None

    def test_valid_expiry_returns_token(self, tmp_db):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        db.set_token("beatport", "Bearer fresh", expires_at=future, db_path=tmp_db)
        assert db.get_token("beatport", tmp_db) == "Bearer fresh"

    def test_no_expiry_always_valid(self, tmp_db):
        db.set_token("beatport", "Bearer forever", db_path=tmp_db)
        assert db.get_token("beatport", tmp_db) == "Bearer forever"
