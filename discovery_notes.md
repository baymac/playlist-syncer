# Discovery notes — Beatport playlist classifier

**Verdict: API direct (option c).** The browser is only needed to obtain a
short-lived OAuth Bearer token; everything after login is plain JSON over
HTTPS to `api.beatport.com/v4/`. No selectors, no scrolling, no UI race conditions.

## Auth

Beatport's web app is Next.js + NextAuth wrapping their own OAuth identity
service. The login flow:

1. `GET https://www.beatport.com/` — seeds session/CSRF cookies
2. Click `Login` link → redirects to
   `https://account.beatport.com/?next=/o/authorize/?...&client_id=eHToND3lsv1Xdpa645DdF4wwBUceBniuKPT2dUB1...&redirect_uri=https://www.beatport.com/api/auth/callback/beatport&code_challenge=...&code_challenge_method=S256`
3. Form fields: `input[name="username"]`, `input[name="password"]`, submit `button[type="submit"]`
4. Behind the scenes the form `POST`s `https://account.beatport.com/identity/v1/login/`
   with `{"username":"...","password":"..."}` (JSON), then redirects back to
   `/api/auth/callback/beatport?code=...` and lands on `https://www.beatport.com/`.
5. The web app then mints a JWT for `api.beatport.com` calls. Every API call
   carries `Authorization: Bearer <jwt>`. Token TTL is ~1 hour (exp - iat = 3600s).
   Anonymous tokens have `scope: app:prostore user:anon`; logged-in tokens have
   `user:dj openid user:bs_sunset` (plus `app:prostore`).

**Strategy in the executor**: launch headless Playwright once per run, perform
the login, intercept the first `Authorization: Bearer ...` header on
`api.beatport.com`, capture it, then close the browser. All subsequent work
goes through plain `httpx` with the captured token.

If the token expires mid-run (1h is plenty for ~4k tracks at our pace, but
just in case), the executor will re-run the Playwright login routine.

There's also a **OneTrust cookie banner** that intercepts clicks — dismiss
with `#onetrust-accept-btn-handler` early in the login flow.

## Source playlist

**"Library Songs"**, ID `7241393`, URL
`https://www.beatport.com/library/playlists/7241393`.

Total tracks at probe time: **4054** (`count` field).

The playlist is **paginated, not infinite scroll**: the API serves
`?page=N&per_page=100` (max per_page tested was 100; we'll stick with 100).
There are 41 pages.

```
GET https://api.beatport.com/v4/my/playlists/7241393/tracks/?page=1&per_page=100
→ {"count":4054,"page":"1/41","per_page":100,"results":[{...}, ...]}
```

There's also a sibling `GET .../tracks/ids/` that returns just the IDs;
not strictly needed since the full track endpoint includes them.

## Track payload schema

Each `results[i]` entry has:
- `id` — the **playlist entry ID** (used by DELETE)
- `position` — 1-based ordering in the playlist
- `track_id` (top-level) — sometimes; otherwise see `track.id`
- `track.id` — the **catalog track ID** (used by ADD)
- `track.genre.name` — the genre string we classify on (e.g. `"Dance / Pop"`,
  `"Melodic House & Techno"`, `"Trance (Main Floor)"`, `"Techno (Peak Time / Driving)"`)
- `track.genre.slug` — kebab-case version
- `track.name`, `track.mix_name`, `track.artists[*].name` — for logging

**Genre normalization**: Beatport renders `Dance / Pop` (with spaces around
the slash) but the classifier key is `dance/pop`. Normalize with
`re.sub(r'\s*/\s*', '/', g.strip().lower())` before exact-match lookup.

The fuzzy contains rules (`trance`, `dubstep`, `mainstage`, `minimal`)
already handle the parenthesized sub-genre variants like `Trance (Main Floor)`
and `Techno (Raw / Deep / Hypnotic)`. **Important**: keep the SKILL's order —
exact matches first, then contains. Otherwise `"Hard Techno"` would prematurely
match a generic `"techno"` rule. Our current contains list doesn't have a
generic "techno" entry, so we're safe, but keep the discipline.

## Destination playlists (genre → ID)

All destination playlists exist on the user's account and were enumerated
from the sidebar links during discovery:

| Classifier output | Playlist ID |
|-------------------|-------------|
| Dance             | 7241436     |
| Melodic House     | 7241417     |
| House             | 7241428     |
| Tech House        | 7241432     |
| Indie Dance       | 7241426     |
| DnB               | 7241422     |
| Hypnotic Techno   | 7241670     |
| Peak Techno       | 7241429     |
| Downtempo         | 7241433     |
| Progressive House | 7241431     |
| Bass House        | 7241427     |
| Afro House        | 7241418     |
| Deep House        | 7241430     |
| Hard Techno       | 7241724     |
| Ambient           | 7241406     |
| Electronica       | 7241405     |
| Dubstep           | 7241720     |
| Mainstage         | 7241409     |
| Minimal           | 7241425     |
| Trance            | 7241416     |

Resolve dynamically at startup by listing
`GET https://api.beatport.com/v4/my/playlists/?page=1&per_page=50`
and matching by `name` (case-sensitive). This way if the user renames a
playlist the script doesn't silently route to a stale ID.

## Add-to-playlist

```
POST https://api.beatport.com/v4/my/playlists/{playlist_id}/tracks/bulk/
content-type: application/json
authorization: Bearer <jwt>

{"track_ids": [<track_id>]}
```

Response 200, body:
```json
{
  "items": [{"id": <new_entry_id>, "position": N, "track_id": <track_id>, "track_available": true}],
  "playlist": {...updated metadata...}
}
```

The endpoint accepts multiple track_ids in one call — useful for batching
all tracks bound for the same destination, but per-track calls are simpler
and let us log per-track outcomes cleanly.

## Duplicate behavior — IMPORTANT

**The API does not deduplicate.** The "Duplicate Tracks Detected" modal in
the UI is enforced client-side only. Replaying the same `(playlist_id, track_id)`
add succeeds with 200 and creates a second entry. Verified twice during probe.

**Mitigation**: before each run, fetch the existing track-id set for every
destination playlist via:
```
GET https://api.beatport.com/v4/my/playlists/{playlist_id}/tracks/ids/
```
…and skip any source track whose `track.id` is already in that destination's
set. Cache it locally (`state/destination_tracks.json`) and refresh on demand.

## Remove from playlist

```
DELETE https://api.beatport.com/v4/my/playlists/{playlist_id}/tracks/bulk/
content-type: application/json
authorization: Bearer <jwt>

{"item_ids": [<playlist_entry_id>]}
```
Note: `item_ids` (entry IDs from the `results[i].id` field), **not**
`track_ids`. We don't need this for the main flow but use it for cleanup
of probe pollution.

## Probe pollution — to clean up

Test additions made during discovery that need removal:
- Dance (7241436) — entry id `371914190`, track `16282207` ("abcdefu" by Fat Tony et al.)
- Trance (7241416) — entry id `371913825`, track `13257248` ("About Us" by Emme & Le Youth — actually Melodic House, will be re-added there by classifier)

(Entry `371914193` in Dance was already removed during the delete probe.)

## Timings (observed)

These are mostly irrelevant for the API path, but for reference:
- Headless login full flow: ~12s end-to-end
- `GET /tracks/?page=N`: 200–500ms
- `POST .../tracks/bulk/`: 200–400ms
- `DELETE .../tracks/bulk/`: 200–400ms

## Rate limiting

No rate limit observed during probes (~10 calls in a few seconds).
The SKILL recommends 300–800ms randomized between writes; we'll follow that
to be polite, even though direct API calls don't pile up the way UI clicks
might. Total expected wall time for ~4k tracks at 600ms cadence: ~40 minutes.

## Failure modes still to handle in executor

- **Token expiry mid-run**: re-login, re-capture, retry the failing call.
  Detect via 401 response.
- **Track unavailable / region-locked**: the existing UI shows greyed-out
  rows. The `track.is_available_for_streaming` field is per-region; we don't
  care for routing purposes. The `track.publish_status` and the `tombstoned`
  flag at the entry level may matter — skip if `tombstoned: true`.
- **Genre missing or null**: skip, log `skipped: no_genre`.
- **Genre not in classifier**: skip, log `skipped: no_match: <genre>`.
- **Destination playlist ID stale**: refuse to start; bail with clear error.
- **5xx on add**: retry once with backoff, then log + skip.

## Out of scope for this run

- **Obscura / stealth**: not needed. The endpoints are user-API endpoints
  the web app itself hits; no bot-detection beyond the standard Cloudflare
  JS challenge that the Playwright login already passes.
- **Removing `tombstoned` tracks**: not in the spec.
- **Re-shuffling existing destination playlists**: out of scope.
