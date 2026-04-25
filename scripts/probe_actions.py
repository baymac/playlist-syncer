"""
Action probe — phase 0d.

Login, navigate to Library Songs, scroll a bit to trigger track loading,
then attempt an add-to-playlist UI action to capture the POST endpoint.
"""
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "actions_capture.jsonl"
SHOTS = ROOT / "logs" / "screenshots"

USERNAME = os.environ["BEATPORT_USERNAME"]
PASSWORD = os.environ["BEATPORT_PASSWORD"]

LIBRARY_SONGS_ID = 7241393


def write(record):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def is_target(url: str) -> bool:
    return "api.beatport.com" in url


async def main():
    LOG.unlink(missing_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
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
            write({"kind": "req", "method": req.method, "url": req.url,
                   "body": body,
                   "auth": req.headers.get("authorization", "")[:160]})

        async def on_response(resp):
            if not is_target(resp.url):
                return
            preview = None
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    preview = (await resp.text())[:10000]
            except Exception as e:
                preview = f"<err: {e}>"
            write({"kind": "resp", "status": resp.status,
                   "method": resp.request.method,
                   "url": resp.url, "body": preview})

        page.on("request", lambda r: asyncio.create_task(on_request(r)))
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        # Login
        await page.goto("https://www.beatport.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
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
        await page.wait_for_timeout(3000)
        print(f"[probe] logged in. URL: {page.url}")

        # Save fresh auth state
        await context.storage_state(path=str(ROOT / ".context" / "auth_state.json"))

        # Navigate to Library Songs
        url = f"https://www.beatport.com/library/playlists/{LIBRARY_SONGS_ID}"
        print(f"[probe] -> {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(SHOTS / "40_library_songs.png"), full_page=True)
        (ROOT / "logs" / "library_songs.html").write_text(await page.content())

        # Inspect the DOM — find a track row
        rows = await page.query_selector_all("[class*='Track'] , [class*='track-row'], [data-testid*='track']")
        print(f"[probe] found {len(rows)} track-ish rows")

        # Scroll a bit to capture pagination/scroll API
        for i in range(3):
            await page.mouse.wheel(0, 1500)
            await page.wait_for_timeout(1500)

        await page.screenshot(path=str(SHOTS / "41_after_scroll.png"), full_page=True)

        # Try clicking the first track's "more" / overflow menu to surface
        # add-to-playlist option. Look for buttons inside rows.
        buttons = await page.query_selector_all("button[aria-label*='ore'], button[aria-label*='add'], button[aria-label*='Add']")
        print(f"[probe] found {len(buttons)} candidate action buttons")
        for b in buttons[:5]:
            label = await b.get_attribute("aria-label")
            print(f"  btn: {label}")

        # Save pretty list of all unique URLs hit
        await page.wait_for_timeout(2000)
        await browser.close()
        print(f"[probe] capture: {LOG}")


if __name__ == "__main__":
    asyncio.run(main())
