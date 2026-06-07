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
        "  home [--recent-limit N] [--playlist-limit N]",
        file=sys.stderr,
    )
