"""
Automation template — phase 2.

Fill in SELECTORS and TIMINGS from discovery_notes.md. Then run.
Streams progress to stdout and writes a JSONL run log.

Resumable: reads `processed_ids.txt` if present.
"""
import asyncio
import json
import os
import random
import time
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ---------- config ----------

CDP_URL = os.environ["OBSCURA_CDP_URL"]
USERNAME = os.environ["BEATPORT_USERNAME"]
PASSWORD = os.environ["BEATPORT_PASSWORD"]

LOG_PATH = Path("run_log.jsonl")
PROCESSED_PATH = Path("processed_ids.txt")

# Fill from discovery_notes.md
SELECTORS = {
    "login_username": "",
    "login_password": "",
    "login_submit": "",
    "sidebar_playlists_section": "",
    "library_songs_link": "",
    "track_row": "",
    "track_genre": "",
    "track_add_to_pl_btn": "",
    "modal_root": "",
    "modal_pl_option_template": "",   # e.g. f"[data-pl-name='{name}']"
    "modal_confirm_btn": "",
    "duplicate_modal": "",
    "duplicate_cancel_btn": "",
    "playlist_scroll_container": "",
}

TIMINGS = {
    "post_login": 5000,
    "modal_open": 1500,
    "outcome_race": 5000,
    "scroll_settle": 1200,
}

# ---------- classifier ----------

EXACT = {
    "dance/pop": "Dance",
    "melodic house & techno": "Melodic House",
    "house": "House",
    "tech house": "Tech House",
    "indie dance": "Indie Dance",
    "drum & bass": "DnB",
    "techno (raw / deep / hypnotic)": "Hypnotic Techno",
    "techno (peak time / driving)": "Peak Techno",
    "downtempo": "Downtempo",
    "progressive house": "Progressive House",
    "bass house": "Bass House",
    "afro house": "Afro House",
    "deep house": "Deep House",
    "hard techno": "Hard Techno",
    "ambient": "Ambient",
    "electronica": "Electronica",
}
CONTAINS = [
    ("dubstep", "Dubstep"),
    ("mainstage", "Mainstage"),
    ("minimal", "Minimal"),
    ("trance", "Trance"),
]


def classify(genre: str) -> str | None:
    if not genre:
        return None
    g = genre.strip().lower()
    if g in EXACT:
        return EXACT[g]
    for needle, pl in CONTAINS:
        if needle in g:
            return pl
    return None


# ---------- run log ----------

def log(record: dict):
    record["ts"] = time.time()
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    print(json.dumps(record))


def load_processed() -> set[str]:
    if PROCESSED_PATH.exists():
        return set(PROCESSED_PATH.read_text().splitlines())
    return set()


def mark_processed(track_id: str):
    with PROCESSED_PATH.open("a") as f:
        f.write(track_id + "\n")


# ---------- main flow ----------

async def login(page):
    # TODO: implement based on discovery
    await page.fill(SELECTORS["login_username"], USERNAME)
    await page.fill(SELECTORS["login_password"], PASSWORD)
    await page.click(SELECTORS["login_submit"])
    await page.wait_for_timeout(TIMINGS["post_login"])


async def open_library_songs(page):
    await page.click(SELECTORS["sidebar_playlists_section"])
    await page.click(SELECTORS["library_songs_link"])
    await page.wait_for_selector(SELECTORS["track_row"])


async def process_row(page, row, processed: set[str]):
    track_id = await row.get_attribute("data-track-id")  # adjust per discovery
    if not track_id or track_id in processed:
        return

    genre_el = await row.query_selector(SELECTORS["track_genre"])
    genre = (await genre_el.get_attribute("title")) or (await genre_el.inner_text())
    destination = classify(genre)

    if not destination:
        log({"track_id": track_id, "genre": genre, "outcome": "skipped_no_match"})
        mark_processed(track_id)
        processed.add(track_id)
        return

    await row.hover()
    add_btn = await row.query_selector(SELECTORS["track_add_to_pl_btn"])
    await add_btn.click()
    await page.wait_for_selector(SELECTORS["modal_root"], timeout=TIMINGS["modal_open"])

    pl_option_sel = SELECTORS["modal_pl_option_template"].format(name=destination)
    await page.click(pl_option_sel)
    await page.click(SELECTORS["modal_confirm_btn"])

    # Race the two outcomes
    modal_closed = asyncio.create_task(
        page.wait_for_selector(SELECTORS["modal_root"], state="detached",
                               timeout=TIMINGS["outcome_race"])
    )
    duplicate = asyncio.create_task(
        page.wait_for_selector(SELECTORS["duplicate_modal"],
                               timeout=TIMINGS["outcome_race"])
    )
    done, pending = await asyncio.wait(
        {modal_closed, duplicate}, return_when=asyncio.FIRST_COMPLETED
    )
    for t in pending:
        t.cancel()

    if duplicate in done and not duplicate.exception():
        await page.click(SELECTORS["duplicate_cancel_btn"])
        outcome = "duplicate"
    elif modal_closed in done and not modal_closed.exception():
        outcome = "added"
    else:
        outcome = "timeout"
        await page.keyboard.press("Escape")

    log({"track_id": track_id, "genre": genre, "destination": destination,
         "outcome": outcome})
    mark_processed(track_id)
    processed.add(track_id)
    await page.wait_for_timeout(random.randint(300, 800))


async def scroll_and_collect(page, processed: set[str]):
    container = await page.query_selector(SELECTORS["playlist_scroll_container"])
    last_count = -1
    stable_scrolls = 0

    while True:
        rows = await page.query_selector_all(SELECTORS["track_row"])
        new_rows = []
        for row in rows:
            tid = await row.get_attribute("data-track-id")
            if tid and tid not in processed:
                new_rows.append(row)

        for row in new_rows:
            try:
                await process_row(page, row, processed)
            except Exception as e:
                log({"track_id": "unknown", "outcome": "error", "error": str(e)})

        if len(rows) == last_count:
            stable_scrolls += 1
            if stable_scrolls >= 3:
                break
        else:
            stable_scrolls = 0
        last_count = len(rows)

        await container.evaluate("el => el.scrollTo(0, el.scrollHeight)")
        await page.wait_for_timeout(TIMINGS["scroll_settle"])


async def main():
    processed = load_processed()
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        if "beatport.com" not in page.url:
            await page.goto("https://www.beatport.com/")
            await login(page)
        await open_library_songs(page)
        await scroll_and_collect(page, processed)


if __name__ == "__main__":
    asyncio.run(main())
