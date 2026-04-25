"""
API probe — phase 0.

Headless Playwright run that:
  1. Logs in to beatport.com as $BEATPORT_USERNAME / $BEATPORT_PASSWORD
  2. Navigates the SPA enough to surface Library-songs list + add-to-playlist XHRs
  3. Logs every request + response (URL, method, status, headers, payload preview)
     to logs/api_capture.jsonl
  4. Saves the authenticated storage state to .context/auth_state.json so we
     can replay sessions without re-logging in.

Output is for human inspection: we want to know if there's a clean
api.beatport.com REST surface we can drive directly with httpx.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright, Request, Response

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / "api_capture.jsonl"
AUTH_STATE = ROOT / ".context" / "auth_state.json"
SCREENSHOT_DIR = ROOT / "logs" / "screenshots"

USERNAME = os.environ["BEATPORT_USERNAME"]
PASSWORD = os.environ["BEATPORT_PASSWORD"]


def log(record: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def is_interesting(url: str) -> bool:
    return (
        "beatport.com/api" in url
        or "api.beatport.com" in url
        or "embed.beatport.com" in url
        or "/_next/data" in url
    )


async def main():
    LOG_PATH.unlink(missing_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()

        async def on_request(req: Request):
            if not is_interesting(req.url):
                return
            try:
                body = req.post_data
            except Exception:
                body = None
            log(
                {
                    "kind": "request",
                    "method": req.method,
                    "url": req.url,
                    "headers": dict(req.headers),
                    "body": body,
                }
            )

        async def on_response(resp: Response):
            if not is_interesting(resp.url):
                return
            payload_preview = None
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    text = await resp.text()
                    payload_preview = text[:4000]
            except Exception as e:
                payload_preview = f"<read-error: {e}>"
            log(
                {
                    "kind": "response",
                    "status": resp.status,
                    "url": resp.url,
                    "headers": dict(resp.headers),
                    "body_preview": payload_preview,
                }
            )

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("console", lambda m: print(f"[console] {m.type}: {m.text}", file=sys.stderr))

        # --- Step 1: hit homepage to seed cookies / CSRF ---
        print("[probe] navigating to beatport.com")
        await page.goto("https://www.beatport.com/", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(SCREENSHOT_DIR / "01_home.png"))

        # --- Step 2: login. Beatport's login is at /account/login ---
        print("[probe] navigating to /account/login")
        await page.goto(
            "https://www.beatport.com/account/login",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(SCREENSHOT_DIR / "02_login.png"))

        # Try common selectors. We'll iterate — log what we find.
        try:
            await page.fill("input[name='username']", USERNAME, timeout=5000)
        except Exception:
            try:
                await page.fill("input[type='email']", USERNAME, timeout=5000)
            except Exception as e:
                print(f"[probe] could not find username field: {e}")
                await page.screenshot(path=str(SCREENSHOT_DIR / "03_no_username.png"))
                html = await page.content()
                (ROOT / "logs" / "login_page.html").write_text(html)
                await browser.close()
                return

        try:
            await page.fill("input[name='password']", PASSWORD, timeout=5000)
        except Exception:
            await page.fill("input[type='password']", PASSWORD, timeout=5000)

        await page.screenshot(path=str(SCREENSHOT_DIR / "03_filled.png"))

        # Submit
        try:
            await page.click("button[type='submit']", timeout=5000)
        except Exception:
            await page.keyboard.press("Enter")

        # Wait for either redirect off /login or visible error
        try:
            await page.wait_for_url(lambda url: "/account/login" not in url, timeout=20_000)
            print(f"[probe] post-login URL: {page.url}")
        except Exception:
            print(f"[probe] still on login page: {page.url}")

        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(SCREENSHOT_DIR / "04_post_login.png"))

        # Save auth state regardless — we may have cookies even on partial fail
        AUTH_STATE.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(AUTH_STATE))
        print(f"[probe] storage state saved to {AUTH_STATE}")

        # --- Step 3: try to find the Library songs playlist ---
        # The playlist sidebar is under /library — try navigating directly.
        print("[probe] navigating to /library/playlists")
        try:
            await page.goto(
                "https://www.beatport.com/library/playlists",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            await page.wait_for_timeout(3000)
            await page.screenshot(path=str(SCREENSHOT_DIR / "05_library.png"))
        except Exception as e:
            print(f"[probe] library nav failed: {e}")

        # --- Step 4: dump cookies for httpx replay ---
        cookies = await context.cookies()
        (ROOT / "logs" / "cookies.json").write_text(json.dumps(cookies, indent=2))
        print(f"[probe] {len(cookies)} cookies captured")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
