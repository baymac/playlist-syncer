"""
Probe duplicate-track behavior by replaying the bulk endpoint with httpx.
Reuses the auth token captured during the previous Playwright session.
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent

USERNAME = os.environ["BEATPORT_USERNAME"]
PASSWORD = os.environ["BEATPORT_PASSWORD"]


def get_fresh_token() -> str:
    """Login via Playwright, capture the api.beatport.com Bearer token."""
    captured = {"token": None}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.new_page()

        def on_req(req):
            if "api.beatport.com" in req.url and "user:" in (
                req.headers.get("authorization", "") or ""
            ):
                if not captured["token"]:
                    captured["token"] = req.headers["authorization"]

        # Capture all api.beatport.com auth headers
        def capture(req):
            auth = req.headers.get("authorization", "")
            if "api.beatport.com" in req.url and auth.startswith("Bearer "):
                captured["token"] = auth

        page.on("request", capture)

        page.goto("https://www.beatport.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        try:
            page.locator("#onetrust-accept-btn-handler").first.click(timeout=2000)
        except Exception:
            pass
        page.get_by_role("link", name="Login").or_(
            page.get_by_role("button", name="Login")
        ).first.click()
        page.wait_for_url(lambda u: "account.beatport.com" in u, timeout=20_000)
        page.fill("input[name='username']", USERNAME)
        page.fill("input[name='password']", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_url(
            lambda u: "beatport.com/" in u and "account.beatport.com" not in u,
            timeout=20_000,
        )
        page.wait_for_timeout(3000)
        # Trigger an authed API call so we definitely capture the user token
        page.goto("https://www.beatport.com/library/playlists/7241393",
                  wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        browser.close()

    if not captured["token"]:
        sys.exit("could not capture token")
    return captured["token"]


def main():
    token = get_fresh_token()
    print("[probe] token len:", len(token))

    # Test 1: add a fresh different track to a destination playlist
    # We'll use track id 16282207 ("abcdefu" -> Dance/Pop) -> Dance playlist 7241436
    headers = {
        "authorization": token,
        "content-type": "application/json",
        "accept": "application/json, text/plain, */*",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "origin": "https://www.beatport.com",
        "referer": "https://www.beatport.com/",
    }

    test_track = 16282207  # "abcdefu" Dance/Pop
    test_dest = 7241436    # Dance

    with httpx.Client(headers=headers, timeout=20) as c:
        # First add
        r = c.post(
            f"https://api.beatport.com/v4/my/playlists/{test_dest}/tracks/bulk/",
            json={"track_ids": [test_track]},
        )
        print(f"[probe] first add: {r.status_code}")
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        print(f"  body: {json.dumps(body)[:600]}")

        # Sleep a tick then duplicate
        time.sleep(1)
        r2 = c.post(
            f"https://api.beatport.com/v4/my/playlists/{test_dest}/tracks/bulk/",
            json={"track_ids": [test_track]},
        )
        print(f"[probe] duplicate add: {r2.status_code}")
        try:
            body2 = r2.json()
        except Exception:
            body2 = r2.text
        print(f"  body: {json.dumps(body2)[:800]}")

        # Test sample track-list call for genre check
        r3 = c.get(
            "https://api.beatport.com/v4/my/playlists/7241393/tracks/?page=1&per_page=2"
        )
        print(f"[probe] sample list: {r3.status_code}")


if __name__ == "__main__":
    main()
