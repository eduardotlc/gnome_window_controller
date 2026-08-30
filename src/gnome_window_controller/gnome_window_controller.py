#!/usr/bin/env python3
"""
Created on 2025-09-04 12:14:41.

@author: eduardotc
@email: eduardotcampos@hotmail.com

Gnome windows focus and browsing gdbus extension manager and integration.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import warnings
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Any

from .errors import DBusError, WindowControllerError
from .highlight import HIGHLIGHT_IFACE, HIGHLIGHT_PATH, WindowHighlighter

# Adding backward compatibility to 3.13 >= python >= 3.8
try:
    from collections.abc import Sequence
except ImportError:
    from typing import Sequence  # ruff: ignore[deprecated-import]

__all__ = [
    "DEFAULT_EXCLUDED_APPS",
    "MONITOR_DIRECTIONS",
    "DBusError",
    "GnomeWindowController",
    "WindowControllerError",
    "cycle_monitor",
    "neighbor_monitor",
    "normalize_direction",
]

#: Assumed monitor order when ``org.gnome.Mutter.DisplayConfig`` cannot be read at all: a single
#: monitor, index 0. The real order comes from the live layout, so nothing here is tied to any
#: particular desk.
MONITOR_ORDER = (0,)

#: Directions :func:`neighbor_monitor` understands, and the words ``--chfocus`` accepts.
MONITOR_DIRECTIONS = ("left", "right", "up", "down")

#: Windows no focus command lands on unless the caller clears the list. A Picture-in-Picture
#: overlay floats above everything and is virtually never what a focus shortcut is reaching for.
DEFAULT_EXCLUDED_APPS = ("Picture-in-Picture",)

#: Spellings accepted alongside :data:`MONITOR_DIRECTIONS`. The integers are what
#: ``cycle_monitors`` took before it grew vertical directions.
_DIRECTION_ALIASES = {
    "1": "right",
    "-1": "left",
    "e": "right",
    "east": "right",
    "w": "left",
    "west": "left",
    "n": "up",
    "north": "up",
    "s": "down",
    "south": "down",
}

#: Mutter transforms that turn a monitor on its side, so its mode's width and height swap.
_ROTATED_TRANSFORMS = frozenset({1, 3, 5, 7})

DISPLAY_CONFIG_BUS = "org.gnome.Mutter.DisplayConfig"

DISPLAY_CONFIG_PATH = "/org/gnome/Mutter/DisplayConfig"

#: Window Calls (``window-calls@domandoman.xyz``), the third-party extension this module used
#: before the bundled one grew a window API. Kept only so a session whose GNOME Shell has not yet
#: loaded the new extension keeps working until the next log in.
LEGACY_WINDOWS_PATH = "/org/gnome/Shell/Extensions/Windows"

LEGACY_WINDOWS_IFACE = "org.gnome.Shell.Extensions.Windows"

#: D-Bus error names meaning "nothing serves that interface here", as opposed to "the call itself
#: failed". Only these are worth retrying against :data:`LEGACY_WINDOWS_IFACE`.
_MISSING_IFACE_ERRORS = (
    "UnknownMethod",
    "UnknownInterface",
    "UnknownObject",
    "ServiceUnknown",
    "NameHasNoOwner",
)

#: Keys under which the various extension builds report the focus flag.
FOCUS_KEYS = ("focus", "has_focus", "focused")

#: Fields coerced to ``int`` on every ``Details()`` payload.
_INT_FIELDS = (
    "monitor",
    "x",
    "y",
    "width",
    "height",
    "pid",
    "id",
    "layer",
    "frame_type",
    "window_type",
    "maximized",
    "workspace",
)


def normalize_direction(direction: int | str) -> str:
    """
    Map any accepted spelling of a direction onto one of :data:`MONITOR_DIRECTIONS`.

    Parameters
    ----------
    direction : int or str
        A name (``"left"``, ``"right"``, ``"up"``, ``"down"``), one of the aliases in
        :data:`_DIRECTION_ALIASES`, or a non-zero integer -- positive for right, negative for
        left, the convention ``cycle_monitors`` used before vertical layouts were supported.

    Returns
    -------
    str
        The canonical direction name.

    Raises
    ------
    ValueError
        If `direction` names no known direction, or is zero.

    Examples
    --------
    >>> normalize_direction("up")
    'up'
    >>> normalize_direction(1), normalize_direction(-1)
    ('right', 'left')
    >>> normalize_direction("NORTH")
    'up'

    """
    if isinstance(direction, bool):
        raise ValueError("direction must be a name or a non-zero integer, not a bool")

    if isinstance(direction, int):
        if direction == 0:
            raise ValueError("direction must be negative or positive")
        return "right" if direction > 0 else "left"

    name = str(direction).strip().lower()
    if name in MONITOR_DIRECTIONS:
        return name
    if name in _DIRECTION_ALIASES:
        return _DIRECTION_ALIASES[name]
    raise ValueError(
        f"unknown monitor direction {direction!r}; expected one of {', '.join(MONITOR_DIRECTIONS)}",
    )


def _axis_span(monitor: dict[str, Any], start_key: str, size_key: str) -> tuple[float, float]:
    """
    Return a monitor's extent along one axis.

    Parameters
    ----------
    monitor : dict
        Entry as produced by :meth:`GnomeWindowController.monitor_layout`.
    start_key : str
        ``"x"`` or ``"y"``.
    size_key : str
        ``"width"`` or ``"height"``.

    Returns
    -------
    tuple of float
        ``(start, end)``. A layout that could not report sizes gives a zero-length span, which
        still orders correctly even though it cannot express overlap.

    Examples
    --------
    >>> _axis_span({"x": 1920, "width": 2560}, "x", "width")
    (1920.0, 4480.0)

    """
    start = float(_as_int(monitor.get(start_key)) or 0)
    return start, start + float(_as_int(monitor.get(size_key)) or 0)


def neighbor_monitor(
    monitors: Sequence[dict[str, Any]],
    current_monitor: int,
    direction: int | str,
    *,
    wrap: bool = True,
) -> int | None:
    r"""
    Return the monitor index that lies `direction` of `current_monitor`.

    Works off the real layout rectangles rather than a fixed ordering, so any arrangement is
    handled: a single row, a vertical stack, an L, a grid, or monitors of different sizes and
    scales that only partly line up.

    A candidate has to sit clear of the current monitor along the travel axis -- a taller screen
    in the same row is beside its neighbours, not above them. Among the candidates, monitors that
    share screen rows with the current one (for `left`/`right`; columns for `up`/`down`) win over
    ones that do not, then the nearest wins. A monitor that shares no row is still eligible, so
    an L-shaped desk can be crossed diagonally rather than leaving a screen unreachable.

    With nothing that way and `wrap` set, the search comes back around from the far side, which
    is what makes repeated ``--chfocus right`` cycle instead of stopping at the edge.

    Parameters
    ----------
    monitors : sequence of dict
        Entries as produced by :meth:`GnomeWindowController.monitor_layout`; each needs
        ``index``, ``x``, ``y``, ``width`` and ``height``.
    current_monitor : int
        Index to move away from.
    direction : int or str
        Anything :func:`normalize_direction` accepts.
    wrap : bool, optional
        Come back around from the opposite edge when nothing lies that way. Default is True.

    Returns
    -------
    int or None
        The neighbouring monitor's index, or None when there is nowhere to go -- a lone monitor,
        an unknown `current_monitor`, or `wrap` disabled at the edge.

    Raises
    ------
    ValueError
        If `direction` names no known direction.

    Examples
    --------
    Three monitors side by side, the middle one larger, listed out of order on purpose because
    Mutter's indices follow no particular geometry:

    >>> row = [
    ...     {"index": 0, "x": 4480, "y": 0, "width": 1920, "height": 1080},
    ...     {"index": 1, "x": 1920, "y": 0, "width": 2560, "height": 1440},
    ...     {"index": 2, "x": 0, "y": 0, "width": 1920, "height": 1080},
    ... ]
    >>> neighbor_monitor(row, 2, "right")
    1
    >>> neighbor_monitor(row, 1, "right")
    0
    >>> neighbor_monitor(row, 0, "right")
    2
    >>> neighbor_monitor(row, 0, "right", wrap=False) is None
    True
    >>> neighbor_monitor(row, 1, "up") is None
    True

    A laptop with an external screen above it:

    >>> stack = [
    ...     {"index": 0, "x": 0, "y": 1080, "width": 1920, "height": 1080},
    ...     {"index": 1, "x": 0, "y": 0, "width": 1920, "height": 1080},
    ... ]
    >>> neighbor_monitor(stack, 0, "up")
    1
    >>> neighbor_monitor(stack, 1, "down")
    0

    An L-shaped desk, with the third screen above the right-hand one. Going up from the left
    monitor crosses diagonally rather than reporting nothing:

    >>> ell = [
    ...     {"index": 0, "x": 0, "y": 1080, "width": 1920, "height": 1080},
    ...     {"index": 1, "x": 1920, "y": 1080, "width": 1920, "height": 1080},
    ...     {"index": 2, "x": 1920, "y": 0, "width": 1920, "height": 1080},
    ... ]
    >>> neighbor_monitor(ell, 1, "up")
    2
    >>> neighbor_monitor(ell, 0, "up")
    2

    In a 2x2 grid, going right stays in the same row, and wrapping does too:

    >>> grid = [
    ...     {"index": 0, "x": 0, "y": 0, "width": 1920, "height": 1080},
    ...     {"index": 1, "x": 1920, "y": 0, "width": 1920, "height": 1080},
    ...     {"index": 2, "x": 0, "y": 1080, "width": 1920, "height": 1080},
    ...     {"index": 3, "x": 1920, "y": 1080, "width": 1920, "height": 1080},
    ... ]
    >>> neighbor_monitor(grid, 2, "right")
    3
    >>> neighbor_monitor(grid, 3, "right")
    2
    >>> neighbor_monitor(grid, 3, "up")
    1

    """
    heading = normalize_direction(direction)
    by_index = {int(m["index"]): m for m in monitors if m.get("index") is not None}

    current = by_index.get(int(current_monitor))
    if current is None or len(by_index) < 2:
        return None

    horizontal = heading in {"left", "right"}
    forward = heading in {"right", "down"}
    travel = ("x", "width") if horizontal else ("y", "height")
    across = ("y", "height") if horizontal else ("x", "width")

    def centre(monitor: dict[str, Any], axis: tuple[str, str]) -> float:
        low, high = _axis_span(monitor, *axis)
        return (low + high) / 2

    here = centre(current, travel)
    here_low, here_high = _axis_span(current, *travel)
    lane_low, lane_high = _axis_span(current, *across)
    here_across = centre(current, across)

    # A neighbour has to sit clear of the current monitor along the travel axis, not merely have
    # a different centre. Otherwise a taller screen in the same row reads as being "above" the
    # short ones beside it, and `up` would jump sideways.
    is_ahead = (lambda c: c > here_high) if forward else (lambda c: c < here_low)
    is_behind = (lambda c: c < here_low) if forward else (lambda c: c > here_high)

    def shares_a_lane(monitor: dict[str, Any]) -> int:
        low, high = _axis_span(monitor, *across)
        return 0 if min(lane_high, high) - max(lane_low, low) > 0 else 1

    ahead: list[tuple[int, float, float, int]] = []
    behind: list[tuple[int, float, float, int]] = []
    for index, monitor in by_index.items():
        if index == int(current_monitor):
            continue
        there = centre(monitor, travel)
        lane = shares_a_lane(monitor)
        drift = abs(centre(monitor, across) - here_across)
        if is_ahead(there):
            ahead.append((lane, abs(there - here), drift, index))
        elif is_behind(there):
            # Kept for the wrap-around pass, where the *farthest* one back is the one wanted.
            behind.append((lane, -abs(there - here), drift, index))

    if ahead:
        return min(ahead)[3]
    if not wrap or not behind:
        return None
    return min(behind)[3]


def cycle_monitor(
    current_monitor: int,
    direction: int,
    monitor_order: Sequence[int],
) -> int:
    """
    Return the next monitor in a flat left-to-right ring.

    A pure ordering helper, kept for callers that already know their monitor order and want plain
    ring behaviour. Anything that has the real layout to hand should prefer
    :func:`neighbor_monitor`, which understands vertical and mixed arrangements.

    Parameters
    ----------
    current_monitor : int
        ID of the currently focused monitor.
    direction : int
        Direction to move. A negative value moves left and a positive value moves right.
    monitor_order : sequence of int
        Monitor IDs arranged in their physical left-to-right order, as returned by
        :meth:`GnomeWindowController.monitor_order`.

    Returns
    -------
    int
        ID of the next monitor.

    Raises
    ------
    ValueError
        If the current monitor is not in `monitor_order`, or direction is zero.

    Examples
    --------
    >>> order = (2, 1, 0)
    >>> cycle_monitor(2, 1, order)
    1
    >>> cycle_monitor(1, 1, order)
    0
    >>> cycle_monitor(0, 1, order)
    2
    >>> cycle_monitor(2, -1, order)
    0

    """
    if direction == 0:
        raise ValueError("direction must be negative or positive")

    monitor_order = tuple(monitor_order)

    try:
        current_index = monitor_order.index(current_monitor)
    except ValueError as error:
        raise ValueError(f"monitor {current_monitor!r} is not in {monitor_order!r}") from error

    step = 1 if direction > 0 else -1
    next_index = (current_index + step) % len(monitor_order)

    return monitor_order[next_index]


@dataclass
class GnomeWindowController:
    """
    Manage GNOME windows via D-Bus with lazy PyGObject imports.

    Listing, describing and activating windows, reporting which one has focus, and the
    focused-window border are all served by the GNOME Shell extension bundled in this package,
    which the module installs itself; see :mod:`gnome_window_controller.highlight`. No third-party
    extension is required.

    Parameters
    ----------
    shell_bus_name : str, optional
        The GNOME Shell bus name to call, by default "org.gnome.Shell".
    windows_path : str, optional
        The D-Bus object path serving the window API, by default the bundled extension's
        "/org/gnome/Shell/Extensions/GnomeWindowController".
    windows_iface : str, optional
        The D-Bus interface serving the window API, by default the bundled extension's
        "org.gnome.Shell.Extensions.GnomeWindowController".
    focused_path : str, optional
        Object path of the deprecated Focused Window D-Bus extension, consulted only when the
        bundled one does not answer. Default "/org/gnome/shell/extensions/FocusedWindow".
    focused_iface : str, optional
        Interface of the deprecated Focused Window D-Bus extension.
        Default "org.gnome.shell.extensions.FocusedWindow".
    highlight_on_focus : bool, optional
        Ask the bundled extension to flash a border around every window this controller focuses.
        Silently skipped when the extension is not installed. Default is True.
    exclude_apps : sequence of str, optional
        Names no focus command may land on, matched the way ``--focus`` matches: a
        case-insensitive substring of a window's wm_class, instance or title. Listing and
        inspection are unaffected. Defaults to :data:`DEFAULT_EXCLUDED_APPS`; pass ``()`` for
        none.
    windows_fallback : bool, optional
        Retry window queries against the deprecated Window Calls extension when the bundled one
        does not answer. Only useful between installing a new extension version and the log in
        that makes GNOME Shell load it. Default is True.

    Attributes
    ----------
    shell_bus_name : str, default "org.gnome.Shell"
    windows_path : str, default "/org/gnome/Shell/Extensions/GnomeWindowController"
    windows_iface : str, default "org.gnome.Shell.Extensions.GnomeWindowController"
    focused_path : str, default "/org/gnome/shell/extensions/FocusedWindow"
    focused_iface : str, default "org.gnome.shell.extensions.FocusedWindow"
    highlight_on_focus : bool, default True
    windows_fallback : bool, default True
    exclude_apps : sequence of str, default DEFAULT_EXCLUDED_APPS
    highlight : gnome_window_controller.highlight.WindowHighlighter
    _gio : gi.overrides.OverridesProxyModule
    _glib : gi.overrides.OverridesProxyModule
    _bus : gi.repository.Gio.DBusConnection
    _details_cache : dict[int, dict[str, Any]]
    _title_cache : dict[int, str]

    Examples
    --------
    >>> gnome_win = GnomeWindowController()
    >>> wins = gnome_win.list_windows()
    >>> assert isinstance(wins, list)
    """

    shell_bus_name: str = "org.gnome.Shell"
    windows_path: str = HIGHLIGHT_PATH
    windows_iface: str = HIGHLIGHT_IFACE
    focused_path: str = "/org/gnome/shell/extensions/FocusedWindow"
    focused_iface: str = "org.gnome.shell.extensions.FocusedWindow"
    highlight_on_focus: bool = True
    windows_fallback: bool = True
    exclude_apps: Sequence[str] = DEFAULT_EXCLUDED_APPS

    # Internal lazy state (do not set manually)
    _gio: Any = field(default=None, init=False, repr=False)
    _glib: Any = field(default=None, init=False, repr=False)
    _bus: Any = field(default=None, init=False, repr=False)
    _glib_error: type[BaseException] = field(default=Exception, init=False, repr=False)
    _details_cache: dict[int, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _monitor_order: tuple[int, ...] | None = field(default=None, init=False, repr=False)
    _monitor_layout: list[dict[str, Any]] | None = field(default=None, init=False, repr=False)
    _windows_target: tuple[str, str] | None = field(default=None, init=False, repr=False)
    _highlight: WindowHighlighter | None = field(default=None, init=False, repr=False)

    # Small cache for window titles to avoid spamming D-Bus.
    _title_cache: dict[int, str] = field(default_factory=dict, init=False, repr=False)

    # ------------------------------ Lazy setup helpers ------------------------------

    @property
    def highlight(self) -> WindowHighlighter:
        """
        Focused-window highlight client, created on first use and returned.

        Returns
        -------
        gnome_window_controller.highlight.WindowHighlighter
        """
        if self._highlight is None:
            self._highlight = WindowHighlighter(self)
        return self._highlight

    def _ensure_gi(self) -> None:
        """
        Ensure lazy import of gi.repository components.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> assert gnome_win._gio is None

        >>> gnome_win._ensure_gi()
        >>> print(type(gnome_win._gio))
        <class 'gi.overrides.OverridesProxyModule'>
        """
        if self._gio is not None and self._glib is not None:
            return
        # Lazy import: happens only when a method is first used.
        from gi.repository import Gio, GLib

        self._gio = Gio
        self._glib = GLib
        self._glib_error = GLib.Error
        self._bus = None  # Reset to force (re)connect on next use.

    def _connection(self) -> Any:
        """
        Return a session D-Bus connection, creating it on first use.

        Returns
        -------
        gi.repository.Gio.DBusConnection

        Raises
        ------
        DBusError
            If the session bus cannot be reached.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> conn = gnome_win._connection()
        >>> print(type(conn))
        <class 'gi.repository.Gio.DBusConnection'>
        """
        self._ensure_gi()
        if self._bus is None:
            try:
                self._bus = self._gio.bus_get_sync(self._gio.BusType.SESSION, None)
            except self._glib_error as error:
                raise DBusError(f"cannot connect to the session bus: {error}") from error
        return self._bus

    def _call(
        self,
        path: str,
        iface: str,
        method: str,
        params: Any | None = None,
        dest: str | None = None,
    ) -> Any:
        """
        Call a D-Bus method and unpack the returned GLib.Variant.

        Parameters
        ----------
        path : str
            Object path to call.
        iface : str
            D-Bus interface name.
        method : str
            D-Bus method name.
        params : any, optional
            Parameters packed as a GLib.Variant or `None` for no parameters.
        dest : str, optional
            Destination bus name. Defaults to :attr:`shell_bus_name`.

        Returns
        -------
        any
            Unpacked return value (plain Python objects).

        Raises
        ------
        DBusError
            If the call fails, typically because the backing extension is missing or disabled.


        Notes
        -----
        A reply is always a tuple, one element per output argument, so a method returning a single
        JSON string arrives wrapped. ``_windows_call("List")`` for instance yields

        ('[{"in_current_workspace":true,"workspace":0,"monitor":1,"wm_class":"Floorp",\
                "wm_class_instance":"Floorp","title":"Optimizing cheatsheet app",\
                "pid":6429,"id":629149937,"frame_type":0,"window_type":0,"focus":false,\
                "minimized":false,"x":0,"y":45,"width":1920,"height":1035},\
                {"in_current_workspace":true,"workspace":0,"monitor":0,"wm_class":"kitty",\
                "wm_class_instance":"kitty","title":"pytest gi_utils.py","pid":5195,\
                "id":629149936,"frame_type":0,"window_type":0,"focus":true,"minimized":false,\
                "x":1920,"y":45,"width":1920,"height":1035}]',)

        Examples
        --------
        ``Ping`` is used here rather than ``List`` because every build of the extension answers it,
        including one an older GNOME Shell session is still holding in memory.

        >>> gnome_win = GnomeWindowController()
        >>> raw = gnome_win._call(
        ...     path=gnome_win.windows_path,
        ...     iface=gnome_win.windows_iface,
        ...     method="Ping",
        ...     params=None,
        ... )
        >>> assert isinstance(raw, tuple)
        >>> assert raw[0].isdigit()
        """
        conn = self._connection()
        params_variant = params if params is not None else self._glib.Variant("()", ())
        try:
            res = conn.call_sync(
                dest or self.shell_bus_name,
                path,
                iface,
                method,
                params_variant,
                None,
                self._gio.DBusCallFlags.NO_AUTO_START,
                -1,
                None,
            )
        except self._glib_error as error:
            raise DBusError(f"{iface}.{method} failed: {error}") from error
        return res.unpack()

    def _windows_call(self, method: str, params: Any | None = None) -> Any:
        """
        Call a window-query method, falling back to Window Calls only if it has to.

        ``List``, ``Details``, ``GetTitle`` and ``Activate`` are served by the extension bundled
        in this package. A freshly installed copy is not loaded until the next log in, though, so
        while the running GNOME Shell still has an older build the call is retried against the
        deprecated Window Calls extension. Only "nothing serves this interface" errors trigger the
        retry; a genuine failure such as an unknown window id is raised as is. The target that
        answered is remembered for the rest of the process.

        Parameters
        ----------
        method : str
            D-Bus method name.
        params : any, optional
            Packed ``GLib.Variant`` arguments, or None.

        Returns
        -------
        any
            Unpacked reply tuple.

        Raises
        ------
        DBusError
            If the bundled extension does not answer and no fallback picks up the call.

        Warns
        -----
        RuntimeWarning
            The first time a call falls back to the Window Calls extension.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> raw = gnome_win._windows_call("List")
        >>> assert isinstance(raw, tuple)
        >>> assert isinstance(raw[0], str)

        """
        if self._windows_target is not None:
            path, iface = self._windows_target
            return self._call(path=path, iface=iface, method=method, params=params)

        try:
            result = self._call(
                path=self.windows_path,
                iface=self.windows_iface,
                method=method,
                params=params,
            )
        except DBusError as error:
            if not self.windows_fallback or not any(
                name in str(error) for name in _MISSING_IFACE_ERRORS
            ):
                raise
            result = self._call(
                path=LEGACY_WINDOWS_PATH,
                iface=LEGACY_WINDOWS_IFACE,
                method=method,
                params=params,
            )
            self._windows_target = (LEGACY_WINDOWS_PATH, LEGACY_WINDOWS_IFACE)
            warnings.warn(
                f"{self.windows_iface} does not serve {method}; falling back to the deprecated "
                "Window Calls extension. Install the bundled extension "
                "(`gnome-window-controller --highlight install`) and log out and back in to stop "
                "depending on it.",
                RuntimeWarning,
                stacklevel=2,
            )
            return result

        self._windows_target = (self.windows_path, self.windows_iface)
        return result

    # ------------------------------ Public API ------------------------------

    def _deep_unpack(self, obj: object) -> object:
        """
        Recursively unpack GLib.Variant values into native Python.

        Returns
        -------
        object
            A Python-native object (dict/list/tuple/scalar) with all nested Variants unpacked.

        Notes
        -----
        Given above `_call` note example `tuple`, passing the first element of it (tuple[0]) to
        this function returns the `str`:

        [{"in_current_workspace":true,"wm_class":"xwaylandvideobridge","wm_class_instance":\
                "xwaylandvideobridge","title":"Wayland to X Recording bridge — \
                Xwayland Video Bridge","pid":4436,"id":629149935,"frame_type":0,"window_type":\
                0,"focus":false},{"in_current_workspace":true,"wm_class":"Floorp",\
                "wm_class_instance":"Floorp","title":"Optimizing cheatsheet app — Ablaze Floorp",\
                "pid":6429,"id":629149937,"frame_type":0,"window_type":0,"focus":false},\
                {"in_current_workspace":true,"wm_class":"kitty","wm_class_instance":"kitty",\
                "title":"pytest gi_utils.py","pid":5195,"id":629149936,"frame_type":0,\
                "window_type":0,"focus":true}]

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> raw = gnome_win._windows_call("List")
        >>> dunpacked = gnome_win._deep_unpack(raw[0])
        >>> assert isinstance(dunpacked, str)

        """
        # Keep unwrapping Variant until native.
        while hasattr(obj, "unpack"):  # type: ignore[attr-defined]
            obj = obj.unpack()  # type: ignore[attr-defined]

        # Some bindings may yield bytes; decode to str.
        if isinstance(obj, (bytes, bytearray)):
            return bytes(obj).decode("utf-8", errors="replace")

        if isinstance(obj, dict):
            return {k: self._deep_unpack(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._deep_unpack(v) for v in obj]
        return obj

    def _maybe_parse_json(self, data: object) -> object:
        """
        Parse JSON-encoded payloads returned as strings by some extension builds.

        Parameters
        ----------
        data : object
            Unpacked value after GLib.Variant processing.

        Returns
        -------
        object
            Original data if not JSON-looking, otherwise the parsed Python object.

        Notes
        -----
        Return of this function given the above `_deep_unpack` note example element, a `str`,
        returning the following `dict`

        `dict` = [
            {
                "in_current_workspace": True,
                "wm_class": "xwaylandvideobridge",
                "wm_class_instance": "xwaylandvideobridge",
                "title": "Wayland to X Recording bridge — Xwayland Video Bridge",
                "pid": 4436,
                "id": 629149935,
                "frame_type": 0,
                "window_type": 0,
                "focus": False,
            },
            {
                "in_current_workspace": True,
                "wm_class": "Floorp",
                "wm_class_instance": "Floorp",
                "title": "Optimizing cheatsheet app — Ablaze Floorp",
                "pid": 6429,
                "id": 629149937,
                "frame_type": 0,
                "window_type": 0,
                "focus": False,
            },
            {
                "in_current_workspace": True,
                "wm_class": "kitty",
                "wm_class_instance": "kitty",
                "title": "pytest gi_utils.py",
                "pid": 5195,
                "id": 629149936,
                "frame_type": 0,
                "window_type": 0,
                "focus": True,
            },
        ]

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> raw = gnome_win._windows_call("List")
        >>> dunpacked = gnome_win._deep_unpack(raw[0])
        >>> json_parsed = gnome_win._maybe_parse_json(dunpacked)
        >>> assert isinstance(json_parsed, list)
        >>> assert isinstance(json_parsed[0], dict)
        >>> assert "title" in json_parsed[0]

        """
        if isinstance(data, str):
            s = data.strip()
            # Heuristic: looks like top-level JSON array/object.
            if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
                try:
                    return json.loads(s)
                except JSONDecodeError:
                    # Leave as-is if it wasn't valid JSON.
                    return data
        return data

    def _normalize_entry(self, item: object) -> list[dict[str, object]]:
        """
        Convert a single 'windows list' entry into one or more dict entries.

        Returns
        -------
        list of dict
            One or more normalized window dicts extracted from `item`.

        Raises
        ------
        ValueError
            If the entry shape is not recognized.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> raw = gnome_win._windows_call("List")
        >>> dunpacked = gnome_win._deep_unpack(raw[0])
        >>> json_parsed = gnome_win._maybe_parse_json(dunpacked)
        >>> norm = gnome_win._normalize_entry(json_parsed)
        >>> assert isinstance(norm, list)
        >>> assert isinstance(norm[0], dict)
        >>> assert "title" in norm[0]
        >>> assert isinstance(norm[0]["title"], str)

        A session with no open windows yields no entries rather than an error:

        >>> gnome_win._normalize_entry("[]")
        []
        >>> gnome_win._normalize_entry([])
        []
        """
        # Fully unpack + JSON-decode if needed.
        item = self._maybe_parse_json(self._deep_unpack(item))

        # Empty payload: a session can legitimately have no windows at all.
        if item is None or (isinstance(item, (list, tuple)) and not item):
            return []

        # Already a single window dict.
        if isinstance(item, dict):
            return [item]

        # GNOME extension sometimes returns a *batch*: [ {..}, {..}, ... ]
        if isinstance(item, (list, tuple)) and item and all(isinstance(x, dict) for x in item):
            return [dict(x) for x in item]

        # Pair-list form: [["id", 123], ["wm_class", "..."]]
        if (
            isinstance(item, (list, tuple))
            and item
            and all(isinstance(p, (list, tuple)) and len(p) == 2 for p in item)
        ):
            return [dict(item)]  # type: ignore[misc]

        # Single wrapper level — unwrap once more.
        if isinstance(item, (list, tuple)) and len(item) == 1:
            inner = self._maybe_parse_json(self._deep_unpack(item[0]))
            return self._normalize_entry(inner)

        raise ValueError(f"Unrecognized window entry shape: {item!r}")

    def list_windows(self, *, with_monitor: bool = False) -> list[dict[str, object]]:
        """
        List the windows the bundled shell extension reports.

        Parameters
        ----------
        with_monitor : bool, optional
            Also resolve each window's monitor through ``Details(id)``. Default is False.

        Returns
        -------
        list of dict
            Each dictionary includes: 'id', 'wm_class', 'wm_class_instance', 'title', 'pid',
            'workspace', 'in_current_workspace', 'monitor', 'frame_type', 'window_type', 'focus',
            'minimized' and the frame rect as 'x', 'y', 'width', 'height'. Exact fields depend on
            the extension version; older ones omit 'monitor' and the frame rect, which is what
            `with_monitor` is for.

        Raises
        ------
        TypeError
            If obtained raw list windows from call, after `_deep_unpack`, and filtering by
            "windows" key element if exists, dont match `list` type

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> wins = gnome_win.list_windows()
        >>> assert isinstance(wins, list)
        >>> assert isinstance(wins[0], dict)
        >>> assert "title" in wins[0]
        >>> assert isinstance(wins[0]["title"], str)
        """
        self._title_cache.clear()
        self._details_cache.clear()

        raw = self._windows_call("List")

        data = self._maybe_parse_json(self._deep_unpack(raw))

        # Accept either {"windows": [...]} or a bare list.
        items: object
        items = data["windows"] if isinstance(data, dict) and "windows" in data else data

        if not isinstance(items, list):
            raise TypeError(f"Unexpected List() payload type: {type(items)!r} → {items!r}")

        normalized: list[dict[str, object]] = []
        for item in items:
            normalized.extend(self._normalize_entry(item))

        # Titles come back with the listing; seed the cache so `_matches` never needs GetTitle().
        for win in normalized:
            wid = _as_int(win.get("id"))
            title = win.get("title")
            if wid is not None and isinstance(title, str):
                self._title_cache[wid] = title

        if with_monitor:
            self.relabel_monitors_via_details(normalized)

        return normalized

    def _coerce_mapping(self, obj: object) -> dict[str, Any] | None:
        """
        Convert a Variant/JSON/pairs structure into a Python dict.

        Try multiple shapes: dict already, JSON string/object, list of [key, value] pairs, or a
        single-element wrapper that contains one of the former. Returns None if it cannot coerce.

        Parameters
        ----------
        obj : object

        Returns
        -------
        dict or None
            A normalized dictionary, or None if the object cannot be interpreted as a mapping.

        Notes
        -----
        Obtained above coerced dict has formatting simillar to the one detailed in `get_focused`

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> raw = gnome_win._windows_call("List")
        >>> dunpacked = gnome_win._deep_unpack(raw[0])
        >>> json_parsed = gnome_win._maybe_parse_json(dunpacked)

        From the `json_parsed` list, each elements matches a window instance.
        Arbitrary in this example, i will choose the lest window element of it to coerce mapping.

        >>> coerced_test = gnome_win._coerce_mapping(json_parsed[-1])
        >>> assert isinstance(coerced_test, dict)
        >>> assert "wm_class" in coerced_test

        """
        obj = self._maybe_parse_json(self._deep_unpack(obj))

        if obj is None:
            return None

        if isinstance(obj, dict):
            # Ensure nested values are native too.
            return {k: self._deep_unpack(v) for k, v in obj.items()}

        if isinstance(obj, (list, tuple)):
            # Form: [["id", 123], ["title", "..."], ...]
            if obj and all(isinstance(p, (list, tuple)) and len(p) == 2 for p in obj):
                return dict(obj)
            # Single wrapper → recurse.
            if len(obj) == 1:
                return self._coerce_mapping(obj[0])

        # Strings that look like JSON were already parsed by _maybe_parse_json.
        return None

    def _focused_payload(self) -> object:
        """
        Ask the shell which window has focus, bundled extension first.

        Three sources are tried in order: the bundled extension's ``GetFocused``, then Focused
        Window D-Bus if that extension happens to be installed, and finally nothing -- leaving
        :meth:`get_focused` to scan :meth:`list_windows`. An empty reply counts as "no answer", so
        a shell that reports nothing focused simply moves the question down the chain.

        Returns
        -------
        object
            The raw reply tuple from whichever source answered, or None.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> payload = gnome_win._focused_payload()
        >>> assert payload is None or isinstance(payload, tuple)

        """
        try:
            payload = self._windows_call("GetFocused")
        except DBusError:
            payload = None
        if isinstance(payload, tuple) and payload and payload[0]:
            return payload

        try:
            return self._call(
                path=self.focused_path,
                iface=self.focused_iface,
                method="Get",
                params=None,
            )
        except DBusError:
            return None

    def get_focused(self) -> dict[str, Any] | None:
        """
        Get the currently focused window.

        Asks :meth:`_focused_payload` and coerces whatever shape it returns into a mapping. If no
        usable id comes back, fall back to scanning `list_windows()` for the focused flag.

        Returns
        -------
        dict or None
            A dict describing the focused window (ideally with 'id'), or None if unknown.

        Notes
        -----
        Served by the bundled extension, the returned dict carries the same keys as one
        `list_windows()` entry:
        - 'in_current_workspace' : bool
        - 'workspace' : int
        - 'monitor' : int
        - 'wm_class' : str
        - 'wm_class_instance' : str
        - 'title' : str
        - 'pid' : int
        - 'id' : int
        - 'frame_type' : int
        - 'window_type' : int
        - 'focus' : bool
        - 'minimized' : bool
        - 'x', 'y', 'width', 'height' : int

        Older Focused Window D-Bus replies carry a subset of those, without 'workspace',
        'monitor', 'minimized' and the frame rect.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> focused = gnome_win.get_focused()
        >>> assert isinstance(focused, dict)
        >>> print(focused["focus"])
        True

        """
        mapping = self._coerce_mapping(self._focused_payload())
        if mapping:
            # Some builds expose title/class but not id; keep it anyway.
            if "id" in mapping:
                wid = _as_int(mapping["id"])
                if wid is not None:
                    mapping["id"] = wid
                return mapping
            # If there is no id, augment with the one flagged as focused in the windows list.
            focused = self._focused_from_list()
            return focused or mapping

        # Hard fallback: derive from the windows list if the extension gave us nothing usable.
        try:
            wins = self.list_windows()
        except (DBusError, TypeError, ValueError):
            return None
        return self._focused_from_list(wins) or (wins[0] if wins else None)

    def _focused_from_list(
        self,
        windows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """
        Return the window flagged as focused inside a listing.

        Parameters
        ----------
        windows : list of dict, optional
            Listing to scan. Fetched with ``list_windows()`` when omitted.

        Returns
        -------
        dict or None
            The focused window entry, or None if none is flagged.
        """
        if windows is None:
            try:
                windows = self.list_windows()
            except (DBusError, TypeError, ValueError):
                return None
        return next((w for w in windows if any(bool(w.get(k)) for k in FOCUS_KEYS)), None)

    def _coerce_details_mapping(
        self,
        obj: object,
        *,
        expected_id: int | None = None,
    ) -> dict[str, Any] | None:
        """
        Coerce a Details(id) payload into a Python dict.

        Try multiple shapes: dict already, JSON str/object, list of dicts (pick by id if possible),
        list of [key, value] pairs, or a single-element wrapper. Returns None if it cannot coerce.

        Parameters
        ----------
        obj : object
            Raw value returned by the D-Bus call (may be nested GLib.Variant).
        expected_id : int or None, optional
            If a list of dicts is returned, prefer the one whose 'id' matches this value.

        Returns
        -------
        dict or None
            Normalized mapping for the details payload, or None if unrecognized.

        Notes
        -----
        Default obtained dict after coerced_maps is formatted with the following keys and values:
        - "in_current_workspace" : bool
        - "workspace" : int
        - "monitor" : int
        - "wm_class" : str
        - "wm_class_instance" : str
        - "title" : str
        - "pid" : int
        - "id" : int
        - "frame_type" : int
        - "window_type" : int
        - "focus" : bool
        - "minimized" : bool
        - "x" : int
        - "y" : int
        - "width" : int
        - "height" : int
        - "role" : str or None
        - "layer" : int
        - "maximized" : int
        - "maximized_horizontally" : bool
        - "maximized_vertically" : bool
        - "fullscreen" : bool
        - "moveable" : bool
        - "resizeable" : bool
        - "canclose" : bool
        - "canmaximize" : bool
        - "canminimize" : bool
        - "canshade" : bool
        - "area" : dict with "x", "y", "width", "height"
        - "area_all" : dict with "x", "y", "width", "height"
        - "area_cust" : dict with "x", "y", "width", "height"

        The `area*` rectangles come back populated; Window Calls used to report them as empty
        objects because it handed the boxed struct straight to `JSON.stringify`.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> gnome_win._ensure_gi()
        >>> focused = gnome_win.get_focused()
        >>> variant = gnome_win._glib.Variant("(u)", (int(focused["id"]),))
        >>> raw = gnome_win._windows_call("Details", variant)
        >>> assert isinstance(raw, tuple)

        >>> coerced_maps = gnome_win._coerce_details_mapping(raw)
        >>> assert isinstance(coerced_maps, dict)
        >>> assert "title" in coerced_maps
        >>> print(coerced_maps["focus"])
        True

        """
        # Fully unpack Variants, then parse JSON-looking strings.
        val = self._maybe_parse_json(self._deep_unpack(obj))

        # dict → done
        if isinstance(val, dict):
            return {k: self._deep_unpack(v) for k, v in val.items()}

        # list / tuple forms
        if isinstance(val, (list, tuple)):
            # Single wrapper → recurse into the only element
            if len(val) == 1:
                return self._coerce_details_mapping(val[0], expected_id=expected_id)

            # List of dicts → choose by id if available, else first
            if val and all(isinstance(x, dict) for x in val):
                if expected_id is not None:
                    for x in val:
                        if _as_int(x.get("id")) == int(expected_id):
                            return {k: self._deep_unpack(v) for k, v in x.items()}
                return {k: self._deep_unpack(v) for k, v in val[0].items()}

            # Pair-list form: [["id", 123], ["monitor", 1], ...]
            if val and all(isinstance(p, (list, tuple)) and len(p) == 2 for p in val):
                return dict(val)

        return None

    def details(self, win_id: int, *, force_refresh: bool = False) -> dict[str, Any]:
        """
        Fetch and cache detailed information for a window id via the extension's 'Details'.

        Parameters
        ----------
        win_id : int
            The target window id.
        force_refresh : bool, optional
            If True, bypass the cache and fetch fresh data. Default is False.

        Returns
        -------
        dict
            A normalized dictionary with details (e.g., 'monitor', 'x', 'y', 'width', 'height').

        Raises
        ------
        TypeError
            If the payload cannot be coerced into a mapping.

        Notes
        -----
        given obtained `details` dict above follows the same described format from
        `_coerce_details_mapping` notes.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> focused = gnome_win.get_focused()
        >>> details = gnome_win.details(win_id=focused["id"])
        >>> assert details["focus"] == True

        """
        win_id = int(win_id)
        if not force_refresh and win_id in self._details_cache:
            return dict(self._details_cache[win_id])

        self._ensure_gi()
        raw = self._windows_call("Details", self._glib.Variant("(u)", (win_id,)))

        mapping = self._coerce_details_mapping(raw, expected_id=win_id)
        if mapping is None:
            raise TypeError(
                f"Unexpected Details() payload for {win_id}: {type(raw)!r} → {raw!r}",
            )

        # Normalize a few common fields to expected Python types.
        for key in _INT_FIELDS:
            if key in mapping:
                coerced = _as_int(mapping[key])
                if coerced is not None:
                    mapping[key] = coerced

        self._details_cache[win_id] = dict(mapping)
        return dict(mapping)

    def relabel_monitors_via_details(
        self,
        windows: list[dict[str, Any]],
        *,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Recompute each window's 'monitor' using the extension's Details(id) method.

        Kept for callers that want the monitor recomputed from scratch, and for older extension
        builds whose `List` omits it: it refetches each window's `Details` and writes the
        `monitor: int` element back into the listing.

        Parameters
        ----------
        windows : list of dict
            Window entries as returned by `list_windows()`.
        force_refresh : bool, optional
            If True, force a fresh Details(id) fetch for each window. Default is False.

        Returns
        -------
        list of dict
            The same list with an integer 'monitor' injected from Details(id) when available.

        Notes
        -----
        This is Wayland-safe and avoids local/global coordinate mismatches by trusting Mutter's
        monitor assignment exposed through the extension.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> wins = gnome_win.list_windows()
        >>> winsn = gnome_win.relabel_monitors_via_details(wins)
        >>> print(type(winsn))
        <class 'list'>

        >>> print(type(winsn[0]))
        <class 'dict'>
        >>> assert isinstance(winsn[0]["focus"], bool)

        """
        for win in windows:
            wid = _as_int(win.get("id"))
            if wid is None:
                continue
            try:
                det = self.details(wid, force_refresh=force_refresh)
            except (DBusError, TypeError):
                # Leave as-is if Details() fails for this entry.
                continue
            monitor = _as_int(det.get("monitor"))
            if monitor is not None:
                win["monitor"] = monitor
        return windows

    def _display_state(self) -> tuple[list[Any], list[Any]] | None:
        """
        Fetch Mutter's current display configuration.

        Returns
        -------
        tuple or None
            ``(physical_monitors, logical_monitors)`` straight out of
            ``org.gnome.Mutter.DisplayConfig.GetCurrentState``, or None when it cannot be read.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> state = gnome_win._display_state()
        >>> assert state is None or len(state) == 2

        """
        try:
            raw = self._call(
                path=DISPLAY_CONFIG_PATH,
                iface=DISPLAY_CONFIG_BUS,
                method="GetCurrentState",
                dest=DISPLAY_CONFIG_BUS,
            )
            state = self._deep_unpack(raw)
            return list(state[1]), list(state[2])
        except (DBusError, IndexError, TypeError, ValueError):
            return None

    @staticmethod
    def _mode_sizes(physical: Sequence[Any]) -> dict[str, tuple[int, int]]:
        """
        Map each connector to the pixel size of the mode currently driving it.

        ``GetCurrentState`` reports position and scale per *logical* monitor but size only per
        *physical* mode, so the two halves of its reply have to be joined to get a rectangle.

        Parameters
        ----------
        physical : sequence
            The physical-monitor array from ``GetCurrentState``.

        Returns
        -------
        dict
            Connector name to ``(width, height)`` in physical pixels.

        Examples
        --------
        >>> mode = ("1920x1080@60", 1920, 1080, 60.0, 1.0, [1.0], {"is-current": True})
        >>> GnomeWindowController._mode_sizes([(("HDMI-1", "v", "p", "s"), [mode], {})])
        {'HDMI-1': (1920, 1080)}

        """
        sizes: dict[str, tuple[int, int]] = {}
        for entry in physical:
            try:
                connector = str(entry[0][0])
                for mode in entry[1]:
                    properties = mode[6] if len(mode) > 6 else {}
                    if isinstance(properties, dict) and properties.get("is-current"):
                        sizes[connector] = (_as_int(mode[1]) or 0, _as_int(mode[2]) or 0)
                        break
            except (IndexError, TypeError, ValueError):
                continue
        return sizes

    def monitor_layout(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        """
        Describe every logical monitor as a rectangle, in physical left-to-right order.

        The rectangle is in logical (scaled) pixels, the same space window positions live in, so
        it can be compared against a window's ``x``/``y`` directly. A monitor rotated onto its
        side reports its mode's width and height swapped, as the desktop sees them.

        Parameters
        ----------
        force_refresh : bool, optional
            Re-query the display configuration instead of reusing the per-instance cache.

        Returns
        -------
        list of dict
            Entries with ``index``, ``x``, ``y``, ``width``, ``height``, ``scale``,
            ``transform``, ``primary`` and ``connectors``. Empty when the layout cannot be read.

        Notes
        -----
        ``index`` is Mutter's own monitor number, which is what a window's ``monitor`` field
        holds -- so an entry can be matched against `list_windows()` output without translation.
        It follows no geometric order of its own; that is what the sorting here is for.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> layout = gnome_win.monitor_layout()
        >>> assert all("index" in m for m in layout)
        >>> assert all(m["width"] >= 0 and m["height"] >= 0 for m in layout)

        """
        if self._monitor_layout is not None and not force_refresh:
            return [dict(m) for m in self._monitor_layout]

        state = self._display_state()
        if state is None:
            return []
        physical, logical = state
        sizes = self._mode_sizes(physical)

        monitors: list[dict[str, Any]] = []
        for index, entry in enumerate(logical):
            try:
                connectors = [str(m[0]) for m in entry[5] if m] if len(entry) > 5 else []
            except (IndexError, TypeError):
                connectors = []

            scale = (
                float(entry[2]) if len(entry) > 2 and isinstance(entry[2], (int, float)) else 1.0
            )
            transform = _as_int(entry[3]) if len(entry) > 3 else 0
            width, height = sizes.get(connectors[0], (0, 0)) if connectors else (0, 0)
            if (transform or 0) in _ROTATED_TRANSFORMS:
                width, height = height, width
            if scale > 0:
                width, height = round(width / scale), round(height / scale)

            monitors.append({
                "index": index,
                "x": _as_int(entry[0]) or 0,
                "y": _as_int(entry[1]) or 0,
                "width": width,
                "height": height,
                "scale": scale,
                "transform": transform or 0,
                "primary": bool(entry[4]) if len(entry) > 4 else False,
                "connectors": connectors,
            })

        monitors.sort(key=lambda m: (m["x"], m["y"]))
        self._monitor_layout = [dict(m) for m in monitors]
        return monitors

    def monitor_order(self, *, force_refresh: bool = False) -> tuple[int, ...]:
        """
        Return Mutter monitor indices sorted by their physical left-to-right position.

        A flat reading of the layout, useful for listing and for :func:`cycle_monitor`. It says
        nothing about rows, so anything acting on a stacked or grid arrangement wants
        :meth:`monitor_neighbor` instead.

        Parameters
        ----------
        force_refresh : bool, optional
            Re-query the display layout instead of reusing the per-instance cache.

        Returns
        -------
        tuple of int
            Monitor indices, leftmost first. Falls back to :data:`MONITOR_ORDER` -- a single
            monitor -- when the layout cannot be read.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> order = gnome_win.monitor_order()
        >>> assert len(order) == len(set(order))

        """
        if self._monitor_order is not None and not force_refresh:
            return self._monitor_order

        layout = self.monitor_layout(force_refresh=force_refresh)
        order = tuple(int(m["index"]) for m in layout)

        self._monitor_order = order or MONITOR_ORDER
        return self._monitor_order

    def monitor_neighbor(
        self,
        direction: int | str,
        *,
        current_monitor: int | None = None,
        wrap: bool = True,
    ) -> int | None:
        """
        Return the monitor lying `direction` of the focused one.

        Parameters
        ----------
        direction : int or str
            Anything :func:`normalize_direction` accepts.
        current_monitor : int, optional
            Start from this monitor instead of the focused window's.
        wrap : bool, optional
            Come back around from the opposite edge at the end of a row or column. Default True.

        Returns
        -------
        int or None
            The neighbouring monitor's index, or None when the current monitor is unknown, the
            layout is unreadable, or there is nowhere to go that way.

        Raises
        ------
        ValueError
            If `direction` names no known direction.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> nxt = gnome_win.monitor_neighbor("right")
        >>> assert nxt is None or isinstance(nxt, int)

        """
        heading = normalize_direction(direction)
        if current_monitor is None:
            current_monitor = self.current_monitor_from_details()
        if current_monitor is None:
            return None
        return neighbor_monitor(self.monitor_layout(), current_monitor, heading, wrap=wrap)

    def current_monitor_from_details(self) -> int | None:
        """
        Determine current monitor using the focused window's Details(id).

        Returns
        -------
        int or None
            The 0-based monitor index if it can be determined; otherwise None.

        Notes
        -----
        We first try the focused-window D-Bus interface to get an id, then fall back to scanning
        `list_windows()` for a window flagged as focused. Finally, we return that window's monitor
        from Details(id).

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> mon = gnome_win.current_monitor_from_details()
        >>> assert mon is None or isinstance(mon, int)
        """
        # 1) Try focused extension for an id.
        foc = self.get_focused()
        wid = _as_int(foc.get("id")) if isinstance(foc, dict) else None

        # 2) Fall back to scanning list_windows() for a flagged focus.
        if wid is None:
            flagged = self._focused_from_list()
            wid = _as_int(flagged.get("id")) if flagged else None

        if wid is None:
            return None

        try:
            det = self.details(wid)
        except (DBusError, TypeError):
            return None
        return _as_int(det.get("monitor"))

    def focus_named_window(
        self,
        needle: str,
        *,
        only_current_monitor: bool = False,
        only_other_monitor: bool = False,
    ) -> int | None:
        """
        Focus a window matching `needle`; optionally restrict to current monitor via Details(id).

        If the focused window already matches, switch to the next matching window (wrap-around). If
        there is only one match overall, switch to the next window in global list. If there are no
        matches, switch to next window in the list. When `only_current_monitor` is True, candidates
        are filtered to the monitor obtained from `current_monitor_from_details()`.

        Parameters
        ----------
        needle : str
            Case-insensitive substring to match against wm_class, wm_class_instance, or title.
        only_current_monitor : bool, optional
            If True, restrict candidates to `current_monitor_from_details()`. Default is False.
        only_other_monitor : bool, optional
            If True, restrict candidates to windows not on current monitor

        Returns
        -------
        int or None
            Focused window id, or None if none could be focused.
        """
        windows = self.focusable(self.list_windows())
        if not windows:
            return None

        # Ensure accurate monitor labels via Details(id).
        windows = self.relabel_monitors_via_details(windows)

        cur_mon = (
            self.current_monitor_from_details()
            if only_current_monitor or only_other_monitor
            else None
        )
        if cur_mon is not None:
            filtered = (
                [w for w in windows if _as_int(w.get("monitor")) == cur_mon]
                if only_current_monitor
                else [w for w in windows if _as_int(w.get("monitor")) != cur_mon]
            )
            # Never strand the caller with an empty candidate pool.
            windows = filtered or windows

        focused = self.get_focused()
        focused_id = _as_int(focused.get("id")) if isinstance(focused, dict) else None

        candidates = [w for w in windows if self._matches(needle, w)]

        target_id: int | None = None

        if candidates:
            cidx = self._idx_of(candidates, focused_id)
            if cidx is not None and len(candidates) > 1:
                target_id = _as_int(candidates[(cidx + 1) % len(candidates)]["id"])
            elif cidx is not None:
                gidx = self._idx_of(windows, focused_id) or 0
                target_id = _as_int(windows[(gidx + 1) % len(windows)]["id"])
            else:
                target_id = _as_int(candidates[0]["id"])
        else:
            gidx = self._idx_of(windows, focused_id) or 0
            target_id = _as_int(windows[(gidx + 1) % len(windows)]["id"])

        return self._activate_safe(target_id)

    def cycle_monitors(
        self,
        direction: int | str = "right",
        exclude_apps: str | list[str] | None = None,
    ) -> int | None:
        """
        Focus a window on the monitor lying `direction` of the current one.

        The step follows the real monitor rectangles, so it works on a row, a stack, or a grid.
        Monitors that hold no window are skipped and the search carries on in the same direction,
        so a single keypress always lands somewhere.

        Parameters
        ----------
        direction : int or str, optional
            ``"left"``, ``"right"``, ``"up"`` or ``"down"``, or a positive/negative integer for
            right/left. Default is ``"right"``.
        exclude_apps : str or list of str, optional
            Names excluded on top of :attr:`exclude_apps`, for this call only.

        Returns
        -------
        int or None
            Focused window id, or None if none could be focused.

        Raises
        ------
        ValueError
            If `direction` names no known direction.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> focused = gnome_win.cycle_monitors("right")
        >>> assert focused is None or isinstance(focused, int)

        """
        heading = normalize_direction(direction)
        extra = [exclude_apps] if isinstance(exclude_apps, str) else list(exclude_apps or ())

        windows = self.list_windows()
        if not windows:
            return None
        windows = self.focusable(self.relabel_monitors_via_details(windows), extra)
        if not windows:
            return None

        cur_mon = self.current_monitor_from_details()
        if cur_mon is None:
            # Nothing to move away from; fall back to focusing the topmost window anywhere.
            return self._activate_safe(_as_int(windows[-1]["id"]))

        layout = self.monitor_layout()
        monitor = cur_mon
        # Keep stepping the same way over monitors that hold no eligible window, stopping once
        # the wrap-around brings us back to where we started.
        for _ in range(len(layout) or 1):
            nxt = neighbor_monitor(layout, monitor, heading)
            if nxt is None or nxt == cur_mon:
                break
            monitor = nxt
            on_monitor = [w for w in windows if _as_int(w.get("monitor")) == monitor]
            if on_monitor:
                return self._activate_safe(_as_int(on_monitor[-1]["id"]))

        # There is no monitor that way, or none of the ones there hold a window. Leaving focus
        # alone beats yanking it to an arbitrary window: on a single row, `up` means nothing.
        return None

    def focus_other_monitor_window(
        self,
        focus_top_window: bool = True,
        exclude_titles: list[str] | None = None,
    ) -> int | None:
        """
        Focus a window from not the current monitor.

        Parameters
        ----------
        focus_top_window : bool or str, Default True
            If focused window will be the current top window (visible) or hidden bottom window
            (hidden). In case of only one window in the other monitor, this parameter don't have
            any effect. The strings ``"top"`` and ``"bottom"`` are also accepted.
        exclude_titles  : list, Default None
            Names excluded on top of :attr:`exclude_apps`, for this call only.

        Returns
        -------
        int or None
            Focused window id, or None if none could be focused.
        """
        if isinstance(focus_top_window, str):
            focus_top_window = focus_top_window != "bottom"
        change_idx = -1 if focus_top_window else 0

        windows = self.list_windows()
        if not windows:
            return None

        # Ensure accurate monitor labels via Details(id).
        windows = self.focusable(self.relabel_monitors_via_details(windows), exclude_titles or ())
        if not windows:
            return None

        cur_mon = self.current_monitor_from_details()
        if cur_mon is not None:
            windows = [w for w in windows if _as_int(w.get("monitor")) != cur_mon] or windows

        if not windows:
            return None

        return self._activate_safe(_as_int(windows[change_idx]["id"]))

    def focus_last_window(self) -> int | None:
        """
        Focus last focused window.

        Returns
        -------
        int or None
            Focused window id, or None if none could be focused.
        """
        windows = self.focusable([
            w for w in self.list_windows() if not any(bool(w.get(k)) for k in FOCUS_KEYS)
        ])
        if not windows:
            return None
        return self._activate_safe(_as_int(windows[-1]["id"]))

    def get_current_workspace_index(self) -> int | None:
        """
        Return current workspace index.

        Reads the focused window's ``workspace`` field first, which works natively on Wayland, and
        only falls back to ``xprop`` when the extension does not report it.

        Returns
        -------
        int or None
            0-based index of the active workspace, or None if unavailable.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> idx = gnome_win.get_current_workspace_index()
        >>> assert idx is None or isinstance(idx, int)
        """
        focused = self.get_focused()
        if isinstance(focused, dict):
            workspace = _as_int(focused.get("workspace"))
            if workspace is not None and focused.get("in_current_workspace", True):
                return workspace

        try:
            cmd = subprocess.run(
                ["xprop", "-root", "-notype", "_NET_CURRENT_DESKTOP"],
                check=False,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, OSError):
            return None

        if cmd.returncode != 0:
            return None
        return _as_int(cmd.stdout.split("=")[-1].strip())

    def focus_same_monitor_window(
        self,
        change_idx: int = 1,
        other_workspace: bool = False,
    ) -> int | None:
        """
        Focus a window from the current monitor.

        Parameters
        ----------
        change_idx : int, optional
            Offset applied to the focused window's position in the monitor's window ring.
            Positive walks forward, negative walks backward.
        other_workspace : bool, optional
            Target windows that live on a workspace other than the current one.

        Returns
        -------
        int or None
            Focused window id, or None if none could be focused.
        """
        windows = self.focusable(self.list_windows())
        if not windows:
            return None

        windows = self.relabel_monitors_via_details(windows)

        cur_mon = self.current_monitor_from_details()
        focused = self.get_focused()
        focused_id = _as_int(focused.get("id")) if isinstance(focused, dict) else None

        if cur_mon is not None:
            candidates = [w for w in windows if _as_int(w.get("monitor")) == cur_mon] or windows
            if other_workspace:
                elsewhere = [w for w in candidates if not w.get("in_current_workspace", True)]
                if elsewhere:
                    return self._activate_safe(_as_int(elsewhere[0].get("id")))
            else:
                here = [w for w in candidates if w.get("in_current_workspace", False)]
                candidates = here or candidates
        else:
            candidates = windows

        sorted_data = sorted(candidates, key=lambda d: _as_int(d.get("id")) or 0, reverse=True)
        if not sorted_data:
            return None

        cur_idx = next(
            (i for i, d in enumerate(sorted_data) if _as_int(d.get("id")) == focused_id),
            None,
        )
        # Focused window is not in this pool: start the walk from its head.
        base = 0 if cur_idx is None else cur_idx + change_idx

        return self._activate_safe(_as_int(sorted_data[base % len(sorted_data)]["id"]))

    def focusable(
        self,
        windows: Sequence[dict[str, Any]],
        extra: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """
        Drop the windows no focus command may land on.

        Every focus path runs its candidates through this, so an excluded app is stepped over and
        the next one is taken instead. Nothing else is filtered: `list_windows`, `details` and the
        monitor lookups still see excluded windows, which is what keeps "where am I" correct even
        while an excluded app holds focus.

        Parameters
        ----------
        windows : sequence of dict
            Entries as returned by `list_windows()`.
        extra : sequence of str, optional
            Names to exclude on top of :attr:`exclude_apps`, for this call only.

        Returns
        -------
        list of dict
            The entries that may be focused, in their original order. Possibly empty, which is
            the honest answer when every window is excluded.

        Examples
        --------
        >>> gnome_win = GnomeWindowController(exclude_apps=("floorp",))
        >>> pool = [
        ...     {"id": 1, "wm_class": "kitty", "title": "shell"},
        ...     {"id": 2, "wm_class": "Floorp", "title": "a tab"},
        ... ]
        >>> [w["id"] for w in gnome_win.focusable(pool)]
        [1]
        >>> [w["id"] for w in gnome_win.focusable(pool, extra=["kitty"])]
        []
        >>> [w["id"] for w in GnomeWindowController(exclude_apps=()).focusable(pool)]
        [1, 2]

        """
        names = [str(n) for n in (*self.exclude_apps, *extra) if str(n).strip()]
        if not names:
            return list(windows)
        return [w for w in windows if not any(self._matches(name, w) for name in names)]

    def _idx_of(self, seq: list[dict[str, Any]], wid: int | None) -> int | None:
        """
        Return the position of the window with id `wid` inside `seq`.

        Parameters
        ----------
        seq : list of dict
            Window entries to scan.
        wid : int or None
            Window id to look for.

        Returns
        -------
        int or None
            Index of the match, or None when `wid` is None or absent.
        """
        if wid is None:
            return None
        return next((i for i, w in enumerate(seq) if _as_int(w.get("id")) == wid), None)

    def focus_same_name_window(self) -> int | None:
        """
        Focus a window with same wm_class/title/instance from curr focused window.

        Returns
        -------
        int or None
            Focused window id, or None when the focused app owns a single window.
        """
        windows = self.focusable(self.list_windows())
        if not windows:
            return None

        focused = self.get_focused()
        if not isinstance(focused, dict):
            return None

        needle = focused.get("wm_class") or focused.get("wm_class_instance")
        if not needle:
            return None

        candidates = [w for w in windows if self._matches(str(needle), w)]
        if len(candidates) <= 1:
            return None

        # Walk the ring from the currently focused window instead of always jumping to the first.
        focused_id = _as_int(focused.get("id"))
        cidx = self._idx_of(candidates, focused_id)
        nxt = 0 if cidx is None else (cidx + 1) % len(candidates)

        return self._activate_safe(_as_int(candidates[nxt]["id"]))

    # ------------------------------ Internals ------------------------------

    def _activate_safe(self, win_id: int | None) -> int | None:
        """
        Activate a window id through the shell extension, returning the id on success.

        When :attr:`highlight_on_focus` is set, the bundled shell extension is asked to draw its
        border around the freshly focused window. A missing or disabled extension is ignored.

        Parameters
        ----------
        win_id : int or None
            The target window id.

        Returns
        -------
        int or None
            The same id on success, or `None` if activation failed or id was None.
        """
        if win_id is None:
            return None
        self._ensure_gi()
        try:
            self._windows_call("Activate", self._glib.Variant("(u)", (int(win_id),)))
        except DBusError:
            return None

        if self.highlight_on_focus:
            self.highlight.try_flash(int(win_id))

        return int(win_id)

    def _get_title(self, win_id: int) -> str:
        """
        Fetch and cache a window's title via the shell extension.

        ``list_windows()`` seeds this cache, so the D-Bus round trip only happens for ids that were
        never listed.

        Parameters
        ----------
        win_id : int
            The target window id.

        Returns
        -------
        str
            The window title string (may be empty if unavailable).
        """
        win_id = int(win_id)
        if win_id in self._title_cache:
            return self._title_cache[win_id]
        self._ensure_gi()
        try:
            title = self._windows_call("GetTitle", self._glib.Variant("(u)", (win_id,)))
            title_s = str(title[0]) if isinstance(title, tuple) and title else str(title)
        except DBusError:
            title_s = ""
        self._title_cache[win_id] = title_s
        return title_s

    def _matches(self, needle: str, w: dict[str, Any]) -> bool:
        """
        Check if a window matches `needle` against wm_class / instance / title (case-insensitive).

        Parameters
        ----------
        needle : str
            Case-insensitive substring to match.
        w : dict
            Window information dictionary from `list_windows()`.

        Returns
        -------
        bool
            True if the window matches; otherwise False.

        Examples
        --------
        >>> gnome_win = GnomeWindowController()
        >>> gnome_win._matches("kitty", {"id": 1, "wm_class": "kitty", "title": "shell"})
        True
        >>> gnome_win._matches("firefox", {"id": 1, "wm_class": "kitty", "title": "shell"})
        False
        """
        n = needle.lower()
        for key in ("wm_class", "wm_class_instance"):
            if n in str(w.get(key, "")).lower():
                return True

        # Prefer the title carried by the listing; only hit D-Bus when it is absent.
        title = w.get("title")
        if not isinstance(title, str):
            wid = _as_int(w.get("id"))
            title = self._get_title(wid) if wid is not None else ""
        return n in title.lower()

    def wayland_selection(self, *, use_primary: bool = True, timeout: float = 1.0) -> str | None:
        """
        Return current selection text on Wayland (primary by default).

        Falls back to normal clipboard if primary is empty/unsupported.

        Parameters
        ----------
        use_primary : bool, default True
            Read the PRIMARY selection first.
        timeout : float, default 1.0
            Seconds allowed for each ``wl-paste`` invocation.

        Returns
        -------
        str or None
            Selection text, or None when nothing could be read.
        """
        attempts = [["--primary"], []] if use_primary else [[]]
        for sel_args in attempts:
            try:
                out = subprocess.check_output(
                    ["wl-paste", *sel_args, "--no-newline"],
                    timeout=timeout,
                    stderr=subprocess.DEVNULL,
                )
            except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                continue
            text = out.decode("utf-8", errors="replace")
            if text:
                return text
        return None

    def copy_wl_sel(self) -> str | None:
        """
        Copy current wayland selection into the clipboard.

        Returns
        -------
        str or None
            ``wl-copy`` stdout, or None when there was no selection to copy.
        """
        sel = self.wayland_selection()
        if not sel:
            return None
        try:
            cmd = subprocess.run(
                ["wl-copy", sel],
                check=True,
                start_new_session=False,
                capture_output=True,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

        return cmd.stdout


def _as_int(value: object) -> int | None:
    """
    Coerce a value to ``int``, returning None instead of raising.

    Parameters
    ----------
    value : object
        Value to coerce.

    Returns
    -------
    int or None
        The integer value, or None when `value` is missing or not numeric.

    Examples
    --------
    >>> _as_int("12")
    12
    >>> _as_int(3.0)
    3
    >>> _as_int(None) is None
    True
    >>> _as_int("kitty") is None
    True
    """
    if value is None or isinstance(value, bool):
        return None
    with contextlib.suppress(TypeError, ValueError):
        return int(value)
    return None
