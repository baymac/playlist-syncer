"""
Login flow probe — phase 0b.

Click the Login link and follow wherever it goes. Capture every navigation
and every XHR. Goal: figure out where the real login form lives.
"""
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "login_flow.jsonl"
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

        page.on("framenavigated", lambda f: write({"kind": "nav", "url": f.url}))
        page.on(
            "request",
            lambda r: write({"kind": "req", "method": r.method, "url": r.url})
            if any(t in r.url for t in ["login", "signin", "auth", "identity", "account"])
            else None,
        )

        await page.goto("https://www.beatport.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # Find and click Login link
        login_locator = page.get_by_role("link", name="Login").or_(
            page.get_by_role("button", name="Login")
        ).or_(page.get_by_text("Login", exact=True))

        try:
            await login_locator.first.click(timeout=10_000)
            print("[probe] clicked Login")
        except Exception as e:
            print(f"[probe] could not click Login: {e}")
            # Try locating any element whose text is Log In
            html = await page.content()
            (ROOT / "logs" / "home.html").write_text(html)
            await browser.close()
            return

        await page.wait_for_timeout(4000)
        await page.screenshot(path=str(SHOTS / "10_login_clicked.png"))
        print(f"[probe] URL after Login click: {page.url}")
        html = await page.content()
        (ROOT / "logs" / "after_login_click.html").write_text(html)

        # Try to fill if a form is present
        for sel in [
            "input[name='username']",
            "input[name='email']",
            "input[type='email']",
            "input[id*='ername']",
            "input[id*='mail']",
        ]:
            try:
                el = await page.wait_for_selector(sel, timeout=2000)
                if el:
                    print(f"[probe] found username field: {sel}")
                    await page.fill(sel, USERNAME)
                    break
            except Exception:
                pass
        for sel in [
            "input[name='password']",
            "input[type='password']",
        ]:
            try:
                el = await page.wait_for_selector(sel, timeout=2000)
                if el:
                    print(f"[probe] found password field: {sel}")
                    await page.fill(sel, PASSWORD)
                    break
            except Exception:
                pass

        await page.screenshot(path=str(SHOTS / "11_filled.png"))

        # Submit
        for sel in [
            "button[type='submit']",
            "button:has-text('Log In')",
            "button:has-text('Login')",
            "button:has-text('Sign In')",
        ]:
            try:
                el = await page.wait_for_selector(sel, timeout=1500)
                if el:
                    print(f"[probe] clicking submit: {sel}")
                    await el.click()
                    break
            except Exception:
                pass

        await page.wait_for_timeout(8000)
        print(f"[probe] post-submit URL: {page.url}")
        await page.screenshot(path=str(SHOTS / "12_post_submit.png"))

        # Save auth state
        await context.storage_state(path=str(ROOT / ".context" / "auth_state.json"))
        cookies = await context.cookies()
        (ROOT / "logs" / "cookies.json").write_text(json.dumps(cookies, indent=2))
        print(f"[probe] cookies: {len(cookies)}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
