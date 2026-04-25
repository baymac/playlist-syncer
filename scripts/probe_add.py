"""
Action probe — find the add-to-playlist API endpoint by triggering it via UI.

Logs in, navigates to Library Songs, hovers a row, opens the overflow menu,
clicks 'Add to playlist', selects a destination, and confirms — capturing
every POST/PUT to api.beatport.com.
"""
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "add_capture.jsonl"
SHOTS = ROOT / "logs" / "screenshots"

USERNAME = os.environ["BEATPORT_USERNAME"]
PASSWORD = os.environ["BEATPORT_PASSWORD"]

LIBRARY_SONGS_ID = 7241393


def write(record):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def is_target(url: str) -> bool:
    # Capture ALL beatport calls so we don't miss alternate API hosts
    return "beatport.com" in url and not any(
        s in url for s in ["geo-media.beatport.com", "geo-samples.beatport.com",
                            ".jpg", ".png", ".svg", ".css", ".js", "/icons/", "/_next/static"]
    )


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
                   "auth": req.headers.get("authorization", "")[:160],
                   "ct": req.headers.get("content-type", "")})

        async def on_response(resp):
            if not is_target(resp.url):
                return
            preview = None
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    preview = (await resp.text())[:6000]
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

        # Dismiss OneTrust cookie banner if present
        for sel in [
            "button#onetrust-accept-btn-handler",
            "button#onetrust-reject-all-handler",
            "button[id*='onetrust'][id*='accept']",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count():
                    await btn.click(timeout=2000)
                    print(f"[probe] dismissed cookie banner: {sel}")
                    await page.wait_for_timeout(500)
                    break
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
        await page.wait_for_timeout(3000)

        # Navigate to Library Songs
        url = f"https://www.beatport.com/library/playlists/{LIBRARY_SONGS_ID}"
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(5000)

        # Click first row's add-to-playlist button (skip index 0 which is bulk)
        # Each row has data-testid="add-to-playlist-button"; first occurrence is the
        # bulk button at the top, subsequent ones are per-row.
        add_btns = page.locator("[data-testid='add-to-playlist-button']")
        n = await add_btns.count()
        print(f"[probe] {n} add-to-playlist buttons on page")

        # Click the SECOND one (index 1) — first per-row button, skipping the bulk one
        target = add_btns.nth(1)
        await target.scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(SHOTS / "50_before_add.png"), full_page=False)
        await target.click()
        await page.wait_for_timeout(2500)
        await page.screenshot(path=str(SHOTS / "51_modal.png"), full_page=True)

        # The modal should list playlists. Save its HTML so we can introspect.
        (ROOT / "logs" / "modal.html").write_text(await page.content())

        # Click "Trance" inside the modal scope. Using force=True to bypass the overlay.
        modal = page.locator("[role='dialog']")
        try:
            await modal.get_by_text("Trance", exact=True).first.click(
                timeout=5000, force=True
            )
            print("[probe] selected Trance row")
            await page.wait_for_timeout(800)
        except Exception as e:
            print(f"[probe] no 'Trance' in modal: {e}")

        await page.screenshot(path=str(SHOTS / "52_selected.png"), full_page=True)

        # Confirm — the confirm button is "Add to Playlist" inside the modal
        for label in ["Add to Playlist", "Add to playlist", "Confirm", "Save", "Add"]:
            try:
                btn = modal.get_by_role("button", name=label).first
                if await btn.count():
                    await btn.click(timeout=2000, force=True)
                    print(f"[probe] confirmed via '{label}'")
                    break
            except Exception:
                pass

        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(SHOTS / "53_after_add.png"), full_page=True)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
