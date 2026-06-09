"""Music Manager CLI sub-commands.

Invoked via ``python -m music_manager <subcommand> [args]``. The dispatcher
is wired in ``music_manager/__main__.py`` *before* the Textual UI starts,
so CLI invocations skip the entire UI boot path (zero perf impact for
normal launches).
"""

import sys

# ── Entry point ──────────────────────────────────────────────────────────────


def dispatch(args: list[str]) -> int:
    """Route the sub-command. ``args[0]`` is the sub-command name."""
    if not args:
        _print_usage()
        return 2

    name = args[0]
    rest = args[1:]

    if name == "search":
        from music_manager.cli.search import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "import-isrcs":
        from music_manager.cli.import_cmd import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "import-status":
        from music_manager.cli.status import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "play":
        from music_manager.cli.play import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "home":
        from music_manager.cli.home import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "play-playlist":
        from music_manager.cli.play_playlist import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "shuffle":
        from music_manager.cli.shuffle import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "search-playlists":
        from music_manager.cli.search_playlists import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "playlist-tracks":
        from music_manager.cli.playlist_tracks import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "spotify-login":
        from music_manager.cli.spotify_login import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "spotify-auth-status":
        from music_manager.cli.spotify_auth_status import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "spotify-logout":
        from music_manager.cli.spotify_logout import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "spotify-playlists":
        from music_manager.cli.spotify_playlists import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "spotify-playlist-tracks":
        from music_manager.cli.spotify_playlist_tracks import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "spotify-set-client-id":
        from music_manager.cli.spotify_set_client_id import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "exportify-process-csv":
        from music_manager.cli.exportify_process_csv import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "playlist-local-tracks":
        from music_manager.cli.playlist_local_tracks import main as cmd  # noqa: PLC0415

        return cmd(rest)
    if name == "import-cancel":
        from music_manager.cli.import_cancel import main as cmd  # noqa: PLC0415

        return cmd(rest)

    _print_usage()
    return 2


# ── Private Functions ────────────────────────────────────────────────────────


def _print_usage() -> None:
    print(
        "Usage: python -m music_manager <command> [args]\n"
        "Commands:\n"
        "  search \"query\" [--limit N]\n"
        "  search-playlists \"query\" [--limit N]\n"
        "  playlist-tracks DEEZER_PLAYLIST_ID [--max N]\n"
        "  import-isrcs ISRC1,ISRC2,... [--playlist-name \"Name\"] [--detach]\n"
        "  import-status\n"
        "  play APPLE_ID\n"
        "  play-playlist \"Playlist Name\"\n"
        "  shuffle\n"
        "  home [--recent-limit N] [--playlist-limit N]\n"
        "  spotify-login [--detach] [--timeout SECONDS]\n"
        "  spotify-auth-status\n"
        "  spotify-logout\n"
        "  spotify-playlists\n"
        "  spotify-playlist-tracks <SPOTIFY_ID|liked> [--max N]\n"
        "  spotify-set-client-id <CLIENT_ID>\n"
        "  exportify-process-csv <ABSOLUTE_PATH>\n"
        "  playlist-local-tracks <NAME>\n"
        "  import-cancel",
        file=sys.stderr,
    )
