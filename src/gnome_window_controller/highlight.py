"""
Created on 2026-08-13 07:45:00.

@author: eduardotc
@email: eduardotcampos@hotmail.com

Focused-window border highlight: installer and D-Bus client for the bundled shell extension.

Mutter implements neither ``wlr-layer-shell`` nor unrestricted ``org.gnome.Shell.Eval``, so an
overlay drawn from a plain GTK process cannot be positioned, kept above other windows or made
click-through under GNOME/Wayland. The border is therefore drawn by a tiny GNOME Shell extension
shipped inside this package (``shell_extension/``), which this module installs, enables and drives
over D-Bus.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import DBusError, HighlightError

if TYPE_CHECKING:
    from .gnome_window_controller import GnomeWindowController

__all__ = ["HighlightError", "HighlightOptions", "WindowHighlighter"]

#: Failures that mean "the shell side is simply not reachable", never a bug on our side.
_UNREACHABLE = (DBusError, ImportError, ValueError)

HIGHLIGHT_UUID = "gnome-window-controller@eduardotcampos.hotmail.com"

HIGHLIGHT_PATH = "/org/gnome/Shell/Extensions/GnomeWindowController"

HIGHLIGHT_IFACE = "org.gnome.Shell.Extensions.GnomeWindowController"

SOURCE_DIR = Path(__file__).parent / "shell_extension" / HIGHLIGHT_UUID

INSTALL_DIR = Path.home() / ".local" / "share" / "gnome-shell" / "extensions" / HIGHLIGHT_UUID

#: What a highlight *mode* means in terms of the two underlying extension flags.
#:
#: ``always``    draw for every focus change, whoever caused it (mouse click included).
#: ``commands``  draw only for focus changes this module performs; ignore everything else.
#: ``off``       never draw. ``follow_focus`` is deliberately left alone so switching back
#:               restores the previous preference.
HIGHLIGHT_MODES: dict[str, dict[str, bool]] = {
    "always": {"enabled": True, "follow_focus": True},
    "commands": {"enabled": True, "follow_focus": False},
    "off": {"enabled": False},
}


@dataclass(slots=True)
class HighlightOptions:
    """
    Tunables for the focused-window border.

    Every field defaults to ``None``, meaning "leave whatever the extension currently uses".

    Parameters
    ----------
    enabled : bool or None
        Master switch. When False the extension stays loaded but draws nothing.
    color : str or None
        Any CSS color accepted by St, e.g. ``"#993c5a"``, ``"red"`` or ``"rgba(0,0,0,0.5)"``.
    width : int or None
        Border thickness in pixels.
    radius : int or None
        Border corner radius in pixels.
    inset : int or None
        Pixels the border is grown outwards past the window frame.
    duration_ms : int or None
        Milliseconds to keep the border visible. ``0`` keeps it until focus moves.
    fade_ms : int or None
        Fade-out duration used when ``duration_ms`` expires.
    follow_focus : bool or None
        Redraw automatically whenever the focused window changes.
    skip_maximized : bool or None
        Do not highlight fully maximized windows.
    skip_fullscreen : bool or None
        Do not highlight fullscreen windows.
    only_normal : bool or None
        Restrict highlighting to ``Meta.WindowType.NORMAL`` windows.

    Examples
    --------
    >>> HighlightOptions(color="#fb4934", width=4).payload()
    {'color': '#fb4934', 'width': 4}

    """

    enabled: bool | None = None
    color: str | None = None
    width: int | None = None
    radius: int | None = None
    inset: int | None = None
    duration_ms: int | None = None
    fade_ms: int | None = None
    follow_focus: bool | None = None
    skip_maximized: bool | None = None
    skip_fullscreen: bool | None = None
    only_normal: bool | None = None

    @classmethod
    def from_mode(cls, mode: str, **overrides: Any) -> HighlightOptions:
        """
        Build options for a named highlight mode.

        Parameters
        ----------
        mode : str
            One of ``"always"``, ``"commands"`` or ``"off"``; see :data:`HIGHLIGHT_MODES`.
        **overrides
            Extra fields set alongside the mode, e.g. ``color="#fb4934"``.

        Returns
        -------
        HighlightOptions

        Raises
        ------
        ValueError
            If `mode` is not a known mode.

        Examples
        --------
        >>> HighlightOptions.from_mode("commands").payload()
        {'enabled': True, 'follow_focus': False}
        >>> HighlightOptions.from_mode("off").payload()
        {'enabled': False}
        >>> HighlightOptions.from_mode("always", width=4).payload()
        {'enabled': True, 'width': 4, 'follow_focus': True}
        """
        if mode not in HIGHLIGHT_MODES:
            raise ValueError(f"unknown highlight mode {mode!r}; pick one of {sorted(HIGHLIGHT_MODES)}")
        return cls(**(HIGHLIGHT_MODES[mode] | overrides))

    @staticmethod
    def mode_of(options: dict[str, Any]) -> str:
        """
        Name the mode a set of effective extension options corresponds to.

        Parameters
        ----------
        options : dict
            Effective options as reported by the extension.

        Returns
        -------
        str
            ``"off"``, ``"always"`` or ``"commands"``.

        Examples
        --------
        >>> HighlightOptions.mode_of({"enabled": False, "follow_focus": True})
        'off'
        >>> HighlightOptions.mode_of({"enabled": True, "follow_focus": False})
        'commands'
        >>> HighlightOptions.mode_of({"enabled": True, "follow_focus": True})
        'always'
        """
        if not options.get("enabled", True):
            return "off"
        return "always" if options.get("follow_focus", True) else "commands"

    def payload(self) -> dict[str, Any]:
        """
        Return the explicitly-set options, dropping every ``None`` field.

        Returns
        -------
        dict
            Mapping suitable for ``SetOptions``.

        """
        return {k: v for k, v in asdict(self).items() if v is not None}


class WindowHighlighter:
    """
    Install, enable and drive the bundled focused-window highlight extension.

    Parameters
    ----------
    controller : GnomeWindowController
        Controller used for its lazily-created session bus connection.

    Attributes
    ----------
    uuid : str
        Extension UUID.
    source_dir : pathlib.Path
        Bundled extension sources inside this package.
    install_dir : pathlib.Path
        Per-user GNOME Shell extension directory the sources are copied to.

    """

    uuid = HIGHLIGHT_UUID
    source_dir = SOURCE_DIR
    install_dir = INSTALL_DIR

    def __init__(self, controller: GnomeWindowController) -> None:
        """
        Init window highlighter class, adding attribute `_controller`.

        Parameters
        ----------
        controller: GnomeWindowController

        """
        self._controller = controller

    # ------------------------------ Installation ------------------------------

    def is_installed(self) -> bool:
        """
        Report whether the extension is present in the user extension directory.

        Returns
        -------
        bool

        """
        return (self.install_dir / "metadata.json").is_file()

    def is_running(self) -> bool:
        """
        Report whether the extension is loaded and answering on the session bus.

        Returns
        -------
        bool

        """
        try:
            self._call("Ping")
        except _UNREACHABLE:
            return False
        return True

    def install(self, *, force: bool = False) -> Path:
        """
        Copy the bundled extension into the user's GNOME Shell extension directory.

        Parameters
        ----------
        force : bool, optional
            Overwrite an existing installation. Default is False.

        Returns
        -------
        pathlib.Path
            The installation directory.

        Raises
        ------
        HighlightError
            If the bundled sources are missing, or the target exists and ``force`` is False.

        """
        if not (self.source_dir / "metadata.json").is_file():
            raise HighlightError(f"bundled extension sources not found at {self.source_dir}")

        if self.install_dir.exists():
            if not force:
                raise HighlightError(
                    f"{self.install_dir} already exists; pass force=True to overwrite",
                )
            shutil.rmtree(self.install_dir)

        self.install_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.source_dir, self.install_dir)
        self.compile_schemas()
        return self.install_dir

    def compile_schemas(self) -> bool:
        """
        Compile the extension's GSettings schema into ``schemas/gschemas.compiled``.

        The preferences dialog and the shell both read the compiled file; without it
        ``getSettings()`` fails and the extension refuses to load.

        Returns
        -------
        bool
            True if the schema directory was compiled, False if there is nothing to compile or
            ``glib-compile-schemas`` is unavailable.
        """
        schema_dir = self.install_dir / "schemas"
        if not any(schema_dir.glob("*.gschema.xml")):
            return False
        try:
            done = subprocess.run(
                ["glib-compile-schemas", str(schema_dir)],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return False
        return done.returncode == 0

    def uninstall(self) -> bool:
        """
        Disable and remove the installed extension.

        Returns
        -------
        bool
            True if something was removed.

        """
        self.set_extension_enabled(enable=False)
        if not self.install_dir.exists():
            return False
        shutil.rmtree(self.install_dir)
        return True

    def set_extension_enabled(self, *, enable: bool) -> bool:
        """
        Enable or disable the extension.

        Tries the ``gnome-extensions`` CLI first, then falls back to editing the
        ``org.gnome.shell`` GSettings keys directly. The fallback matters right after
        :meth:`install`: the CLI refuses to touch a UUID the running shell has not scanned yet,
        but writing the key still makes the extension come up on the next login.

        Parameters
        ----------
        enable : bool
            Whether to enable (True) or disable (False) the extension.

        Returns
        -------
        bool
            True if the extension is now listed as (un)enabled.

        """
        action = "enable" if enable else "disable"
        try:
            done = subprocess.run(
                ["gnome-extensions", action, self.uuid],
                check=False,
                capture_output=True,
                text=True,
            )
            if done.returncode == 0:
                return True
        except FileNotFoundError:
            pass
        return self._set_enabled_via_gsettings(enable=enable)

    def _set_enabled_via_gsettings(self, *, enable: bool) -> bool:
        """
        Add or remove the UUID from the shell's extension lists via GSettings.

        Parameters
        ----------
        enable : bool
            Whether to enable (True) or disable (False) the extension.

        Returns
        -------
        bool
            True if the settings were written.

        """
        settings = self._shell_settings()
        if settings is None:
            return False

        for key, wanted in (("enabled-extensions", enable), ("disabled-extensions", not enable)):
            current = list(settings.get_strv(key))
            present = self.uuid in current
            if wanted and not present:
                current.append(self.uuid)
            elif not wanted and present:
                current = [u for u in current if u != self.uuid]
            else:
                continue
            settings.set_strv(key, current)

        settings.sync()
        return True

    def _shell_settings(self) -> Any | None:
        """
        Return a ``Gio.Settings`` for ``org.gnome.shell``, or None when unavailable.

        The schema is looked up before instantiating, because ``Gio.Settings.new()`` aborts the
        process instead of raising when the schema is not installed.

        Returns
        -------
        Gio.Settings or None

        """
        try:
            self._controller._ensure_gi()
            from gi.repository import Gio
        except ImportError:
            return None

        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup("org.gnome.shell", True) is None:
            return None
        return Gio.Settings.new("org.gnome.shell")

    # ------------------------------- D-Bus client -------------------------------

    def _call(self, method: str, params: Any | None = None) -> Any:
        """
        Invoke a method on the highlight extension.

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

        """
        return self._controller._call(
            path=HIGHLIGHT_PATH,
            iface=HIGHLIGHT_IFACE,
            method=method,
            params=params,
        )

    def _require(self) -> None:
        """
        Raise a helpful error when the extension is not answering.

        Raises
        ------
        HighlightError
            If the extension is not installed, not enabled, or not yet loaded.

        """
        if self.is_running():
            return
        if not self.is_installed():
            raise HighlightError(
                "highlight extension is not installed; run "
                "`python -m gnome_window_controller --highlight install`",
            )
        if self.is_enabled():
            raise HighlightError(
                f"highlight extension {self.uuid} is installed and enabled, but GNOME Shell has "
                "not loaded it yet. Log out and back in (Wayland cannot restart the shell "
                "in place), then try again.",
            )
        raise HighlightError(
            f"highlight extension {self.uuid} is installed but not enabled; run "
            f"`gnome-extensions enable {self.uuid}`",
        )

    def is_enabled(self) -> bool:
        """
        Report whether the UUID is listed in the shell's ``enabled-extensions`` setting.

        Returns
        -------
        bool

        """
        settings = self._shell_settings()
        return settings is not None and self.uuid in settings.get_strv("enabled-extensions")

    def options(self) -> dict[str, Any]:
        """
        Return the extension's current options.

        Returns
        -------
        dict

        """
        self._require()
        raw = self._call("GetOptions")
        return json.loads(raw[0] if isinstance(raw, tuple) else raw)

    def configure(self, options: HighlightOptions) -> dict[str, Any]:
        """
        Push new options to the extension and persist them.

        Parameters
        ----------
        options : HighlightOptions
            Only the explicitly-set fields are sent.

        Returns
        -------
        dict
            The effective options after the update.

        Examples
        --------
        >>> # ctl = GnomeWindowController()
        >>> # ctl.highlight.configure(HighlightOptions(color="#fabd2f", width=4))

        """
        self._require()
        payload = self._glib().Variant("(s)", (json.dumps(options.payload()),))
        raw = self._call("SetOptions", payload)
        return json.loads(raw[0] if isinstance(raw, tuple) else raw)

    def flash(self, win_id: int | None = None, duration_ms: int = -1) -> None:
        """
        Draw the border around a window right now.

        Parameters
        ----------
        win_id : int or None, optional
            Window to outline. ``None`` uses whatever currently has focus.
        duration_ms : int, optional
            Milliseconds to keep the border. ``-1`` uses the configured duration,
            ``0`` keeps it until focus moves away. Default is -1.

        """
        self._require()
        glib = self._glib()
        if win_id is None:
            self._call("FlashFocused", glib.Variant("(i)", (int(duration_ms),)))
        else:
            self._call("Highlight", glib.Variant("(ui)", (int(win_id), int(duration_ms))))

    def show_focused(self, duration_ms: int = -1, *, force: bool = True) -> bool:
        """
        Outline the currently focused window on demand.

        Unlike :meth:`flash`, this defaults to ``force=True``: the border is drawn even when the
        highlight mode is ``off`` or the window is one the ``skip_*`` options normally ignore.
        Pressing a "show me the focused window" shortcut is an explicit request, so the automatic
        rules should not silently swallow it.

        Parameters
        ----------
        duration_ms : int, optional
            Milliseconds to keep the border. ``-1`` uses the configured duration, ``0`` keeps it
            until focus moves away. Default is -1.
        force : bool, optional
            Ignore the master switch and the eligibility filters. Default is True.

        Returns
        -------
        bool
            True if the extension reported that a border was drawn.

        Raises
        ------
        HighlightError
            If the extension is not installed, enabled, or loaded.
        """
        self._require()
        glib = self._glib()
        try:
            raw = self._call(
                "ShowFocused",
                glib.Variant("(ib)", (int(duration_ms), bool(force))),
            )
        except DBusError:
            # Extension predates ShowFocused: fall back to the mode-respecting flash.
            self.flash(duration_ms=duration_ms)
            return True
        return bool(raw[0]) if isinstance(raw, tuple) and raw else True

    def try_flash(self, win_id: int | None = None, duration_ms: int = -1) -> bool:
        """
        Flash the border, silently doing nothing when the extension is unavailable.

        Unlike :meth:`flash` this skips the liveness probe, so the happy path costs a single D-Bus
        round trip and a missing extension costs one failed call.

        Parameters
        ----------
        win_id : int or None, optional
            Window to outline; ``None`` means the focused one.
        duration_ms : int, optional
            Milliseconds to keep the border, ``-1`` for the configured value.

        Returns
        -------
        bool
            True if the extension was reached.

        """
        try:
            glib = self._glib()
            if win_id is None:
                self._call("FlashFocused", glib.Variant("(i)", (int(duration_ms),)))
            else:
                self._call("Highlight", glib.Variant("(ui)", (int(win_id), int(duration_ms))))
        except _UNREACHABLE:
            return False
        return True

    def state(self) -> dict[str, Any]:
        """
        Return the extension's live runtime state.

        Returns
        -------
        dict
            Keys ``mode``, ``border_visible``, ``tracked_window_id`` and ``focused_window_id``.
            Older builds of the extension without ``GetState`` yield a dict derived from the
            options instead.
        """
        self._require()
        try:
            raw = self._call("GetState")
        except DBusError:
            options = self.options()
            return {"mode": HighlightOptions.mode_of(options), "border_visible": None}
        return json.loads(raw[0] if isinstance(raw, tuple) else raw)

    def clear(self) -> None:
        """Remove the border immediately."""
        self._require()
        self._call("Clear")

    def _glib(self) -> Any:
        """
        Return the lazily-imported ``GLib`` module from the controller.

        Returns
        -------
        module

        """
        self._controller._ensure_gi()
        return self._controller._glib
