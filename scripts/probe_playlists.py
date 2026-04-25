"""
Playlist API probe — phase 0c.

Reuses .context/auth_state.json. Navigates to the playlists area and
captures every api.beatport.com call. Goal: find the endpoints for
  - listing user playlists (need ID for "Library songs" + each destination)
  - listing tracks in a playlist (with genre)
  - adding a track to a playlist
"""
import asyncio
import json
import os
import re
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "playlist_capture.jsonl"
SHOTS = ROOT / "logs" / "screenshots"
AUTH_STATE = ROOT / ".context" / "auth_state.json"


def write(record):
    with LOG.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def is_target(url: str) -> bool:
    return "api.beatport.com" in url or "/api/auth" in url or "/_next/data" in url


async def main():
    LOG.unlink(missing_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            storage_state=str(AUTH_STATE),
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()

        async def on_request(req):
            if not is_target(req.url):
                return
            try:
                body = req.post_data
            except Exception:
                body = None
            write({"kind": "req", "method": req.method, "url": req.url, "body": body,
                   "auth": req.headers.get("authorization", "")[:120]})

        async def on_response(resp):
            if not is_target(resp.url):
                return
            preview = None
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    preview = (await resp.text())[:6000]
            except Exception as e:
                preview = f"<err: {e}>"
            write({"kind": "resp", "status": resp.status, "url": resp.url, "body": preview})

        page.on("request", lambda r: asyncio.create_task(on_request(r)))
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        # 1) Hit the "My Beatport" / collection area to discover playlist API
        print("[probe] /my/playlists")
        await page.goto("https://www.beatport.com/my/playlists",
                        wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(SHOTS / "20_my_playlists.png"), full_page=True)
        (ROOT / "logs" / "my_playlists.html").write_text(await page.content())

        # 2) Try collection page too
        print("[probe] /my/collection")
        await page.goto("https://www.beatport.com/my/collection",
                        wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=str(SHOTS / "21_collection.png"), full_page=True)

        # 3) Look for Library songs / library link in sidebar
        # Print all nav links visible
        links = await page.query_selector_all("a[href*='/library'], a[href*='/playlist'], a[href*='/my']")
        seen = set()
        for a in links:
            href = await a.get_attribute("href")
            text = (await a.inner_text()).strip()
            if href and href not in seen:
                seen.add(href)
                print(f"  link: {text[:40]:40s}  {href}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
