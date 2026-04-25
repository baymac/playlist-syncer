"""
Full probe: login + explore + capture + dump.

Single session — login, then find Library Songs URL via sidebar inspection,
then capture every api.beatport.com call as we navigate it.
"""
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "full_capture.jsonl"
SHOTS = ROOT / "logs" / "screenshots"

USERNAME = os.environ["BEATPORT_USERNAME"]
PASSWORD = os.environ["BEATPORT_PASSWORD"]


def write(record):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def is_target(url: str) -> bool:
    return "api.beatport.com" in url or "/api/auth" in url


async def main():
    LOG.unlink(missing_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()

        async def on_response(resp):
            if not is_target(resp.url):
                return
            preview = None
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    preview = (await resp.text())[:8000]
            except Exception as e:
                preview = f"<err: {e}>"
            write({"kind": "resp", "status": resp.status,
                   "method": resp.request.method,
                   "url": resp.url, "body": preview})

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

        page.on("request", lambda r: asyncio.create_task(on_request(r)))
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        # 1) Login
        await page.goto("https://www.beatport.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)
        await page.get_by_role("link", name="Login").or_(
            page.get_by_role("button", name="Login")
        ).first.click()
        await page.wait_for_url(lambda u: "account.beatport.com" in u, timeout=20_000)
        await page.fill("input[name='username']", USERNAME)
        await page.fill("input[name='password']", PASSWORD)
        await page.click("button[type='submit']")
        await page.wait_for_url(lambda u: "beatport.com/" in u and "account.beatport.com" not in u,
                                timeout=20_000)
        await page.wait_for_timeout(3000)
        print(f"[probe] logged in. URL: {page.url}")
        await page.screenshot(path=str(SHOTS / "30_post_login.png"), full_page=True)

        # 2) Save auth state for later replay
        await context.storage_state(path=str(ROOT / ".context" / "auth_state.json"))

        # 3) Inspect sidebar — find all playlist links
        # Sidebar appears to be a div containing anchors to /library/... or /playlist/...
        all_anchors = await page.query_selector_all("aside a, nav a, [class*='sidebar'] a, [class*='Sidebar'] a")
        print(f"[probe] {len(all_anchors)} sidebar-ish anchors found")
        seen = set()
        for a in all_anchors:
            try:
                href = await a.get_attribute("href")
                text = (await a.inner_text()).strip()
            except Exception:
                continue
            if href and href not in seen:
                seen.add(href)
                if any(t in href.lower() for t in ["library", "playlist", "/my/"]):
                    print(f"  PL: {text[:40]:40s}  {href}")

        # 4) Look for any anchor with text "Library songs"
        try:
            lib = page.get_by_text("Library songs", exact=False).first
            href = await lib.get_attribute("href")
            print(f"[probe] Library songs href via text: {href}")
            if href:
                await lib.click()
                await page.wait_for_timeout(5000)
                await page.screenshot(path=str(SHOTS / "31_library_songs.png"), full_page=True)
                print(f"[probe] After click URL: {page.url}")
        except Exception as e:
            print(f"[probe] could not find Library songs by text: {e}")

        # 5) Dump all <a> hrefs containing "playlist" or "library"
        all_a = await page.query_selector_all("a")
        playlist_hrefs = []
        for a in all_a:
            try:
                href = await a.get_attribute("href")
                text = (await a.inner_text()).strip()
            except Exception:
                continue
            if href and ("library" in href.lower() or "playlist" in href.lower()):
                playlist_hrefs.append((text, href))
        (ROOT / "logs" / "playlist_links.json").write_text(
            json.dumps(playlist_hrefs, indent=2)
        )
        print(f"[probe] saved {len(playlist_hrefs)} playlist-ish links")

        # 6) Save the rendered HTML for inspection
        (ROOT / "logs" / "post_login.html").write_text(await page.content())

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
