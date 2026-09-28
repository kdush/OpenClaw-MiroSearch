"""Smoke checks for Gradio static theme/scripts (no browser required)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

GRADIO_DEMO_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = GRADIO_DEMO_DIR / "static"


def _load_static_assets():
    if str(GRADIO_DEMO_DIR) not in sys.path:
        sys.path.insert(0, str(GRADIO_DEMO_DIR))
    module_name = "gradio_demo_static_assets_smoke"
    path = GRADIO_DEMO_DIR / "static_assets.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def static_assets():
    return _load_static_assets()


def _read(rel: str) -> str:
    return (STATIC_DIR / rel).read_text(encoding="utf-8")


def test_theme_entry_imports_all_sections(static_assets):
    entry = _read("theme.css")
    for rel in static_assets.THEME_CSS_PARTS:
        assert f"/diting-static/{rel}" in entry
        assert (STATIC_DIR / rel).is_file()


def test_theme_sections_contain_key_selectors(static_assets):
    assert "__LOCAL_FONT_FAMILY_STACK__" in _read("css/base.css")
    assert "#settings-modal" in _read("css/settings-modal.css")
    assert "#export-bar" in _read("css/export.css")
    assert "#log-view" in _read("css/output-log.css")
    assert ".search-card" in _read("css/search-cards.css")
    assert "@media" in _read("css/responsive.css")


def test_font_stack_substituted_in_css(static_assets):
    stack = '"Test Font", sans-serif'
    css = static_assets._with_font_stack(_read("css/base.css"), stack)
    assert stack in css
    assert "__LOCAL_FONT_FAMILY_STACK__" not in css


def test_head_scripts_exist_and_are_linked_externally(static_assets):
    head = static_assets.build_head_tags('<link rel="icon" href="/x">')
    for rel in static_assets.HEAD_SCRIPTS:
        assert (STATIC_DIR / rel).is_file()
        assert f'<script src="/diting-static/{rel}"></script>' in head


def test_starfield_has_perf_guards(static_assets):
    js = _read("js/starfield.js")
    assert "prefers-reduced-motion" in js
    assert "visibilitychange" in js
    assert "particleScale" in js
    assert "__miroStarfieldVer" in js


def test_gradio_css_import_points_at_theme(static_assets):
    assert "/diting-static/theme.css" in static_assets.gradio_css_import()
