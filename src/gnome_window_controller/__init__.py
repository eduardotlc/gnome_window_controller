# ruff: file-ignore[undefined-export]
"""
Created on 2026-07-19 08:03:12.

@author: eduardotc
@email: eduardotcampos@hotmail.com

Linux gnome windows utils init script.
"""

from __future__ import annotations

__author__ = "eduardotlc"
__license__ = "MIT"
__version__ = "1.2.1"

__all__ = [
    "COLORS",
    "MONITOR_DIRECTIONS",
    "DBusError",
    "GnomeWindowController",
    "HighlightError",
    "HighlightOptions",
    "Palette",
    "WindowControllerError",
    "WindowHighlighter",
    "__version__",
    "cycle_monitor",
    "main",
    "neighbor_monitor",
    "normalize_direction",
]


def __getattr__(name: str) -> object:
    """
    Resolve public names lazily so ``--help`` never imports PyGObject.

    Parameters
    ----------
    name : str
        Attribute being looked up.

    Returns
    -------
    object
        The requested attribute.

    Raises
    ------
    AttributeError
        If `name` is not part of the public API.

    """
    if name in {"COLORS", "Palette"}:
        from . import colors as module

        return getattr(module, name)

    if name in {"DBusError", "HighlightError", "WindowControllerError"}:
        from . import errors as module

        return getattr(module, name)

    if name in {
        "GnomeWindowController",
        "MONITOR_DIRECTIONS",
        "cycle_monitor",
        "neighbor_monitor",
        "normalize_direction",
    }:
        from . import gnome_window_controller as module

        return getattr(module, name)

    if name in {"HighlightOptions", "WindowHighlighter"}:
        from . import highlight as module

        return getattr(module, name)

    if name == "main":
        from .cli import main

        return main

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
