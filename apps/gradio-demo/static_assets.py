"""Static theme/script assets for the Gradio demo.

Dev hot-read (default on): each request reloads files from disk with
``Cache-Control: no-store``, so editing CSS or JS only needs a browser
refresh. Set ``DITING_STATIC_HOT=0`` in production to allow short caching.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_URL_PREFIX = "/diting-static"

# Theme sections (order = cascade). ``theme.css`` @imports these.
THEME_CSS_PARTS = (
    "css/base.css",
    "css/settings-modal.css",
    "css/export.css",
    "css/output-log.css",
    "css/search-cards.css",
    "css/report-blocks.css",
    "css/nav.css",
    "css/hero.css",
    "css/icons.css",
    "css/report-document.css",
    "css/overrides.css",
    "css/responsive.css",
)

_HOT_RAW = (os.getenv("DITING_STATIC_HOT") or "1").strip().lower()
STATIC_HOT = _HOT_RAW in {"1", "true", "yes", "on"}

HEAD_SCRIPTS = (
    "js/skills_bind.js",
    "js/process_details.js",
    "js/task_id_url_bridge.js",
    "js/miro_modal.js",
    "js/enter_submit.js",
    "js/export_titles.js",
    "js/elapsed_timer.js",
    "js/starfield.js",
)


def _safe_path(rel: str) -> Path:
    rel_norm = rel.replace("\\", "/").lstrip("/")
    path = (STATIC_DIR / rel_norm).resolve()
    root = STATIC_DIR.resolve()
    if root not in path.parents and path != root:
        raise FileNotFoundError(rel)
    if not path.is_file():
        raise FileNotFoundError(rel)
    return path


def _with_font_stack(css_text: str, font_family_stack: str) -> str:
    return css_text.replace("__LOCAL_FONT_FAMILY_STACK__", font_family_stack)


def build_head_tags(favicon_link: str) -> str:
    """Favicon + one ``<script src>`` per :data:`HEAD_SCRIPTS` entry.

    Gradio re-creates these head elements after hydration, so the scripts load
    from ``/diting-static`` exactly like the CSS does — edited files land on the
    next refresh while ``STATIC_HOT`` is on.
    """
    parts = [favicon_link]
    for rel in HEAD_SCRIPTS:
        parts.append(f'<script src="{STATIC_URL_PREFIX}/{rel}"></script>')
    return "".join(parts)


def gradio_css_import() -> str:
    """CSS entry for gr.Blocks(css=...): browser fetches theme on each load."""
    return '@import url("/diting-static/theme.css");\n'


def mount_static_routes(app, *, font_family_stack: str) -> None:
    """Register a FastAPI route that always reads files from disk."""
    from fastapi import HTTPException
    from fastapi.responses import Response

    cache_headers = (
        {"Cache-Control": "no-store, must-revalidate"}
        if STATIC_HOT
        else {"Cache-Control": "public, max-age=3600"}
    )

    @app.get(f"{STATIC_URL_PREFIX}/{{file_path:path}}")
    async def _serve_diting_static(file_path: str) -> Response:
        try:
            path = _safe_path(file_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc

        media, _ = mimetypes.guess_type(str(path))
        media = media or "application/octet-stream"
        if path.suffix.lower() == ".css":
            body = _with_font_stack(
                path.read_text(encoding="utf-8"), font_family_stack
            ).encode("utf-8")
            media = "text/css; charset=utf-8"
        else:
            body = path.read_bytes()
            if media.startswith("text/") or "javascript" in media:
                media = f"{media}; charset=utf-8"

        return Response(content=body, media_type=media, headers=cache_headers)
