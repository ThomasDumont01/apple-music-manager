"""`python -m music_manager import-failures [--clear]` — list failed imports.

The widget uses this to show what didn't make it in and to offer a retry.
Failures are recorded by the detached import worker, so they survive the
process that produced them.

JSON stdout::

    {"entries": [{"isrc", "title", "artist", "reason", "detail", "at"}, ...]}
"""

import argparse
import json
import sys

from music_manager.cli.failures import clear_failures, load_failures
from music_manager.core.config import Paths, load_config

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    """Print the recorded import failures as JSON."""
    parser = argparse.ArgumentParser(prog="music_manager import-failures")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="forget every recorded failure and return an empty list",
    )
    parsed = parser.parse_args(args)

    config = load_config()
    data_root = str(config.get("data_root") or "")
    if not data_root:
        sys.stdout.write(json.dumps({"entries": []}))
        return 0
    paths = Paths(data_root)

    if parsed.clear:
        clear_failures(paths.widget_failures_path)
        sys.stdout.write(json.dumps({"entries": []}))
        return 0

    entries = load_failures(paths.widget_failures_path)
    entries.sort(key=lambda entry: float(entry.get("at") or 0), reverse=True)
    sys.stdout.write(json.dumps({"entries": entries}, ensure_ascii=False))
    return 0
