"""
Created on 2026-08-26 23:55:00.

@author: eduardotc
@email: eduardotcampos@hotmail.com

ANSI color handling for the terminal client.

Colors are resolved at *run time*, never at import time, so a test or a caller can turn them off
after the module is already imported. Every name is served by one lookup table, which makes it
impossible for a color to exist while colored output is on but be missing while it is off.
"""

from __future__ import annotations

import os
import sys
from typing import IO, Literal

__all__ = ["ANSI", "COLORS", "ColorMode", "Palette", "resolve_colors", "supports_ansi_colors"]

type ColorMode = Literal["auto", "always", "never"]

#: Every color this client can emit, by name.
ANSI: dict[str, str] = {
    "RST": "\033[0m",
    "RED": "\033[38;5;001m",
    "GRN": "\033[38;5;002m",
    "YLW": "\033[38;5;003m",
    "BLUE": "\033[38;5;004m",
    "TXT": "\033[38;5;007m",
    "BLD": "\033[38;5;015m",
    "VLT": "\033[38;5;063m",
    "LRED": "\033[38;5;203m",
    "ORANGE": "\033[38;5;208m",
}

#: Values of ``GWC_COLORS`` (and friends) understood as "off".
_FALSEY = frozenset({"0", "false", "no", "off", "none", "never", ""})

#: Values understood as "on".
_TRUTHY = frozenset({"1", "true", "yes", "on", "always", "force"})


def _env_flag(name: str) -> bool | None:
    """
    Read an environment variable as a tri-state boolean.

    Parameters
    ----------
    name : str
        Environment variable to read.

    Returns
    -------
    bool or None
        True/False when the variable is set to a recognized value, otherwise None (unset or
        unrecognized), meaning "no opinion".

    Examples
    --------
    >>> import os
    >>> os.environ["GWC_DOCTEST_FLAG"] = "False"
    >>> _env_flag("GWC_DOCTEST_FLAG")
    False
    >>> os.environ["GWC_DOCTEST_FLAG"] = "1"
    >>> _env_flag("GWC_DOCTEST_FLAG")
    True
    >>> del os.environ["GWC_DOCTEST_FLAG"]
    >>> _env_flag("GWC_DOCTEST_FLAG") is None
    True

    """
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in _FALSEY:
        return False
    if value in _TRUTHY:
        return True
    return None


def supports_ansi_colors(stream: IO[str] | None = None) -> bool:
    """
    Check whether a stream is an interactive terminal that understands ANSI colors.

    Parameters
    ----------
    stream : file-like, optional
        Stream to test. Defaults to ``sys.stdout``.

    Returns
    -------
    bool
        True when the stream is a tty and ``TERM`` names a color-capable terminal.

    Examples
    --------
    >>> import io
    >>> supports_ansi_colors(io.StringIO())
    False

    """
    stream = sys.stdout if stream is None else stream
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    term = os.environ.get("TERM", "").lower()
    if term in {"", "dumb"}:
        return False
    return any(token in term for token in ("xterm", "color", "kitty", "screen", "tmux", "alacritty"))


def resolve_colors(mode: ColorMode | None = None, stream: IO[str] | None = None) -> bool:
    """
    Decide whether colored output should be emitted.

    Precedence, highest first:

    1. `mode`, when it is ``"always"`` or ``"never"`` (the ``--color`` flag).
    2. ``GWC_COLORS`` -- this project's own switch.
    3. ``NO_COLOR`` set to anything non-empty (the https://no-color.org convention).
    4. ``FORCE_COLOR`` set to anything non-empty.
    5. Auto-detection through :func:`supports_ansi_colors`.

    Parameters
    ----------
    mode : {"auto", "always", "never"}, optional
        Explicit request, usually straight from the command line. ``None`` and ``"auto"`` both
        mean "fall through to the environment".
    stream : file-like, optional
        Stream the output is headed for. Defaults to ``sys.stdout``.

    Returns
    -------
    bool
        True if ANSI codes should be written.

    Examples
    --------
    >>> import io, os
    >>> resolve_colors("never")
    False
    >>> resolve_colors("always")
    True

    ``GWC_COLORS`` outranks auto-detection, and understands the obvious spellings:

    >>> os.environ["GWC_COLORS"] = "False"
    >>> resolve_colors()
    False
    >>> os.environ["GWC_COLORS"] = "1"
    >>> resolve_colors(stream=io.StringIO())
    True
    >>> del os.environ["GWC_COLORS"]

    An explicit ``--color`` still wins over the environment:

    >>> os.environ["GWC_COLORS"] = "1"
    >>> resolve_colors("never")
    False
    >>> del os.environ["GWC_COLORS"]

    >>> os.environ["NO_COLOR"] = "1"
    >>> resolve_colors(stream=io.StringIO())
    False
    >>> del os.environ["NO_COLOR"]

    """
    if mode == "always":
        return True
    if mode == "never":
        return False

    explicit = _env_flag("GWC_COLORS")
    if explicit is not None:
        return explicit

    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True

    return supports_ansi_colors(stream)


class Palette:
    """
    Serve ANSI codes by name, or empty strings when colors are off.

    Attribute lookup goes through :data:`ANSI`, so a color can never be defined in one state and
    missing in the other -- the failure mode of hand-written if/else blocks.

    Parameters
    ----------
    enabled : bool, optional
        Whether attribute access yields real escape codes. Default is True.

    Attributes
    ----------
    enabled : bool
        Live switch; assign to it or call :meth:`configure`.

    Examples
    --------
    >>> palette = Palette(enabled=True)
    >>> palette.YLW == ANSI["YLW"]
    True

    Turning it off yields empty strings for *every* name, so format strings stay valid:

    >>> palette.enabled = False
    >>> palette.YLW, palette.RST, palette.ORANGE
    ('', '', '')
    >>> f"{palette.YLW}title{palette.RST}"
    'title'

    Unknown names still raise, rather than silently formatting as empty:

    >>> palette.NOT_A_COLOR
    Traceback (most recent call last):
        ...
    AttributeError: 'Palette' object has no color 'NOT_A_COLOR'

    """

    __slots__ = ("enabled",)

    def __init__(self, enabled: bool = True) -> None:
        """
        Palette handling class initing.

        Parameters
        ----------
        enabled : bool, Default True

        Returns
        -------
        None

        """
        self.enabled = enabled

    def __getattr__(self, name: str) -> str:
        """
        Return the escape code for `name`, or ``""`` when colors are disabled.

        Parameters
        ----------
        name : str
            Color name from :data:`ANSI`.

        Returns
        -------
        str
            The escape code, or an empty string.

        Raises
        ------
        AttributeError
            If `name` is not a known color.

        """
        try:
            code = ANSI[name]
        except KeyError:
            raise AttributeError(f"{type(self).__name__!r} object has no color {name!r}") from None
        return code if self.enabled else ""

    def configure(self, mode: ColorMode | None = None, stream: IO[str] | None = None) -> bool:
        """
        Resolve `mode` against the environment and apply the result.

        Parameters
        ----------
        mode : {"auto", "always", "never"}, optional
            Explicit request; ``None``/``"auto"`` defer to the environment.
        stream : file-like, optional
            Stream the output is headed for. Defaults to ``sys.stdout``.

        Returns
        -------
        bool
            The value :attr:`enabled` was set to.

        Examples
        --------
        >>> palette = Palette()
        >>> palette.configure("never")
        False
        >>> palette.YLW
        ''

        """
        self.enabled = resolve_colors(mode, stream)
        return self.enabled

    def strip(self, text: str) -> str:
        """
        Remove every escape code this palette can emit from `text`.

        Useful in tests that capture output regardless of the active mode.

        Parameters
        ----------
        text : str
            Text possibly containing ANSI codes.

        Returns
        -------
        str
            `text` without any known escape code.

        Examples
        --------
        >>> Palette().strip(f"{ANSI['YLW']}focus{ANSI['RST']}")
        'focus'

        """
        for code in ANSI.values():
            text = text.replace(code, "")
        return text


#: Shared palette used by the terminal client; configured once in ``cli.main()``.
COLORS = Palette()
