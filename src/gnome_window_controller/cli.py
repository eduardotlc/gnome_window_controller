"""
Created on 2026-07-19 08:21:03.

@author: eduardotc
@email: eduardotcampos@hotmail.com

Terminal client commands handling.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .colors import COLORS, supports_ansi_colors
from .errors import HighlightError, WindowControllerError
from .gnome_window_controller import (
    DEFAULT_EXCLUDED_APPS,
    MONITOR_DIRECTIONS,
    WORKSPACE_SCOPES,
    GnomeWindowController,
)
from .highlight import HIGHLIGHT_MODES, HighlightOptions

CHFOCUS_PRIMARY = ("monitor", "win", "right", "left", "up", "down", "last")

CHFOCUS_SECONDARY = (
    "top",
    "bottom",
    "same_app",
    "same_monitor",
    "same_monitor_down",
    "same_monitor_up",
)

HIGHLIGHT_ACTIONS = ("status", "install", "uninstall", "on", "off", "flash", "clear")

#: ``--highlight-*`` flags that configure the border. Passing any of them without an explicit
#: ``--highlight ACTION`` still applies them, instead of silently doing nothing.
HIGHLIGHT_OPTION_DESTS = (
    "highlight_mode",
    "highlight_color",
    "highlight_width",
    "highlight_radius",
    "highlight_inset",
    "highlight_duration",
)

MODE_BLURB = {
    "always": "every focus change, mouse clicks included",
    "commands": "only focus changes made by this module",
    "off": "never draw",
}

EPILOG = """
examples:
  gnome_window_controller --list-windows
  gnome_window_controller --list-windows --json
  gnome_window_controller --focus kitty
  gnome_window_controller --chfocus right
  gnome_window_controller --chfocus up             # monitor above, on a stacked layout
  gnome_window_controller --chfocus win --exclude Slack --exclude mail
  gnome_window_controller --chfocus win same_monitor --workspace current
  gnome_window_controller --chfocus right --workspace prefer-current
  gnome_window_controller --chfocus win same_app
  gnome_window_controller --highlight install
  gnome_window_controller --highlight on --highlight-color '#fb4934' --highlight-width 4
  gnome_window_controller --highlight flash
  gnome_window_controller --show-focus                # outline the focused window now
  gnome_window_controller --highlight-mode commands  # only our own focus changes
  gnome_window_controller --highlight-mode off       # disable the border

suggested keybindings:
  Super+L  --chfocus right
  Super+H  --chfocus left
  Super+J  --chfocus win same_monitor
  Super+K  --chfocus win same_monitor_up
  Super+Shift+K  --chfocus up    # only useful on a stacked or grid layout
  Super+Shift+J  --chfocus down
  Super+R  --chfocus last
  Super+C  --chfocus monitor top
  Super+X  --chfocus win same_app
"""


#: Re-exported for backwards compatibility; the implementation lives in `colors`.
__all__ = ["build_parser", "main", "supports_ansi_colors"]


def build_parser() -> argparse.ArgumentParser:
    """
    Create the application's argument parser.

    Returns
    -------
    argparse.ArgumentParser

    Examples
    --------
    >>> parser = build_parser()
    >>> args = parser.parse_args(["--chfocus", "right"])
    >>> args.chfocus
    ['right']

    """
    parser = argparse.ArgumentParser(
        prog="gnome_window_controller",
        description="Control gnome windows and focus.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    query = parser.add_argument_group("queries")

    query.add_argument(
        "--list-windows",
        action="store_true",
        help="List all gnome windows and theirs infos.",
    )

    query.add_argument(
        "--details-windows",
        action="store_true",
        help="Details all gnome windows infos through extension.",
    )

    query.add_argument(
        "--list-monitors",
        action="store_true",
        help="List logical monitors in physical left-to-right order.",
    )

    query.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help=(
            "Colored output. 'auto' (default) colors only an interactive terminal. Overrides the "
            "GWC_COLORS, NO_COLOR and FORCE_COLOR environment variables."
        ),
    )

    query.add_argument(
        "--json",
        action="store_true",
        help="Emit query results as JSON instead of colored text.",
    )

    focus = parser.add_argument_group("focus")

    focus.add_argument(
        "--focus",
        type=str,
        help="Focus given wm_class/instance/title window.",
        nargs="*",
        metavar="NEEDLE",
    )

    focus.add_argument(
        "--scope",
        choices=("any", "current-monitor", "other-monitor"),
        default="other-monitor",
        help="Which monitor --focus searches. Default: other-monitor.",
    )

    focus.add_argument(
        "--workspace",
        choices=WORKSPACE_SCOPES,
        metavar="SCOPE",
        help=(
            "How focus commands treat other workspaces. 'current': only windows on the "
            "workspace in view. 'prefer-current': those first, then the rest. 'any': no "
            "weighting. Left out, each command keeps its own default -- only "
            "`--chfocus win same_monitor` stays on the current workspace."
        ),
    )

    focus.add_argument(
        "--exclude",
        action="extend",
        nargs="*",
        metavar="NAME",
        help=(
            "Never focus a window whose wm_class, instance or title contains NAME "
            "(case-insensitive). Repeatable, and added to the built-in list "
            f"({', '.join(DEFAULT_EXCLUDED_APPS)}). Applies to --focus and every --chfocus "
            "command; listings are unaffected."
        ),
    )

    focus.add_argument(
        "--chfocus",
        type=str,
        metavar="COMMAND",
        choices=(*CHFOCUS_PRIMARY, *CHFOCUS_SECONDARY),
        nargs="*",
        help=(
            "Change focus based on passed arguments. First arg is required, second is optional "
            "and depends on the first one. "
            "arg 1: monitor, win, right, left, up, down, last. "
            "arg 2: monitor -> top|bottom; "
            "win -> same_app|same_monitor|same_monitor_down|same_monitor_up; "
            "right, left, up, down, last -> none. "
            "The directions follow the real monitor rectangles, so they work on a row, a "
            "vertical stack or a grid; see --list-monitors."
        ),
    )

    highlight = parser.add_argument_group("focused-window highlight")

    highlight.add_argument(
        "--highlight",
        nargs="?",
        const="status",
        choices=HIGHLIGHT_ACTIONS,
        metavar="ACTION",
        help=(
            "Manage the focused-window border. ACTION is one of: "
            f"{', '.join(HIGHLIGHT_ACTIONS)}. Defaults to 'status'."
        ),
    )

    highlight.add_argument(
        "--highlight-mode",
        choices=tuple(HIGHLIGHT_MODES),
        metavar="MODE",
        help=(
            "When the border is drawn. 'always': every focus change, including mouse clicks. "
            "'commands': only focus changes made by this module. 'off': never."
        ),
    )

    highlight.add_argument("--highlight-color", metavar="CSS", help="Border color, e.g. '#993c5a'.")
    highlight.add_argument("--highlight-width", type=int, metavar="PX", help="Border thickness.")
    highlight.add_argument("--highlight-radius", type=int, metavar="PX", help="Corner radius.")
    highlight.add_argument("--highlight-inset", type=int, metavar="PX", help="Outward offset.")
    highlight.add_argument(
        "--highlight-duration",
        type=int,
        metavar="MS",
        help="Milliseconds the border stays up; 0 keeps it until focus moves.",
    )
    highlight.add_argument(
        "--show-focus",
        action="store_true",
        help=(
            "Outline the currently focused window right now. Meant for a keyboard shortcut: it "
            "draws even when the highlight mode is 'off'. Honours --highlight-duration."
        ),
    )

    highlight.add_argument(
        "--no-highlight",
        action="store_true",
        help="Do not flash the border for windows focused by this invocation.",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    return parser


def _print_windows(windows: list[dict[str, object]], as_json: bool) -> None:
    """
    Render a window listing.

    Parameters
    ----------
    windows : list of dict
        Entries from ``GnomeWindowController.list_windows()``.
    as_json : bool
        Emit JSON instead of colored text.

    """
    if as_json:
        print(json.dumps(windows, indent=2, ensure_ascii=False))
        return

    for idx, win in enumerate(windows):
        print(idx)
        for key, value in win.items():
            print(f"{COLORS.YLW}{key}{COLORS.RST}: {COLORS.BLUE}{value}{COLORS.RST}")
        print("")


def _print_details(ctl: GnomeWindowController, as_json: bool) -> int:
    """
    Render ``Details(id)`` for every listed window.

    Parameters
    ----------
    ctl : GnomeWindowController
        Controller used for the D-Bus calls.
    as_json : bool
        Emit JSON instead of colored text.

    Returns
    -------
    int
        Process exit code.

    """
    windows = ctl.list_windows()

    if as_json:
        payload = []
        for win in windows:
            win_id = win.get("id")
            if win_id is None:
                continue
            payload.append(ctl.details(int(win_id)))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    for win in windows:
        win_id = win.get("id")
        if win_id is None:
            continue
        title = str(win.get("title", ""))
        print(
            "\n".join([
                "",
                f"{COLORS.YLW}{win.get('wm_class', '')}{COLORS.RST}",
                f"{COLORS.VLT}{title}{COLORS.RST}",
                f"{COLORS.ORANGE}{'=' * len(title)}{COLORS.RST}",
            ]),
        )
        for key, value in ctl.details(int(win_id)).items():
            print(f"{COLORS.LRED}{key}{COLORS.RST} : {COLORS.BLD}{value}{COLORS.RST}")
    return


def _print_monitors(ctl: GnomeWindowController, as_json: bool) -> int:
    """
    Render the logical monitor layout.

    Parameters
    ----------
    ctl : GnomeWindowController
        Controller used for the D-Bus calls.
    as_json : bool
        Emit JSON instead of colored text.

    Returns
    -------
    int
        Process exit code.

    """
    monitors = ctl.monitor_layout()
    current = ctl.current_monitor_from_details()
    neighbors = {
        int(mon["index"]): {
            heading: ctl.monitor_neighbor(heading, current_monitor=int(mon["index"]))
            for heading in MONITOR_DIRECTIONS
        }
        for mon in monitors
    }

    if as_json:
        print(
            json.dumps(
                {
                    "order": list(ctl.monitor_order()),
                    "current": current,
                    "monitors": [m | {"neighbors": neighbors[int(m["index"])]} for m in monitors],
                },
                indent=2,
            ),
        )
        return 0

    if not monitors:
        print("No monitor layout available from org.gnome.Mutter.DisplayConfig.", file=sys.stderr)
        return 1

    print(f"left-to-right order: {COLORS.GRN}{list(ctl.monitor_order())}{COLORS.RST}")
    for mon in monitors:
        index = int(mon["index"])
        here = " <" if index == current else ""
        print(
            " ".join([
                f"{COLORS.YLW}[{index}]{COLORS.RST}",
                f"{COLORS.BLUE}{','.join(mon['connectors']) or '?'}{COLORS.RST}",
                f"{mon['width']}x{mon['height']} @ {mon['x']},{mon['y']}",
                f" scale {mon['scale']}",
                f"{COLORS.GRN}{' (primary)' if mon['primary'] else ''}{COLORS.RST}",
                f"{COLORS.GRN}{here}{COLORS.RST}",
            ]),
        )

    # The whole point of the directional commands is that they follow this table, so print it:
    # a layout where `up` says "-" is one where `--chfocus up` has nowhere to go.
    print("\nneighbors (what --chfocus <direction> follows, wrapping at the edges):")
    header = "".join(f"{heading:>8}" for heading in MONITOR_DIRECTIONS)
    print(f"  {'from':>5}{header}")
    for mon in monitors:
        index = int(mon["index"])
        cells = "".join(
            f"{'-' if neighbors[index][heading] is None else neighbors[index][heading]:>8}"
            for heading in MONITOR_DIRECTIONS
        )
        print(f"  {COLORS.YLW}{index:>5}{COLORS.RST}{cells}")
    return 0


def _highlight_options(args: argparse.Namespace) -> HighlightOptions:
    """
    Collect the ``--highlight-*`` flags into an options object.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments.

    Returns
    -------
    HighlightOptions

    """
    options = HighlightOptions(
        color=args.highlight_color,
        width=args.highlight_width,
        radius=args.highlight_radius,
        inset=args.highlight_inset,
        duration_ms=args.highlight_duration,
    )
    if args.highlight_mode is not None:
        for key, value in HIGHLIGHT_MODES[args.highlight_mode].items():
            setattr(options, key, value)
    return options


def _run_highlight(ctl: GnomeWindowController, args: argparse.Namespace) -> int:
    """
    Execute the requested ``--highlight`` action.

    Parameters
    ----------
    ctl : GnomeWindowController
        Controller owning the highlight client.
    args : argparse.Namespace
        Parsed command-line arguments.

    Returns
    -------
    int
        Process exit code.

    """
    hl = ctl.highlight
    action = args.highlight or "status"

    if action == "install":
        was_running = hl.is_running()
        path = hl.install(force=True)
        print(f"Installed {hl.uuid}\n       -> {path}")
        if not hl.set_extension_enabled(enable=True):
            print(
                f"Could not enable it; run `gnome-extensions enable {hl.uuid}`.",
                file=sys.stderr,
            )
            return 1
        print("Enabled.")
        if was_running:
            print(
                "An older copy is already loaded in this session. GJS caches the ES module, so "
                "log out and back in to run the code just installed and to expose its "
                "preferences dialog.",
            )
        elif not hl.is_running():
            print(
                "GNOME Shell has not loaded it yet. Wayland cannot restart the shell in place, "
                "so log out and back in to activate the highlight.",
            )
        return 0

    if action == "uninstall":
        removed = hl.uninstall()
        print("Removed." if removed else "Nothing to remove.")
        return 0

    options = _highlight_options(args)

    if action in {"on", "off"}:
        options.enabled = action == "on"
        if action == "on":
            hl.set_extension_enabled(enable=True)

    if action == "clear":
        hl.clear()
        return 0

    if action == "flash":
        if options.payload():
            hl.configure(options)
        # An explicit 0 means "keep it until focus moves"; only an absent flag means "use config".
        duration = -1 if args.highlight_duration is None else args.highlight_duration
        hl.flash(duration_ms=duration)
        return 0

    # 'status' reports what it can instead of failing when the shell has not loaded the extension.
    running = hl.is_running()
    if action == "status" and not running:
        state = {
            "extension": hl.uuid,
            "installed": hl.is_installed(),
            "enabled": hl.is_enabled(),
            "running": False,
        }
        if args.json:
            print(json.dumps(state, indent=2))
        else:
            for key, value in state.items():
                print(f"{key:<10}: {value}")
            print(
                "\nNot answering on the session bus. "
                + (
                    "Log out and back in to let GNOME Shell load it."
                    if state["installed"] and state["enabled"]
                    else "Run `--highlight install` first."
                ),
            )
        return 1

    effective = hl.configure(options) if options.payload() else hl.options()
    mode = HighlightOptions.mode_of(effective)

    if args.json:
        print(json.dumps({"mode": mode} | effective, indent=2))
    else:
        print(f"extension : {hl.uuid}")
        print(f"installed : {'yes' if hl.is_installed() else 'no'}")
        print("running   : yes")
        print(f"mode      : {mode}  ({MODE_BLURB[mode]})")
        for key, value in effective.items():
            print(f"{key:<16}: {value}")
    return 0


def _run_chfocus(ctl: GnomeWindowController, chfocus: list[str]) -> int:
    """
    Execute a ``--chfocus`` command pair.

    Parameters
    ----------
    ctl : GnomeWindowController
        Controller performing the focus change.
    chfocus : list of str
        The one or two ``--chfocus`` words.

    Returns
    -------
    int
        Process exit code: 0 when a window was focused, 1 otherwise.

    """
    command = chfocus[0]
    option = chfocus[1] if len(chfocus) > 1 else None

    match command:
        case "monitor":
            top = option != "bottom"
            focused = ctl.focus_other_monitor_window(focus_top_window=top)

        case "win" if option == "same_app":
            focused = ctl.focus_same_name_window() or ctl.focus_last_window()

        case "win" if option in {"same_monitor", "same_monitor_down"}:
            focused = ctl.focus_same_monitor_window(other_workspace=False)

        case "win":
            other = ctl.get_current_workspace_index() == 1
            focused = ctl.focus_same_monitor_window(change_idx=-1, other_workspace=other)

        case command if command in MONITOR_DIRECTIONS:
            focused = ctl.cycle_monitors(direction=command)
            if focused is None and ctl.monitor_neighbor(command) is None:
                print(
                    f"No monitor {command} of the current one. "
                    "`--list-monitors` shows which directions this layout has.",
                    file=sys.stderr,
                )

        case "last":
            focused = ctl.focus_last_window()

        case _:
            print(f"Unknown --chfocus command: {command!r}", file=sys.stderr)
            return 2

    return 0 if focused else 1


def main(argv: list[str] | None = None) -> int:
    """
    Run the command-line interface.

    Parameters
    ----------
    argv
        Command-line arguments. If ``None``, ``sys.argv`` is used.

    Returns
    -------
    int
        Process exit code.

    Examples
    --------
    Calling with no arguments prints the help and succeeds. The help is written only when
    stdout is a terminal, so a redirected run stays silent:

    >>> import contextlib, io
    >>> out = io.StringIO()
    >>> with contextlib.redirect_stdout(out):
    ...     code = main([])
    >>> code
    0
    >>> out.getvalue()
    ''

    """
    argv = list(sys.argv[1:] if argv is None else argv)

    parser = build_parser()
    isterm = sys.stdout.isatty()

    # No arguments at all: show the help instead of doing nothing.
    if not argv:
        if isterm:
            parser.print_help()
        return 0

    args = parser.parse_args(argv)

    # Resolve colors before the first byte of output.
    COLORS.configure(args.color)

    ctl = GnomeWindowController(
        highlight_on_focus=not args.no_highlight,
        exclude_apps=(*DEFAULT_EXCLUDED_APPS, *(args.exclude or ())),
        workspace_scope=args.workspace,
    )

    try:
        if args.list_windows:
            _print_windows(ctl.list_windows(), args.json)
            return 0

        if args.details_windows:
            return _print_details(ctl, args.json)

        if args.list_monitors:
            return _print_monitors(ctl, args.json)

        if args.show_focus:
            duration = -1 if args.highlight_duration is None else args.highlight_duration
            if ctl.highlight.show_focused(duration):
                return 0
            print("No window is focused; nothing to outline.", file=sys.stderr)
            return 1

        if args.highlight is not None or any(
            getattr(args, dest) is not None for dest in HIGHLIGHT_OPTION_DESTS
        ):
            return _run_highlight(ctl, args)

        if args.focus is not None:
            if not args.focus:
                parser.error("--focus needs a wm_class, instance or title fragment")
            needle = " ".join(args.focus)
            focused = ctl.focus_named_window(
                needle,
                only_current_monitor=args.scope == "current-monitor",
                only_other_monitor=args.scope == "other-monitor",
            )
            return 0 if focused else 1

        if args.chfocus is not None:
            if not args.chfocus:
                parser.error(f"--chfocus needs one of: {', '.join(CHFOCUS_PRIMARY)}")
            return _run_chfocus(ctl, args.chfocus)

    except HighlightError as error:
        if isterm:
            print(f"highlight: {error}", file=sys.stderr)
        return 1
    except WindowControllerError as error:
        if isterm:
            print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    # Flags were given, but none of them selected an action.
    if isterm:
        parser.print_help()

    return 0
