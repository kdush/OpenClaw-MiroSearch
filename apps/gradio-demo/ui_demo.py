"""Wire Gradio layout to the demo module without circular imports.

``ui_layout.build_demo`` still uses unqualified names (handlers, I18N, …).
Before calling it we overlay those symbols from the loaded demo module
(``main`` / test-loaded module / ``__main__``).
"""

from __future__ import annotations

from typing import Any

import ui_layout

_SKIP = {"build_demo"}


def build_gradio_blocks(demo_module: Any):
    """Return ``gr.Blocks`` built against ``demo_module``'s callbacks/constants."""
    for name, value in vars(demo_module).items():
        # Dunders are module metadata; ``build_demo`` is ``ui_layout``'s own entry.
        if name.startswith("__") or name in _SKIP:
            continue
        setattr(ui_layout, name, value)
    return ui_layout.build_demo()
