"""
Created on 2026-08-13 08:10:00.

@author: eduardotc
@email: eduardotcampos@hotmail.com

Exception hierarchy shared by the controller and the highlight client.

Lives in its own module so :mod:`gnome_window_controller.gnome_window_controller` and
:mod:`gnome_window_controller.highlight` can both use it without importing each other at runtime.
"""

from __future__ import annotations

__all__ = ["DBusError", "HighlightError", "WindowControllerError"]


class WindowControllerError(RuntimeError):
    """Base error for every failure raised by this package."""


class DBusError(WindowControllerError):
    """Raised when a D-Bus call to GNOME Shell or one of its extensions fails."""


class HighlightError(WindowControllerError):
    """Raised when the highlight extension is missing or cannot be driven."""
