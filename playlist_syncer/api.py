"""Beatport HTTP API client and token capture."""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx
from playwright.async_api import async_playwright

API_ROOT = "https://api.beatport.com/v4"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ---------- Auth ----------

async def _capture_token_async(username: str, password: str) -> str:
    captured: dict[str, Optional[str]] = {"token": None}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent=USER_AGENT, viewport={"width": 1440, "height": 900}
        )
        page = await context.new_page()

        def grab(req) -> None:
            auth = req.headers.get("authorization", "")
            if "api.beatport.com" in req.url and auth.startswith("Bearer "):
                captured["token"] = auth

        page.on("request", grab)
        await page.goto("https://www.beatport.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        try:
            await page.locator("#onetrust-accept-btn-handler").click(timeout=2000)
        except Exception:
            pass
        await (
            page.get_by_role("link", name="Login").or_(
                page.get_by_role("button", name="Login")
            ).first.click()
        )
        await page.wait_for_url(lambda u: "account.beatport.com" in u, timeout=20_000)
        await page.fill("input[name='username']", username)
        await page.fill("input[name='password']", password)
        await page.click("button[type='submit']")
        await page.wait_for_url(
            lambda u: "beatport.com/" in u and "account.beatport.com" not in u,
            timeout=20_000,
        )
        await page.wait_for_timeout(2500)
        await page.goto("https://www.beatport.com/library/playlists",
                        wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await browser.close()

    if not captured["token"]:
        raise RuntimeError(
            "Beatport login failed — could not capture Bearer token.\n"
            "Check BEATPORT_USERNAME and BEATPORT_PASSWORD env vars.\n"
            "If credentials are correct, Beatport's login page may have changed."
        )
    return captured["token"]


def _is_user_scoped(token: str) -> bool:
    try:
        payload = token.split()[-1].split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        return "user:anon" not in decoded.get("scope", "")
    except Exception:
        return True


def capture_token(username: str, password: str) -> str:
    """Headless login → user-scoped Bearer token. Retries once if anonymous token returned."""
    token = asyncio.run(_capture_token_async(username, password))
    if not _is_user_scoped(token):
        token = asyncio.run(_capture_token_async(username, password))
    return token


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


# ---------- API client ----------

@dataclass
class Beatport:
    client: httpx.Client
    on_401: Optional[Callable[[], None]] = field(default=None)

    def _request(self, method: str, url: str, **kw) -> httpx.Response:
        for attempt in range(6):
            r = self.client.request(method, url, **kw)
            if r.status_code == 429:
                if attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
            elif r.status_code == 401 and self.on_401 and attempt == 0:
                self.on_401()
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r  # unreachable

    def search_tracks(
        self, query: str, per_page: int = 5, debug: bool = False
    ) -> Optional[list[dict]]:
        """Search catalog.
        Returns list of track dicts (possibly empty), or None if request failed.
        Empty list = genuinely no results. None = request error (retry next run).
        """
        try:
            data = self._request(
                "GET",
                f"{API_ROOT}/catalog/search/",
                params={"q": query, "type": "tracks", "page": 1, "per_page": per_page},
            ).json()
            if isinstance(data, list):
                tracks = data
            else:
                tracks_raw = data.get("tracks", [])
                tracks = tracks_raw if isinstance(tracks_raw, list) else tracks_raw.get("data", [])
        except Exception as e:
            if debug:
                print(f"[search primary] {query!r}: {type(e).__name__}: {e}", file=sys.stderr)
            return None

        if tracks:
            return tracks

        try:
            data = self._request(
                "GET",
                f"{API_ROOT}/catalog/tracks/",
                params={"q": query, "page": 1, "per_page": per_page},
            ).json()
            if isinstance(data, list):
                return data
            return data.get("results", [])
        except Exception as e:
            if debug:
                print(f"[search fallback] {query!r}: {type(e).__name__}: {e}", file=sys.stderr)
            return None

    def list_my_playlists(self) -> list[dict]:
        out: list[dict] = []
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

    def create_playlist(self, name: str) -> dict:
        return self._request(
            "POST",
            f"{API_ROOT}/my/playlists/",
            json={"name": name},
        ).json()

    def list_track_ids(self, playlist_id: int) -> set[int]:
        try:
            data = self._request(
                "GET", f"{API_ROOT}/my/playlists/{playlist_id}/tracks/ids/"
            ).json()
            if "results" in data:
                return {item.get("track_id") or item.get("id") for item in data["results"]}
            if "track_ids" in data:
                return set(data["track_ids"])
        except Exception:
            pass
        return self._list_track_ids_paged(playlist_id)

    def _list_track_ids_paged(self, playlist_id: int) -> set[int]:
        ids: set[int] = set()
        page = 1
        while True:
            data = self._request(
                "GET",
                f"{API_ROOT}/my/playlists/{playlist_id}/tracks/"
                f"?page={page}&per_page=100",
            ).json()
            for entry in data["results"]:
                tid = entry.get("track_id") or entry.get("track", {}).get("id")
                if tid:
                    ids.add(tid)
            if not data.get("next"):
                break
            page += 1
        return ids

    def list_playlist_items(self, playlist_id: int) -> list[dict]:
        """Return raw playlist track entries, each containing item `id` and catalog `track_id`."""
        items: list[dict] = []
        page = 1
        while True:
            data = self._request(
                "GET",
                f"{API_ROOT}/my/playlists/{playlist_id}/tracks/",
                params={"page": page, "per_page": 100},
            ).json()
            items.extend(data.get("results", []))
            if not data.get("next"):
                break
            page += 1
        return items

    def add_track(self, dest_id: int, track_id: int) -> dict:
        return self._request(
            "POST",
            f"{API_ROOT}/my/playlists/{dest_id}/tracks/bulk/",
            json={"track_ids": [track_id]},
        ).json()

    def delete_track(self, playlist_id: int, track_id: int) -> None:
        """Remove a track from a playlist using its internal playlist item ID."""
        items = self.list_playlist_items(playlist_id)
        item_id: Optional[int] = None
        for item in items:
            catalog_id = item.get("track_id") or item.get("track", {}).get("id")
            if catalog_id == track_id:
                item_id = item.get("id")
                break

        if item_id is None:
            raise ValueError(
                f"Track {track_id} not found in playlist {playlist_id}."
            )

        self._request(
            "DELETE",
            f"{API_ROOT}/my/playlists/{playlist_id}/tracks/bulk/",
            json={"item_ids": [item_id]},
        )
