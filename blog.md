# Using Claude for browser automation with skills creator

I had ~4000 tracks sitting in a Beatport playlist called `Library Songs` and twenty genre playlists I'd been hand-sorting them into. Open track, read genre, click `Add to Playlist`, pick destination, dismiss the duplicate warning if it pops, next track. Done that on weekends for months.

So I wrote one chunky paragraph for Claude Code describing what I'd been doing manually. It had the credentials, the source playlist, the destination playlists, what the modal did, the genre-to-playlist mapping and one important line at the end: "first try this with the LLM one step at a time, recording timings and selectors, then write code to automate it."

Then I said: rewrite this like a skill.

That's the whole bootstrap. The rest of this post is what Claude did with it.

## What skills creator gives back

Claude Code's skills creator turns a prose prompt into a proper skill folder. From my one paragraph, it produced:

- `SKILL.md` - frontmatter with the trigger description, then a structured body covering credentials, per-track flow, classifier rules, failure modes
- `references/classifier.md` - the genre-to-playlist map extracted into its own file with exact-match and word-contains rules
- `scripts/discovery_template.py` - an empty Playwright skeleton with `SELECTORS = {}` and `TIMINGS = {}` dicts for phase 1 to fill in
- `scripts/automate_template.py` - the executor skeleton with the `modal-races-modal` pattern wired up

Three things the creator did with my prompt that turned out to be load-bearing:

1. It promoted my "try with the LLM one step at a time" line into the entire **discovery-first methodology** in `SKILL.md`. That single sentence in the prompt became a multi-paragraph contract about probing the UI manually before automating.
2. It split the classifier into `exact match` and `contains` rules, with explicit ordering guidance ("check more specific terms first") because my prompt mentioned that some genres have parenthesised qualifiers and some are word-matches.
3. It added a `Failure modes` section enumerating selector drift, hover-required actions, modal stacking, genre tag truncation and pagination-vs-infinite-scroll. None of those were in my prompt. The creator generalised from the messy reality I'd implicitly described.

The skill body also added an `Order of operations` checklist with a hard dry-run gate. You don't get to run on the full playlist until you've shown the user a 3-track preview and gotten confirmation. That gate is the most important line in the skill.

## What in my prompt actually shaped the skill

Reading the generated `SKILL.md` against the prompt, I noticed a pattern: the lines that became real constraints in the skill weren't the headlines. They were the throwaways.

- "Note some left side values might not be exactly the same so you can do pattern matching" → became the entire fuzzy-match policy in `classifier.md`, including the order-matters rule for `Hard Techno` vs generic `Techno`.
- "If any of the genre is not mentioned in the key map, then ignore" → became the `skipped: no_match` outcome and a logging rule.
- "Keep track of them until it is finished" → became the resumable `processed_track_ids.txt` pattern and the rule "persist processed set to disk after every N tracks."
- The two-sentence description of the duplicate modal → became a whole `race the two modal outcomes` paragraph with `Promise.race` / `asyncio.wait(..., return_when=FIRST_COMPLETED)` guidance.

Big lesson here: a skill is only as good as the edge cases you put in the prompt. Vague "automate this" prompts produce vague skills. Concrete sentences about what goes wrong (even one-liners about what a specific modal does) produce skills that handle the messy reality.

If you're about to invoke skills creator, write the prompt like you're describing the job to a new hire. Include the failures. Include the workarounds. Include the "oh and by the way…" footnotes. The creator will pick those up and turn them into skill structure.

## Probe before you automate

The skill says it explicitly: probe one step at a time, record selectors and timings, then write the script. Three things will burn you on Beatport if you skip this:

1. The `OneTrust` cookie banner sits behind the viewport and silently intercepts pointer events. Every click in your script retries until it times out.
2. The track list is virtualised. A `page.locator(...)` snapshot at load time misses 95% of the playlist.
3. The `Add to Playlist` modal animates in with an overlay that intercepts clicks for ~400ms after open.

Discovery output is a file called `discovery_notes.md` at the repo root, with selectors, observed timings and quirks. Phase 2 reads it and codifies. Without phase 1 you'd find out about the cookie banner on track 800.

## The pivot: drive the API, not the modal

Two minutes into discovery, I noticed the data calls. Beatport's web app authenticates with `NextAuth`, but every actual fetch hits `https://api.beatport.com/v4/...` with `Authorization: Bearer <jwt>`. Once you have the token, the web UI is a thin client over a clean REST API.

I told Claude to forget the modal. Find the API.

Claude ran a sniffer probe. Headless Playwright that did the login, navigated to the source playlist, clicked `Add to Playlist` once and logged every request to `api.beatport.com`. The whole surface fell out in three runs:

```
GET    /v4/my/playlists/?page=1&per_page=50              # list user playlists
GET    /v4/my/playlists/{id}/tracks/?page=N&per_page=100 # paginate Library Songs
GET    /v4/my/playlists/{id}/tracks/ids/                 # cheap dedup query
POST   /v4/my/playlists/{id}/tracks/bulk/                # add. body: {"track_ids":[N]}
DELETE /v4/my/playlists/{id}/tracks/bulk/                # remove. body: {"item_ids":[entry_id]}
```

4054 tracks across 41 pages of 100. No infinite scroll at the API level. Just plain pagination. The job that looked like "drive a SPA's modal four thousand times" turned into "loop and POST."

The API is almost always the right target for sustained automation. Selectors drift, animations race, virtualised lists hide rows. Versioned APIs don't move much. If you can find the endpoints the official client is hitting, drive those instead.

## The duplicate war story

The skill body talks about a `Duplicate Tracks Detected` modal that pops when you try to add a track already in the destination. The execution flow handles it: race two waits, click `Cancel` on the warning.

When Claude probed the API directly, it added a test track to the `Trance` playlist. 200 OK. Added it again. 200 OK. The track count went up. Tried it a third time. Up again.

Beatport's dedup is client-side only. The `Duplicate Tracks Detected` modal is the web app refusing to make the call. The server has no opinion on duplicates. If you naively replay the bulk endpoint, you can stuff the same track into the same playlist a hundred times.

Had Claude trusted the SKILL.md narrative and built the executor around the modal-races-modal pattern, it would have worked through the UI. The moment we switched to direct API calls, the executor would have silently created thousands of duplicates without warning.

Probing beats trusting the spec. Always.

The fix is a pre-fetch:

```python
def list_track_ids(playlist_id: int) -> set[int]:
    r = client.get(f"{API_ROOT}/my/playlists/{playlist_id}/tracks/ids/")
    return {item["track_id"] for item in r.json()["results"]}

dest_track_ids = {
    name: list_track_ids(pl_id) for name, pl_id in destinations.items()
}

if track_id in dest_track_ids[dest_name]:
    log({"track_id": track_id, "outcome": "duplicate"})
    continue

add_track(dest_id, track_id)
dest_track_ids[dest_name].add(track_id)
```

Three lines of real dedup logic. Wouldn't have existed without phase 1.

## Auth: Playwright in, httpx the rest

I didn't want to drive Playwright for 4000 calls. I just needed the bearer token. So:

1. Headless `Playwright` runs the login flow once: navigate, dismiss the cookie banner, click `Login`, fill the form on `account.beatport.com`, submit, wait for the OAuth callback to redirect back to `www.beatport.com`.
2. A request listener grabs the first `Authorization: Bearer <jwt>` header sent to `api.beatport.com` after login.
3. Browser closes.
4. `httpx.Client` takes over with that header and runs the entire classifier loop.

Token TTL is ~60 minutes. Full run takes ~35 minutes. If a 401 comes back mid-run, the client re-runs the Playwright login and retries. Wired that path once, never had to use it.

## How Claude kept me in the loop

For a 40-minute autonomous run, visibility matters more than speed. Claude wrote everything as JSONL. One JSON object per line, streamed to stdout and appended to `logs/run_log.jsonl`. Every track gets a line:

```json
{"event": "track", "track_id": 16282207, "label": "abcdefu (Original Mix) by Fat Tony, MEDUN, Tiffany Aris", "genre": "Dance / Pop", "destination": "Dance", "outcome": "duplicate", "ts": 1777118772.0}
{"event": "track", "track_id": 13257248, "label": "About Us (feat. EMME) (Extended Mix) by Emme, Le Youth", "genre": "Melodic House & Techno", "destination": "Melodic House", "dest_id": 7241417, "entry_id": 371916542, "outcome": "added", "ts": 1777118772.6}
```

Three reasons this format is right for an agent run:

1. It's valid JSON per line, so you can `tail -f logs/run_log.jsonl | jq` to watch live or pipe through `json-render` (or any JSONL viewer) to browse afterwards.
2. The `outcome` field is enumerable (`added`, `duplicate`, `skipped_no_match`, `skipped_no_genre`, `error`, `noop_empty_items`) so you can `jq 'select(.outcome=="error")'` and audit just the bad ones.
3. It's append-only. If the run crashes, the log is intact and resumable. If you want to grep "did track X get added?", it's one line.

There's also `state/processed_track_ids.txt` next to the log. A flat list of every track ID we've made a routing decision on. Re-running the executor reads that file and skips anything already processed. Idempotent by construction.

The 3-track dry run wrote the same JSONL format. Before saying `go`, I scrolled the four lines, saw `abcdefu → Dance (duplicate)`, `About Us → Melodic House (added)`, `Above It All → Melodic House (added)`, `Abyss → Bass House (added)` and that was enough to trust the pattern.

## What broke mid-run

About 40 tracks into the full run, the executor crashed with an `IndexError` on `resp["items"][0]`. Beatport's bulk-add endpoint had returned `{"items": [], "playlist": {...}}` for one track. A 200 with empty items. Probably a tombstoned or region-locked track that the server silently no-ops on.

The fix was three lines:

```python
items = resp.get("items") or []
if not items:
    log({"event": "track", "outcome": "noop_empty_items", ...})
    mark_processed(track_id)
    continue
```

Treat empty items as a logged warning, mark the track processed so we don't retry it forever, move on. Resumed from `processed_track_ids.txt`, no work lost. The state file paid for itself the first time something went sideways.

## Result

Source playlist had **4054 entries** at run time. I cross-checked against a Beatport CSV export I'd taken earlier (`beatport_export.csv`), which captured `Library Songs` at 3557 entries / 3313 unique `track_id`s. The numbers line up:

```
4054 entries in `Library Songs` (live API)
  3397 unique track_ids
   657 in-source duplicates (same track at multiple positions —
                             manual re-adds over time)
    44 tombstoned (delisted by Beatport — iterator skips them)

3357 routing decisions logged in run_log.jsonl across all runs
  2801 added            (83.4%)
   461 skipped_no_match (13.7%)  Pop, Organic House, World Music, etc.
    77 noop_empty_items  (2.3%)  Beatport's bulk endpoint returned 200
                                 with empty items[] (probably tombstoned
                                 or region-locked, server silently no-ops)
    18 duplicate         (0.5%)  already in destination, caught by the
                                 pre-check
     0 hard errors (no 5xx, no auth failures)
```

The CSV cross-check made one finding tight: **44 tracks were in the CSV but never appeared in my logs**, and the API reports **44 tombstoned** entries. Same number, no coincidence. Beatport's exporter emits tombstoned rows (they're in your library), the API surfaces them with the flag, the iterator drops them silently. Verified, not guessed.

The 657-track gap between source entries and logged decisions is the executor doing the right thing silently. Once a `track_id` lands in `processed_track_ids.txt`, subsequent encounters of the same track are skipped without a log line. If I'd keyed dedup on `entry_id` instead of `track_id`, I'd have gone through every occurrence and probably created destination duplicates too.

Two v2 todos that fell out of this:

- Log a `repeat_in_source` outcome for the silent-skip case so the run is fully accounted for.
- Add a `tombstoned` outcome instead of dropping those entries inside the iterator.

Wall time: ~38 minutes for the resumed full run, plus ~12 minutes across the dry runs and the crashed first attempt.

If Beatport ships a UI change tomorrow, none of the code breaks. The skill is now a one-shot. I add tracks to `Library Songs`, type "run the beatport classifier", Claude does the rest.

## Things to know before you write a web-automation skill

If you're about to do this for some other site, here's what I'd tell past-me:

- **The skill is only as good as the edge cases in your bootstrap prompt.** Write down the failures, the workarounds, the modals you've seen, the things that broke last time. Skills creator picks those up and turns them into structure.
- **Always include a discovery-first phase.** Don't let the agent write the script before it's driven the page once. SPAs lie.
- **Always include a dry-run gate.** Three tracks, show the log, wait for `go`. Without it you'll find out about the bug at scale.
- **API-first when you can.** If the page hits a JSON endpoint, drive that endpoint. Skip the modal. Skip the selectors. Skip the stealth browser.
- **JSONL logs, always.** One event per line, append-only, machine-readable. You will want to grep them later.
- **Resumability.** A flat file of processed IDs. Cheap to write, saves you the first time something crashes mid-run.
- **Don't trust the spec.** The skill body said duplicates were handled by a modal. The server didn't care. Probe the actual behaviour before writing dedup logic.

The fastest path from "I keep doing this thing manually" to "Claude handles it" is a chunky prompt → skills creator → discovery-first executor. Three steps. The middle one writes most of the structure for you.

## Future work

- Run on a cron: sweep `Library Songs` weekly and route anything new
- Add `Trance (Hard / Uplifting)` and `Psy-Trance` as exact-match destinations (currently they fall through to the generic `Trance` bucket)
- Drop in a small confidence score on genre matches and route ambiguous ones to a `Review` playlist instead of skipping

## Acknowledgements

Claude Code wrote the skill, ran the discovery, built the executor and managed the 35-minute autonomous run. I wrote one paragraph and pressed `go`. Thanks for the assist :)
