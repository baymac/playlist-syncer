---
name: beatport-playlist-classifier
description: Use this skill whenever the user wants to automate Beatport.com playlist organization — specifically, classifying tracks from a source playlist (e.g. "Library songs") into genre-specific destination playlists by reading each track's genre tag and routing it via the "add to playlist" modal. Trigger this for requests involving Beatport automation, DJ track sorting, genre-based playlist routing, browser automation against Beatport with Playwright, or any workflow that mentions an Obscura/stealth browser session against beatport.com. Also use when the user wants to scale a manual Beatport sorting workflow they've been doing by hand.
---

# Beatport Playlist Classifier

Automates classifying tracks in a Beatport source playlist into genre-specific destination playlists. Built around a stealth browser session (Obscura: https://github.com/h4ckf0r0day/obscura) driving Playwright, with a discovery-first methodology: probe the UI manually one step at a time, record timings and selectors, then codify into an automation script.

## Overview

The workflow has two phases:

1. **Discovery phase** (LLM-driven, manual): Drive the browser one action at a time. For each step, record (a) the selector that worked, (b) how long the page took to settle, and (c) any quirks (modal animations, lazy-loaded list items, debounced search). Output is a notes file you can hand to phase 2.
2. **Execution phase** (scripted): Translate the discovery notes into a Playwright script that loops over every track in the source playlist, reads the genre tag, looks it up in the classifier map, and routes the track to the right destination playlist via the "add to playlist" modal.

This split exists because Beatport is SPA-heavy with lazy-loaded virtualized lists and animated modals. Selectors and timings drift; guessing them up front wastes runs. Probe first, then automate.

## Credentials

The user's Beatport username is `antmatter`. **Do not hardcode the password into any committed file.** Read it from an environment variable:

```bash
export BEATPORT_USERNAME=antmatter
export BEATPORT_PASSWORD=<password>
```

The password the user provided in chat goes in the env var at runtime, never into `SKILL.md`, scripts, or git history.

## Stack

- **Obscura** for the browser session (handles fingerprinting, evasion). Clone from https://github.com/h4ckf0r0day/obscura and follow its README to spin up a session that Playwright can attach to via CDP.
- **Playwright** (Python or Node — pick whichever the user prefers; default to Python for parity with their broader stack) for selectors and actions.
- **Source playlist**: "Library songs" on the user's Beatport account, found in the left sidebar under Playlists. May require scrolling the sidebar to locate.

## Source playlist behavior

"Library songs" uses infinite scroll. The full track list is not in the DOM at load time. The execution script must scroll the playlist container until either no new tracks load (terminal state) or every visible track has been processed and re-scrolling produces nothing new.

Maintain a `processed_track_ids` set keyed by Beatport's internal track ID (extractable from the track row's data attributes or link href — confirm in discovery). Idempotency matters because scrolling can re-render rows.

## Per-track flow

For each track row:

1. Read the **genre** tag from the track row.
2. Look up the genre in the classifier map (see `references/classifier.md`). If no match, **skip the track** and log it.
3. Click the track's **add to playlist** button. A modal opens listing the user's playlists.
4. In the modal, select the destination playlist (from the classifier map).
5. Click **Add to Playlist**.
6. One of two things happens:
   - The modal closes → success, mark track processed, move on.
   - A **"Duplicate Tracks Detected"** modal appears → click **Cancel**, mark track processed (it's already in the destination playlist), move on.
7. If the original modal is still open (neither outcome fired within a reasonable timeout), back out with Escape and log the row for retry.

## Classifier map

The genre-to-playlist mapping lives in `references/classifier.md`. It includes both exact matches (e.g. "Tech House" → "Tech House") and word-contains rules (e.g. any genre containing "Trance" → "Trance"). Read that file before writing the lookup function. Word-contains rules must be checked **after** exact matches to avoid premature matches (e.g. "Hard Techno" should not be caught by a generic "Techno" rule if a more specific entry exists).

## Phase 1: Discovery

Run discovery before writing any loop. The script in `scripts/discovery_template.py` is a skeleton — fill it in as you probe.

Steps to walk through manually:

1. Launch Obscura, attach Playwright via CDP. Record CDP attach time.
2. Navigate to beatport.com, log in. Record selectors for username/password fields and submit button. Record time from submit click to authenticated landing page.
3. Open the left sidebar, find "Playlists" section. Record selector. If "Library songs" requires scrolling within the sidebar, record that too.
4. Click "Library songs". Record selector and the time until the first batch of tracks is visible.
5. For one track row, identify and record selectors for:
   - The genre tag/label
   - The track ID (data attribute, href, or row id)
   - The "add to playlist" button (often an icon in a row-level action menu — note if it's in an overflow `...` menu)
6. Click "add to playlist". Record selector for the playlist modal, the playlist option list, and the **Add to Playlist** confirm button. Record modal open animation time.
7. Confirm with a known destination. Watch for either:
   - Modal close → record close animation time
   - Duplicate Tracks Detected modal → record its selector and the Cancel button selector
8. Scroll the source playlist. Record the scrollable container selector and how many new rows appear per scroll, plus settle time.

Save findings to `discovery_notes.md` in the working directory. Format:

```markdown
## Selectors
- login_username: <selector>
- login_password: <selector>
- ...

## Timings (worst-case observed)
- post_login_settle: 2.5s
- modal_open: 400ms
- ...

## Quirks
- Add to playlist button only appears on row hover; need to hover row first.
- ...
```

These timings drive the `wait_for` / `expect` timeouts in phase 2. Pad them ~2x for safety.

## Phase 2: Execution script

Once `discovery_notes.md` is filled in, write the script. Skeleton: `scripts/automate_template.py`.

Structure:

```
attach_browser()
login()
open_library_songs()
processed = set()
while True:
    rows = visible_track_rows()
    new_rows = [r for r in rows if r.id not in processed]
    if not new_rows:
        if scrolled_to_bottom():
            break
        scroll_one_page()
        wait_for_settle()
        continue
    for row in new_rows:
        try:
            classify_and_route(row)
        except Exception as e:
            log_failure(row.id, e)
        processed.add(row.id)
```

Key implementation rules:

- **Wait on conditions, not sleeps.** Use `page.wait_for_selector` / `expect(...).to_be_visible()` with timeouts derived from discovery, not bare `time.sleep`. The only acceptable sleeps are short post-action settles (~150–300ms) for animations.
- **Race the two modal outcomes.** After clicking Add to Playlist, race a wait for either (a) the playlist modal disappearing or (b) the Duplicate Tracks modal appearing. `Promise.race` / `asyncio.wait(..., return_when=FIRST_COMPLETED)`.
- **Log every track**: id, genre read, destination playlist (or "skipped: no match"), outcome (added / duplicate / error). CSV or JSONL. The user will want to audit afterward.
- **Resumability**: persist the processed set to disk after every N tracks so a crash mid-run doesn't restart from zero.
- **Rate limiting**: insert a small randomized delay (300–800ms) between tracks. Beatport will not appreciate 50 add-to-playlist calls per second, and Obscura's stealth doesn't excuse hammering the API.

## Order of operations for the agent running this skill

1. Read `references/classifier.md` and load the mapping.
2. Confirm with the user that `BEATPORT_USERNAME` and `BEATPORT_PASSWORD` env vars are set.
3. Run discovery (phase 1), filling in `discovery_notes.md`. Show the user the notes before proceeding.
4. Write the execution script using the discovery notes.
5. Do a **dry run on the first 3 tracks only** with logging on. Show the user the log. Get confirmation before unleashing on the full playlist.
6. Run on the full playlist. Stream progress (tracks processed / total visible / current row).
7. At the end, present the run log file via `present_files`.

## Failure modes to watch for

- **Selector drift**: Beatport ships UI changes. If a selector fails on first use, halt and re-run discovery for that step rather than retrying blindly.
- **Hover-required actions**: many row-level controls only appear on hover. Always `row.hover()` before clicking row-level buttons.
- **Modal stacking**: the Duplicate Tracks modal sometimes appears *over* the playlist modal. Cancel closes only the top one. After Cancel, verify the playlist modal also closed; if not, Escape it.
- **Genre tag truncation**: long genre names may be visually truncated with ellipsis but the full text lives in a `title` attribute or a tooltip. Read the attribute, not `innerText`.
- **Pagination vs infinite scroll**: confirm during discovery which one "Library songs" actually uses. The instructions assume infinite scroll based on the user's description; verify.

## Reference files

- `references/classifier.md` — full genre → playlist mapping with matching rules.
- `scripts/discovery_template.py` — phase 1 skeleton.
- `scripts/automate_template.py` — phase 2 skeleton.
