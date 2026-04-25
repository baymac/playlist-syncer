"""
Beatport playlist classifier — executor (API direct).

Logs in once via headless Playwright to capture an OAuth Bearer token, then
drives the api.beatport.com REST surface directly with httpx. Pages through
the source playlist, classifies each track by genre, and adds it to the
destination playlist via the bulk endpoint, deduping against each
destination's existing track-id set.

Usage:
  uv run python scripts/automate.py --limit 3        # dry run on first 3 candidates
  uv run python scripts/automate.py                   # full run
  uv run python scripts/automate.py --cleanup         # remove probe pollution

Env: BEATPORT_USERNAME, BEATPORT_PASSWORD
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / "run_log.jsonl"
STATE_DIR = ROOT / "state"
PROCESSED_PATH = STATE_DIR / "processed_track_ids.txt"
DEST_TRACKS_CACHE = STATE_DIR / "destination_track_ids.json"

USERNAME = os.environ["BEATPORT_USERNAME"]
PASSWORD = os.environ["BEATPORT_PASSWORD"]

LIBRARY_SONGS_ID = 7241393
API_ROOT = "https://api.beatport.com/v4"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Classifier (mirrors references/classifier.md)
EXACT = {
    "dance/pop": "Dance",
    "melodic house & techno": "Melodic House",
    "house": "House",
    "tech house": "Tech House",
    "indie dance": "Indie Dance",
    "drum & bass": "DnB",
    "techno (raw / deep / hypnotic)": "Hypnotic Techno",
    "techno (peak time / driving)": "Peak Techno",
    "downtempo": "Downtempo",
    "progressive house": "Progressive House",
    "bass house": "Bass House",
    "afro house": "Afro House",
    "deep house": "Deep House",
    "hard techno": "Hard Techno",
    "ambient": "Ambient",
    "electronica": "Electronica",
}
CONTAINS = [
    ("dubstep", "Dubstep"),
    ("mainstage", "Mainstage"),
    ("minimal", "Minimal"),
    ("trance", "Trance"),
]


def normalize_genre(g: str) -> str:
    g = g.strip().lower()
    g = re.sub(r"\s*/\s*", "/", g)
    g = re.sub(r"\s+", " ", g)
    return g


def classify(genre: str | None) -> str | None:
    if not genre:
        return None
    g = normalize_genre(genre)
    if g in EXACT:
        return EXACT[g]
    for needle, dest in CONTAINS:
        if needle in g:
            return dest
    return None


# ---------- Logging ----------

def log(record: dict) -> None:
    record["ts"] = time.time()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    line = json.dumps({k: v for k, v in record.items() if k != "ts"})
    print(line)


# ---------- Auth ----------

async def capture_token() -> str:
    """Headless login → capture first user-scoped Bearer on api.beatport.com."""
    captured: dict[str, str | None] = {"token": None}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()

        def grab(req):
            auth = req.headers.get("authorization", "")
            if "api.beatport.com" in req.url and auth.startswith("Bearer "):
                # Prefer the user-scoped token (post-login). Anonymous tokens
                # are also Bearer; we keep overwriting so the last one wins.
                captured["token"] = auth

        page.on("request", grab)

        await page.goto("https://www.beatport.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        try:
            await page.locator("#onetrust-accept-btn-handler").click(timeout=2000)
        except Exception:
            pass
        await page.get_by_role("link", name="Login").or_(
            page.get_by_role("button", name="Login")
        ).first.click()
        await page.wait_for_url(lambda u: "account.beatport.com" in u, timeout=20_000)
        await page.fill("input[name='username']", USERNAME)
        await page.fill("input[name='password']", PASSWORD)
        await page.click("button[type='submit']")
        await page.wait_for_url(
            lambda u: "beatport.com/" in u and "account.beatport.com" not in u,
            timeout=20_000,
        )
        await page.wait_for_timeout(2500)
        # Trigger an authed call so we capture a user-scoped token (not anon)
        await page.goto(
            f"https://www.beatport.com/library/playlists/{LIBRARY_SONGS_ID}",
            wait_until="domcontentloaded",
        )
        await page.wait_for_timeout(3000)
        await browser.close()

    if not captured["token"]:
        sys.exit("could not capture Bearer token")
    return captured["token"]


def is_user_scoped(token: str) -> bool:
    """Decode JWT payload (no signature check) and look for user:dj scope."""
    try:
        import base64
        payload = token.split()[-1].split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        return "user:anon" not in decoded.get("scope", "")
    except Exception:
        return True  # don't block on parse errors


# ---------- API client ----------

@dataclass
class Beatport:
    client: httpx.Client
    on_401: callable = None  # called to refresh the token

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        r = self.client.request(method, url, **kw)
        if r.status_code == 401 and self.on_401:
            self.on_401()
            r = self.client.request(method, url, **kw)
        r.raise_for_status()
        return r

    def list_my_playlists(self) -> list[dict]:
        out = []
        page = 1
        while True:
            data = self._request(
                "GET", f"{API_ROOT}/my/playlists/?page={page}&per_page=50"
            ).json()
            out.extend(data["results"])
            if not data.get("next"):
                break
            page += 1
        return out

    def list_track_ids(self, playlist_id: int) -> set[int]:
        data = self._request(
            "GET", f"{API_ROOT}/my/playlists/{playlist_id}/tracks/ids/"
        ).json()
        if "results" in data:
            return {item.get("track_id") or item.get("id") for item in data["results"]}
        if "track_ids" in data:
            return set(data["track_ids"])
        return self._list_track_ids_full(playlist_id)

    def _list_track_ids_full(self, playlist_id: int) -> set[int]:
        ids: set[int] = set()
        page = 1
        while True:
            data = self._request(
                "GET",
                f"{API_ROOT}/my/playlists/{playlist_id}/tracks/?page={page}&per_page=100",
            ).json()
            for entry in data["results"]:
                tid = entry.get("track_id") or entry.get("track", {}).get("id")
                if tid:
                    ids.add(tid)
            if not data.get("next"):
                break
            page += 1
        return ids

    def iter_source_tracks(self, playlist_id: int, per_page: int = 100):
        page = 1
        while True:
            data = self._request(
                "GET",
                f"{API_ROOT}/my/playlists/{playlist_id}/tracks/"
                f"?page={page}&per_page={per_page}",
            ).json()
            for entry in data["results"]:
                if entry.get("tombstoned"):
                    continue
                tr = entry.get("track", {})
                tid = tr.get("id") or entry.get("track_id")
                genre = tr.get("genre", {}).get("name") if tr.get("genre") else None
                artists = ", ".join(a["name"] for a in tr.get("artists", []))
                label = f"{tr.get('name','?')} ({tr.get('mix_name','')}) by {artists}"
                yield (entry["id"], tid, genre, label.strip())
            if not data.get("next"):
                break
            page += 1

    def add_track(self, dest_id: int, track_id: int) -> dict:
        return self._request(
            "POST",
            f"{API_ROOT}/my/playlists/{dest_id}/tracks/bulk/",
            json={"track_ids": [track_id]},
        ).json()

    def delete_entry(self, dest_id: int, entry_id: int) -> dict:
        return self._request(
            "DELETE",
            f"{API_ROOT}/my/playlists/{dest_id}/tracks/bulk/",
            json={"item_ids": [entry_id]},
        ).json()


def make_client(token: str) -> httpx.Client:
    return httpx.Client(
        timeout=30,
        headers={
            "authorization": token,
            "content-type": "application/json",
            "accept": "application/json, text/plain, */*",
            "user-agent": USER_AGENT,
            "origin": "https://www.beatport.com",
            "referer": "https://www.beatport.com/",
        },
    )


# ---------- Persistence ----------

def load_processed() -> set[int]:
    if PROCESSED_PATH.exists():
        return {int(x) for x in PROCESSED_PATH.read_text().splitlines() if x.strip()}
    return set()


def mark_processed(track_id: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with PROCESSED_PATH.open("a") as f:
        f.write(f"{track_id}\n")


def load_dest_cache() -> dict[str, set[int]]:
    if DEST_TRACKS_CACHE.exists():
        raw = json.loads(DEST_TRACKS_CACHE.read_text())
        return {k: set(v) for k, v in raw.items()}
    return {}


def save_dest_cache(cache: dict[str, set[int]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DEST_TRACKS_CACHE.write_text(
        json.dumps({k: sorted(v) for k, v in cache.items()}, indent=2)
    )


# ---------- Run ----------

def resolve_destinations(bp: Beatport) -> dict[str, int]:
    """Map every classifier output name → playlist ID. Bail if any are missing."""
    needed = set(EXACT.values()) | {dest for _, dest in CONTAINS}
    playlists = bp.list_my_playlists()
    name_to_id = {pl["name"]: pl["id"] for pl in playlists}
    out = {}
    missing = []
    for name in needed:
        if name in name_to_id:
            out[name] = name_to_id[name]
        else:
            missing.append(name)
    if missing:
        sys.exit(f"missing destination playlists: {missing}")
    return out


def login_until_user_scoped() -> str:
    token = asyncio.run(capture_token())
    if not is_user_scoped(token):
        log({"event": "warn_anon_token"})
        token = asyncio.run(capture_token())
    return token


def run(limit: int | None) -> None:
    log({"event": "start", "limit": limit})

    token = login_until_user_scoped()
    log({"event": "token_captured", "user_scoped": is_user_scoped(token)})

    with make_client(token) as client:
        bp = Beatport(client)

        def refresh_token():
            new_token = login_until_user_scoped()
            client.headers["authorization"] = new_token
            log({"event": "token_refreshed"})

        bp.on_401 = refresh_token
        destinations = resolve_destinations(bp)
        log({"event": "destinations_resolved", "count": len(destinations)})

        # Build dest → existing track-id set (for dedup)
        dest_track_ids: dict[str, set[int]] = {}
        for name, pl_id in destinations.items():
            dest_track_ids[name] = bp.list_track_ids(pl_id)
        save_dest_cache(dest_track_ids)
        log({"event": "dest_cache_built",
             "sizes": {k: len(v) for k, v in dest_track_ids.items()}})

        processed = load_processed()
        added = duplicate = skipped_no_match = skipped_no_genre = errors = 0
        actions_done = 0

        for entry_id, track_id, genre, label in bp.iter_source_tracks(LIBRARY_SONGS_ID):
            if track_id is None:
                log({"event": "track", "outcome": "skipped_missing_id", "entry_id": entry_id})
                continue
            if track_id in processed:
                continue

            if not genre:
                log({"event": "track", "track_id": track_id, "label": label,
                     "outcome": "skipped_no_genre"})
                mark_processed(track_id)
                processed.add(track_id)
                skipped_no_genre += 1
                continue

            dest_name = classify(genre)
            if not dest_name:
                log({"event": "track", "track_id": track_id, "label": label,
                     "genre": genre, "outcome": "skipped_no_match"})
                mark_processed(track_id)
                processed.add(track_id)
                skipped_no_match += 1
                continue

            dest_id = destinations[dest_name]
            if track_id in dest_track_ids[dest_name]:
                log({"event": "track", "track_id": track_id, "label": label,
                     "genre": genre, "destination": dest_name,
                     "outcome": "duplicate"})
                mark_processed(track_id)
                processed.add(track_id)
                duplicate += 1
                continue

            # Live add. Honour the dry-run limit on actually-acted-upon tracks.
            if limit is not None and actions_done >= limit:
                log({"event": "limit_reached", "limit": limit})
                break

            try:
                resp = bp.add_track(dest_id, track_id)
                items = resp.get("items") or []
                if not items:
                    log({"event": "track", "track_id": track_id, "label": label,
                         "genre": genre, "destination": dest_name,
                         "outcome": "noop_empty_items",
                         "playlist_track_count": resp.get("playlist", {}).get("track_count")})
                    mark_processed(track_id)
                    processed.add(track_id)
                    errors += 1
                    continue
                new_entry_id = items[0]["id"]
                dest_track_ids[dest_name].add(track_id)
                log({"event": "track", "track_id": track_id, "label": label,
                     "genre": genre, "destination": dest_name, "dest_id": dest_id,
                     "entry_id": new_entry_id, "outcome": "added"})
                added += 1
                actions_done += 1
            except httpx.HTTPStatusError as e:
                log({"event": "track", "track_id": track_id, "label": label,
                     "genre": genre, "destination": dest_name,
                     "outcome": "error",
                     "status": e.response.status_code,
                     "body": e.response.text[:300]})
                errors += 1
                continue

            mark_processed(track_id)
            processed.add(track_id)

            # Polite jitter
            time.sleep(random.uniform(0.3, 0.8))

        save_dest_cache(dest_track_ids)
        log({"event": "done", "added": added, "duplicate": duplicate,
             "skipped_no_match": skipped_no_match,
             "skipped_no_genre": skipped_no_genre, "errors": errors})


# ---------- Cleanup of probe pollution ----------

POLLUTION = [
    # (playlist_id, entry_id, track_id, note)
    (7241436, 371914190, 16282207, "Dance: 'abcdefu' duplicate"),
    (7241416, 371913825, 13257248, "Trance: 'About Us' (actually Melodic House)"),
]


def cleanup() -> None:
    log({"event": "cleanup_start"})
    token = login_until_user_scoped()
    with make_client(token) as client:
        bp = Beatport(client)
        bp.on_401 = lambda: client.headers.update(
            {"authorization": login_until_user_scoped()}
        )
        for pl_id, entry_id, _track_id, note in POLLUTION:
            try:
                bp.delete_entry(pl_id, entry_id)
                log({"event": "cleanup", "outcome": "deleted",
                     "playlist_id": pl_id, "entry_id": entry_id, "note": note})
            except httpx.HTTPStatusError as e:
                log({"event": "cleanup", "outcome": "error",
                     "playlist_id": pl_id, "entry_id": entry_id,
                     "status": e.response.status_code, "body": e.response.text[:300]})


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="dry run: only act on N tracks")
    ap.add_argument("--cleanup", action="store_true",
                    help="remove probe-pollution entries and exit")
    args = ap.parse_args()

    if args.cleanup:
        cleanup()
        return

    run(args.limit)


if __name__ == "__main__":
    main()
