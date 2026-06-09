"""`python -m music_manager import-cancel` — signal the running import to stop.

Writes a small flag file that the in-progress ``import-isrcs`` worker checks
after every track. The worker terminates cleanly with ``status: "cancelled"``
and the widget polls ``import-status`` to pick up the final state.

JSON stdout: ``{"status": "ok"}``. Exits 0 even if no import is running —
this is best-effort signalling.
"""

import json
import os
import sys

from music_manager.core.config import Paths, load_config

# ── Entry point ──────────────────────────────────────────────────────────────


def main(args: list[str]) -> int:
    config = load_config()
    data_root = str(config.get("data_root") or "")
    if not data_root:
        sys.stdout.write(json.dumps({"status": "ok"}))
        return 0
    paths = Paths(data_root)
    try:
        os.makedirs(os.path.dirname(paths.widget_cancel_path), exist_ok=True)
        with open(paths.widget_cancel_path, "w", encoding="utf-8") as handle:
            handle.write("1")
    except OSError:
        pass
    sys.stdout.write(json.dumps({"status": "ok"}))
    return 0
