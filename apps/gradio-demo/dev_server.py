"""Dev entry: auto-restart Gradio when ``static/js`` changes.

CSS under ``static/css`` / ``theme.css`` is hot-read on browser refresh
(``DITING_STATIC_HOT=1``). JS is inlined at process start, so edits need a
restart — this wrapper uses ``watchfiles`` for that.

Usage::

    cd apps/gradio-demo
    uv sync --group dev
    uv run python dev_server.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WATCH_JS = ROOT / "static" / "js"


def main() -> None:
    try:
        from watchfiles import run_process
    except ImportError:
        print(
            "watchfiles is required for auto-restart. "
            "Run: uv sync --group dev",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    if not WATCH_JS.is_dir():
        print(f"Missing watch path: {WATCH_JS}", file=sys.stderr)
        raise SystemExit(1)

    print(
        "Diting dev: watching static/js (CSS → browser refresh only; "
        "JS → auto-restart)"
    )
    run_process(
        str(WATCH_JS),
        target=sys.executable,
        args=(str(ROOT / "main.py"),),
        callback=lambda changes: print(f"JS change → restart ({len(changes)} files)"),
    )


if __name__ == "__main__":
    main()
