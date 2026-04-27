"""Click CLI entry points for the Apple Music → Beatport sync."""
from __future__ import annotations

import sys
from typing import Optional

import click
from rich.console import Console

from beatport_sync import api, db, matching, musickit, sync

console = Console()


@click.group()
def cli():
    """Apple Music → Beatport playlist sync."""
    db.init_db()


@cli.command()
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


@cli.command(name="list-playlists")
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


@cli.command(name="sync")
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


if __name__ == "__main__":
    cli()
