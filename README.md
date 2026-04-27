# beatport-sync

CLI tool to migrate Apple Music library and playlists to Beatport genre playlists.

Reads songs from Apple Music via MusicKit, searches Beatport for each track, fuzzy-matches title + artist, classifies by Beatport genre, and adds the track to a destination playlist (auto-creating it if missing).

## Requirements

- macOS (uses MusicKit via a Swift bridge)
- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/) for environment management
- A Beatport account
- Apple Music with the songs you want to migrate

## Setup

```bash
uv sync
uv run playwright install chromium
export BEATPORT_USERNAME='you@example.com'
export BEATPORT_PASSWORD='…'
```

The first run will prompt the macOS Music app to grant MusicKit access.

## Usage

```bash
# Verify both connections
uv run beatport-sync check-connections

# List Apple Music playlists
uv run beatport-sync list-playlists

# Sync a single playlist (mirrors into a Beatport playlist of the same name —
# no genre classification, just direct track-for-track copy of matches)
uv run beatport-sync sync --playlist "My Playlist"

# Sync all songs added to your library (cursor-aware — only new songs after the last run)
uv run beatport-sync sync --library

# Sync your "Favourite Songs" playlist
uv run beatport-sync sync --favorites

# Library + Favourites union
uv run beatport-sync sync --library-and-favorites

# Everything in your music library (no filter)
uv run beatport-sync sync --all

# Dry-run any of the above
uv run beatport-sync sync --library --dry-run
```

Useful flags: `--limit N`, `--verbose`, `--threshold 0.72`.

## Layout

```
beatport_sync/      # main package
  cli.py            # Click commands
  sync.py           # the sync loop
  api.py            # Beatport HTTP client + Playwright token capture
  matching.py       # fuzzy title/artist matching
  classifier.py     # Beatport genre → destination playlist
  musickit.py       # Swift bridge wrapper
  db.py             # SQLite persistence (synced tracks, run history, cursor, token cache)
  bridge/
    musickit_bridge.swift   # compiled on first run, cached in ~/.cache/beatport-sync

helpers/            # one-off Apple Music utilities (export to CSV, backup, restore, clear)
tests/              # pytest suite
state/              # local DB, NDJSON cache, run logs (gitignored)
docs/               # project notes and writeup
```

## Run tests

```bash
uv run pytest
```

## State

All persistent state lives under `state/`:

- `state/sync.db` — SQLite (synced tracks, run history, library cursor, Beatport token cache)
- `state/musickit_library.ndjson` — NDJSON snapshot of your Apple Music library
- `state/logs/run_*.log` — per-run logs (one per `sync` invocation)
