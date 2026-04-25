"""
Probe the delete-from-playlist endpoint by triggering UI removal.
"""
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "delete_capture.jsonl"
SHOTS = ROOT / "logs" / "screenshots"

USERNAME = os.environ["BEATPORT_USERNAME"]
PASSWORD = os.environ["BEATPORT_PASSWORD"]


def write(record):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


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
            if "api.beatport.com" not in req.url:
                return
            try:
                body = req.post_data
            except Exception:
                body = None
            write({"kind": "req", "method": req.method, "url": req.url, "body": body})

        async def on_response(resp):
            if "api.beatport.com" not in resp.url:
                return
            preview = None
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    preview = (await resp.text())[:3000]
            except Exception:
                pass
            write({"kind": "resp", "status": resp.status,
                   "method": resp.request.method,
                   "url": resp.url, "body": preview})

        page.on("request", lambda r: asyncio.create_task(on_request(r)))
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        # Login
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
        await page.wait_for_timeout(3000)

        # Navigate to the Dance playlist (where we added test tracks)
        await page.goto(
            "https://www.beatport.com/library/playlists/7241436",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        await page.wait_for_timeout(4000)
        await page.screenshot(path=str(SHOTS / "60_dance_playlist.png"), full_page=True)

        # Find row close-x buttons (icon-close-x)
        close_btns = page.locator("[data-testid='icon-close-x']")
        n = await close_btns.count()
        print(f"[probe] {n} close-x buttons on page")

        # The first close-x is the modal close (no modal here), so the row ones
        # are likely all of them. Click the first row remove.
        # We need to click the parent button, not the SVG.
        if n > 0:
            # Click the LAST one (bottom of list = our recently added duplicate)
            target_btn = close_btns.nth(n - 1)
            try:
                # Locate parent button
                parent = target_btn.locator("xpath=ancestor::button[1]")
                await parent.scroll_into_view_if_needed()
                await parent.hover()
                await page.wait_for_timeout(500)
                await parent.click(force=True)
                print("[probe] clicked remove on last row")
                await page.wait_for_timeout(3000)
                await page.screenshot(path=str(SHOTS / "61_after_remove.png"), full_page=True)
            except Exception as e:
                print(f"[probe] remove failed: {e}")

        # Look for a confirm dialog
        try:
            for label in ["Remove", "Delete", "Confirm", "Yes"]:
                btn = page.get_by_role("button", name=label, exact=True).first
                if await btn.count():
                    await btn.click(timeout=2000, force=True)
                    print(f"[probe] confirmed via '{label}'")
                    break
        except Exception:
            pass

        await page.wait_for_timeout(3000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
