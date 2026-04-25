"""
Discovery template — phase 1.

Run this interactively (or step through in a REPL/notebook). The goal is to
fill in the SELECTORS and TIMINGS dicts at the bottom, and to surface any
quirks. Output gets pasted into discovery_notes.md.

Prereq: Obscura is running and exposing a CDP endpoint. Set OBSCURA_CDP_URL.
"""
import os
import time
from playwright.sync_api import sync_playwright

CDP_URL = os.environ["OBSCURA_CDP_URL"]
USERNAME = os.environ["BEATPORT_USERNAME"]
PASSWORD = os.environ["BEATPORT_PASSWORD"]

SELECTORS: dict[str, str] = {}
TIMINGS: dict[str, float] = {}


def t(label: str):
    """Context manager-ish timer. Use: start = t('label'); ...; TIMINGS['label'] = time.time()-start"""
    return time.time()


def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()

        # --- Step 1: navigate + login ---
        start = t("nav")
        page.goto("https://www.beatport.com/", wait_until="domcontentloaded")
        TIMINGS["nav_to_loaded"] = time.time() - start
        # TODO: find and fill the login form. Record the selectors that worked.
        # SELECTORS['login_username'] = '...'
        # SELECTORS['login_password'] = '...'
        # SELECTORS['login_submit']   = '...'

        # --- Step 2: open Library songs ---
        # Left sidebar → Playlists → scroll to find "Library songs" → click.
        # SELECTORS['sidebar_playlists_section'] = '...'
        # SELECTORS['library_songs_link']        = '...'

        # --- Step 3: inspect one track row ---
        # SELECTORS['track_row']           = '...'   # rows in the playlist
        # SELECTORS['track_genre']         = '...'   # within row
        # SELECTORS['track_id_attr']       = '...'   # data attribute or href
        # SELECTORS['track_add_to_pl_btn'] = '...'

        # --- Step 4: add-to-playlist modal ---
        # Click add-to-playlist, then probe the modal.
        # SELECTORS['modal_root']          = '...'
        # SELECTORS['modal_pl_option']     = '...'   # template, parameterized by name
        # SELECTORS['modal_confirm_btn']   = '...'

        # --- Step 5: outcome modals ---
        # SELECTORS['duplicate_modal']      = '...'
        # SELECTORS['duplicate_cancel_btn'] = '...'

        # --- Step 6: scrolling ---
        # SELECTORS['playlist_scroll_container'] = '...'
        # Test scroll: count rows, scroll, count rows, record delta + settle.

        print("SELECTORS:", SELECTORS)
        print("TIMINGS:", TIMINGS)


if __name__ == "__main__":
    main()
