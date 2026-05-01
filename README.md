# playlist-syncer

CLI tool to sync music tracks to Beatport genre playlists from two sources:

1. **Apple Music** — reads your library/playlists via MusicKit, fuzzy-matches each track on Beatport, classifies by genre, and adds to destination playlists.
2. **track-detect DB** — reads tracks detected from posts (via [track-detect](https://github.com/baymac/track-detect/)), searches Beatport, and adds matches to playlists.

## Requirements

- macOS (uses MusicKit via a Swift bridge)
- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/) for environment management
- A Beatport account

## Setup

```bash
uv sync
uv run playwright install chromium
export BEATPORT_USERNAME='you@example.com'
export BEATPORT_PASSWORD='…'
```

The first run will prompt the macOS Music app to grant MusicKit access.

## Usage

### Apple Music → Beatport (`music-beatport-sync`)

```bash
# Verify both connections
uv run playlist-syncer music-beatport-sync check-connections

# List Apple Music playlists
uv run playlist-syncer music-beatport-sync list-playlists

# Sync a single playlist (mirrors into a Beatport playlist of the same name —
# no genre classification, just direct track-for-track copy of matches)
uv run playlist-syncer music-beatport-sync sync --playlist "My Playlist"

# Sync all songs added to your library (cursor-aware — only new songs after the last run)
uv run playlist-syncer music-beatport-sync sync --library

# Sync your "Favourite Songs" playlist
uv run playlist-syncer music-beatport-sync sync --favorites

# Library + Favourites union
uv run playlist-syncer music-beatport-sync sync --library-and-favorites

# Everything in your music library (no filter)
uv run playlist-syncer music-beatport-sync sync --all

# Dry-run any of the above
uv run playlist-syncer music-beatport-sync sync --library --dry-run
```

### track-detect → Beatport (`detect-beatport-sync`)

```bash
# Sync tracks from a track-detect SQLite database
uv run playlist-syncer detect-beatport-sync sync --db /path/to/detect.db

# Into a specific playlist instead of genre classification (accepts name or numeric ID)
uv run playlist-syncer detect-beatport-sync sync --db /path/to/detect.db --playlist "Detected"
uv run playlist-syncer detect-beatport-sync sync --db /path/to/detect.db --playlist 12345

# Dry-run
uv run playlist-syncer detect-beatport-sync sync --db /path/to/detect.db --dry-run
```

The source track-detect DB is **never modified**. Sync state is tracked in `~/.playlist-syncer/detect_sync.db` for both genre and playlist modes — each track's outcome is recorded after the first run so only newly added tracks are processed on subsequent runs. State is keyed per destination (genre mode and each named/ID playlist are independent). No-match outcomes (no results, fuzzy miss, unclassifiable genre) are terminal — not retried. Only Beatport API errors are retried. Check the run log for fuzzy misses to review manually.

Useful flags (both commands): `--limit N`, `--verbose`, `--threshold 0.72`.

## Layout

```
playlist_syncer/    # main package
  cli.py            # Click commands
  sync.py           # sync loops (Apple Music and track-detect)
  api.py            # Beatport HTTP client + Playwright token capture
  matching.py       # fuzzy title/artist matching
  classifier.py     # Beatport genre → destination playlist
  musickit.py       # Swift bridge wrapper
  db.py             # SQLite persistence
  bridge/
    musickit_bridge.swift   # compiled on first run, cached in ~/.cache/playlist-syncer

helpers/            # one-off utilities (Apple Music: export/backup/restore; Beatport: delete track)
tests/              # pytest suite
```

## Run tests

```bash
uv sync --extra dev
uv run pytest
```

## State

All persistent state lives under `~/.playlist-syncer/` and survives across workspace changes:

| Path | Contents |
|---|---|
| `~/.playlist-syncer/sync.db` | Apple Music sync state — synced tracks, run history, library cursor, Beatport token cache |
| `~/.playlist-syncer/detect_sync.db` | track-detect sync state — synced tracks, run history |
| `~/.playlist-syncer/browser-profile/` | Persistent Chromium profile used for token capture (keeps Cloudflare clearance cookies) |
| `~/.playlist-syncer/logs/YYYY-MM-DD_apple-music-sync_N.log` | Per-run logs for Apple Music syncs |
| `~/.playlist-syncer/logs/YYYY-MM-DD_detect-db-sync_N.log` | Per-run logs for track-detect syncs |
| `~/.playlist-syncer/apple_music_export.csv` | Apple Music library export (from helpers) |

## Beatport token & Cloudflare troubleshooting

The tool captures a Beatport Bearer token by driving a headless Chromium browser via Playwright. The token is cached in `sync.db` and reused across runs. When it expires the browser re-logs in automatically.

**How token capture works**

1. Chromium opens `www.beatport.com` (Cloudflare clearance obtained).
2. Navigates to `account.beatport.com/settings` and fills in `BEATPORT_USERNAME` / `BEATPORT_PASSWORD`.
3. After login, navigates to `www.beatport.com/library/playlists` — the API request from that page carries the Bearer token, which the tool intercepts and caches.

The Chromium session is stored in `~/.playlist-syncer/browser-profile/` so Cloudflare cookies persist between re-auth runs.

**When Cloudflare blocks the headless browser**

The tool will print a clear message and exit — no crash, no traceback. When you see it, follow these steps:

1. Open Brave → `www.beatport.com/library/playlists` (logged in)
2. DevTools → Network tab → click any `api.beatport.com` request → copy the `Authorization` header value
3. Run:
   ```bash
   uv run playlist-syncer set-token 'Bearer eyJ...'
   ```
4. Re-run your sync command immediately — the token expires in ~10 minutes.

The tool reprints these exact steps every time it's blocked, so you never need to remember them.

**Other causes of login failure**

- **Stale browser profile** — delete it and retry (Playwright will rebuild it):
  ```bash
  rm -rf ~/.playlist-syncer/browser-profile
  ```
- **IP temporarily flagged** — wait a few minutes or switch networks, then retry.
- **Wrong credentials** — check `BEATPORT_USERNAME` / `BEATPORT_PASSWORD` are set, then run `check-connections`.
- **Beatport login page changed** — Playwright is filling the wrong fields. Screenshots saved to `~/bp_login_debug/` on each failed attempt show what the browser actually sees.

**Preventing frequent re-auth**

**Preventing frequent re-auth**

The token has no explicit expiry set by Beatport — it's valid until revoked or until Beatport rotates keys. Logging out of Beatport in a real browser, changing your password, or Beatport pushing a new release can invalidate the cached token. If re-auth fails repeatedly, delete the cached token from the DB:

```bash
sqlite3 ~/.playlist-syncer/sync.db "DELETE FROM auth_cache WHERE service='beatport';"
```

Then run `check-connections` to trigger a fresh login interactively before the next sync.
