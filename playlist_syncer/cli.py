"""Click CLI entry points for playlist-syncer."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console

from playlist_syncer import api, db, matching, musickit, sync

console = Console()


_TOKEN_HINT = (
    "Get a fresh token from Brave/Chrome:\n"
    "  1. Open beatport.com/library/playlists (logged in)\n"
    "  2. DevTools → Network → any api.beatport.com request → copy Authorization header\n"
    "  3. Run:  playlist-syncer set-token 'Bearer eyJ...'"
)


@click.group()
def cli():
    """playlist-syncer — sync playlists across music platforms."""


@cli.command(name="set-token")
@click.argument("token")
def set_token_cmd(token: str):
    """Cache a Beatport Bearer token obtained manually from your browser.

    TOKEN is the full Authorization header value, e.g. 'Bearer eyJ...'
    """
    db.init_db()
    db.init_detect_db()
    if not token.startswith("Bearer "):
        token = f"Bearer {token}"
    db.set_token("beatport", token)
    db.set_token("beatport", token, db_path=db.DETECT_DB_PATH)
    console.print("[green]Beatport token cached.[/green] Run your sync command now — the token expires in ~10 minutes.")


@cli.group(name="music-beatport-sync")
def music_beatport_sync():
    """Apple Music → Beatport playlist sync."""
    db.init_db()


@music_beatport_sync.command()
def check_connections():
    """Verify MusicKit authorization and Beatport credentials."""
    console.print("Checking MusicKit…", end=" ")
    authorized, msg = musickit.check_musickit()
    if authorized:
        console.print("[green]OK[/green]")
    else:
        console.print(f"[red]FAILED[/red]\n{msg}")

    console.print("Checking Beatport…", end=" ")
    username, password = sync.require_env()
    try:
        token = sync.get_or_refresh_token(username, password)
        client = api.make_client(token)
        beatport = api.Beatport(client=client)
        playlists = beatport.list_my_playlists()
        console.print(f"[green]OK[/green] ({len(playlists)} playlists found)")
        client.close()
    except Exception as e:
        console.print(f"[red]FAILED[/red]\n{e}")
        if "401" in str(e):
            console.print(f"\n[yellow]{_TOKEN_HINT}[/yellow]")


@music_beatport_sync.command(name="list-playlists")
def list_playlists_cmd():
    """List Apple Music playlists available for sync."""
    console.print("Fetching playlists from Apple Music…")
    try:
        names = musickit.list_playlists()
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    for name in sorted(names):
        console.print(f"  {name}")
    console.print(f"\n[dim]{len(names)} playlists[/dim]")


@music_beatport_sync.command(name="sync")
@click.option("--playlist", "-p", default=None, help="Apple Music playlist name to sync.")
@click.option("--library", "use_library", is_flag=True,
              help="Sync songs added to library (Music app 'Songs' tab).")
@click.option("--favorites", "use_favorites", is_flag=True,
              help="Sync songs in the 'Favourite Songs' playlist.")
@click.option("--library-and-favorites", "use_lib_and_fav", is_flag=True,
              help="Sync library songs plus Favourite Songs (union).")
@click.option("--all", "use_all", is_flag=True,
              help="Sync all songs from MusicLibraryRequest<Song> (no filter).")
@click.option("--dry-run", is_flag=True, help="Show what would be synced without making changes.")
@click.option("--limit", default=0, help="Stop after processing N tracks (0 = no limit).")
@click.option("--verbose", "-v", is_flag=True, help="Print Beatport search errors to stderr.")
@click.option("--threshold", default=matching.MATCH_THRESHOLD, show_default=True,
              help="Fuzzy match threshold (0-1).")
def sync_cmd(playlist: Optional[str], use_library: bool, use_favorites: bool,
             use_lib_and_fav: bool, use_all: bool, dry_run: bool, limit: int,
             verbose: bool, threshold: float):
    """Sync an Apple Music playlist or library selection to Beatport genre playlists."""
    mode_flags = sum([bool(playlist), use_library, use_favorites, use_lib_and_fav, use_all])
    if mode_flags > 1:
        console.print("[red]Error:[/red] --playlist, --library, --favorites, "
                      "--library-and-favorites, and --all are mutually exclusive.")
        sys.exit(1)

    sync.run_sync(
        playlist=playlist,
        use_library=use_library,
        use_favorites=use_favorites,
        use_lib_and_fav=use_lib_and_fav,
        use_all=use_all,
        dry_run=dry_run,
        limit=limit,
        verbose=verbose,
        threshold=threshold,
    )


@cli.group(name="detect-beatport-sync")
def detect_beatport_sync():
    """track-detect → Beatport playlist sync."""
    db.init_db()


@detect_beatport_sync.command(name="sync")
@click.option(
    "--db", "detect_db", required=True, type=click.Path(exists=True, path_type=Path),
    help="Path to the track-detect SQLite database.",
)
@click.option("--playlist", "-p", default=None,
              help="Beatport destination playlist name. Defaults to genre classification.")
@click.option("--dry-run", is_flag=True, help="Show what would be synced without making changes.")
@click.option("--limit", default=0, help="Stop after processing N tracks (0 = no limit).")
@click.option("--verbose", "-v", is_flag=True, help="Print Beatport search details to stderr.")
@click.option("--threshold", default=matching.MATCH_THRESHOLD, show_default=True,
              help="Fuzzy match threshold (0-1).")
def detect_sync_cmd(
    detect_db: Path,
    playlist: Optional[str],
    dry_run: bool,
    limit: int,
    verbose: bool,
    threshold: float,
):
    """Sync tracks from a track-detect database to Beatport.

    State is tracked in state/detect_sync.db — separate from the Apple Music
    sync DB. The source track-detect database is never modified.
    No-match outcomes are terminal (not retried). Only search errors are retried.
    Check the run log for fuzzy misses to review manually.
    """
    sync.run_sync_detected(
        detect_db=detect_db,
        dry_run=dry_run,
        limit=limit,
        verbose=verbose,
        threshold=threshold,
        playlist=playlist,
    )


if __name__ == "__main__":
    cli()
