"""
Created on 2026-08-26 23:47:00.

@author: eduardotc
@email: eduardotcampos@hotmail.com

Smoke-test the gnome_window_controller CLI through real subprocess calls.

These exercise the module entry point (``python -m gnome_window_controller``) rather than importing
its internals, so they cover argument parsing, exit codes and terminal output as a user sees them.

Commands that reach GNOME Shell need a session bus and the bundled shell extension; those tests
skip themselves when it is unavailable, so the file stays runnable in CI. To run them for real:

.. code-block:: sh

    dbus-run-session -- pytest -q tests/
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gnome_window_controller.colors import ANSI, Palette, resolve_colors
from gnome_window_controller.errors import DBusError
from gnome_window_controller.gnome_window_controller import (
    DEFAULT_EXCLUDED_APPS,
    LEGACY_WINDOWS_IFACE,
    MONITOR_DIRECTIONS,
    GnomeWindowController,
    cycle_monitor,
    neighbor_monitor,
    normalize_direction,
)
from gnome_window_controller.highlight import HIGHLIGHT_IFACE, HIGHLIGHT_PATH

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture

MODULE = "gnome_window_controller"

#: Repository root, one level above this file.
REPO_ROOT = Path(__file__).resolve().parent.parent

#: Directory holding the package, i.e. the one that must be on the child's import path. An
#: installed copy is used when there is no checkout beside these tests.
SRC_DIR = REPO_ROOT / "src"


#: Matches any SGR escape sequence, not just the ones this project emits.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

#: Environment variables that steer coloring; cleared so each test starts from a known state.
COLOR_ENV = ("GWC_COLORS", "NO_COLOR", "FORCE_COLOR")


def run_cli(
    argv: list[str],
    *,
    color: str | None = "never",
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run the CLI in a subprocess and capture its output.

    Parameters
    ----------
    argv : list of str
        Arguments after ``python -m gnome_window_controller``.
    color : str or None, optional
        Value for ``--color``. ``None`` omits the flag entirely, leaving the decision to the
        environment. Default is ``"never"`` so assertions are not littered with escape codes.
    extra_env : dict, optional
        Environment overrides applied last.

    Returns
    -------
    subprocess.CompletedProcess
        The finished process, with text stdout/stderr.

    Notes
    -----
    ``src`` is put on the child's import path so the tests exercise the checkout rather than
    whatever happens to be installed. It is skipped when there is no ``src`` beside the tests,
    which is how a test run against an installed wheel finds the installed package instead.

    """
    env = dict(os.environ)
    if SRC_DIR.is_dir():
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing}" if existing else str(SRC_DIR)
    for name in COLOR_ENV:
        env.pop(name, None)
    if extra_env:
        env.update(extra_env)

    cmd = [sys.executable, "-m", MODULE, *argv]
    if color is not None:
        cmd += ["--color", color]

    return subprocess.run(
        cmd, capture_output=True, text=True, check=False, cwd=REPO_ROOT, env=env,
    )


@pytest.fixture(scope="session")
def shell_available() -> None:
    """
    Skip the whole test when GNOME Shell's window extensions cannot be reached.

    Returns
    -------
    None

    """
    result = run_cli(["--list-windows", "--json"])
    if result.returncode != 0:
        pytest.skip(f"GNOME Shell window extensions unavailable: {result.stderr.strip()[:120]}")


# --------------------------------------------------------------------------------------
# Palette unit tests: no subprocess, always run.
# --------------------------------------------------------------------------------------


def test_palette_defines_every_name_in_both_states() -> None:
    """Every color resolves whether coloring is on or off, so no format string can NameError."""
    on, off = Palette(enabled=True), Palette(enabled=False)
    for name in ANSI:
        assert getattr(on, name) == ANSI[name]
        assert getattr(off, name) == ""


def test_palette_rejects_unknown_names() -> None:
    """An unknown color raises instead of silently formatting as an empty string."""
    with pytest.raises(AttributeError):
        _ = Palette().NOPE


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("False", False),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
    ],
)
def test_gwc_colors_spellings(value: str, expected: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """``GWC_COLORS`` understands the obvious spellings, including the string ``"False"``."""
    for name in COLOR_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GWC_COLORS", value)
    assert resolve_colors() is expected


def test_color_flag_beats_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit --color value outranks every environment variable."""
    monkeypatch.setenv("GWC_COLORS", "1")
    assert resolve_colors("never") is False
    monkeypatch.setenv("NO_COLOR", "1")
    assert resolve_colors("always") is True


# --------------------------------------------------------------------------------------
# Window backend: which extension serves List/Details/GetTitle/Activate.
# --------------------------------------------------------------------------------------


def _scripted(controller: GnomeWindowController, script: dict[str, object]) -> list[str]:
    """
    Replace the controller's ``_call`` with a stub driven by `script`, and record the ifaces hit.

    Parameters
    ----------
    controller : GnomeWindowController
        Controller to patch in place.
    script : dict
        Maps a D-Bus interface name to what a call on it does: an exception instance is raised,
        anything else is returned.

    Returns
    -------
    list of str
        Interfaces called, in order, appended to as the stub runs.

    """
    seen: list[str] = []

    def fake_call(*, path: str, iface: str, method: str, params: object = None) -> object:
        seen.append(iface)
        outcome = script[iface]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    controller._call = fake_call  # type: ignore[method-assign]
    return seen


def test_window_api_targets_the_bundled_extension() -> None:
    """Window queries address this project's own extension, not the third-party Window Calls."""
    controller = GnomeWindowController()
    assert controller.windows_iface == HIGHLIGHT_IFACE
    assert controller.windows_path == HIGHLIGHT_PATH


def test_missing_interface_falls_back_to_window_calls() -> None:
    """An old shell still running extension v1 is served by Window Calls, with one warning."""
    controller = GnomeWindowController()
    seen = _scripted(
        controller,
        {
            HIGHLIGHT_IFACE: DBusError("GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: no"),
            LEGACY_WINDOWS_IFACE: ("[]",),
        },
    )
    with pytest.warns(RuntimeWarning, match="Window Calls"):
        assert controller._windows_call("List") == ("[]",)
    assert seen == [HIGHLIGHT_IFACE, LEGACY_WINDOWS_IFACE]

    # The working target is remembered, so the dead one is not retried on every call.
    assert controller._windows_call("List") == ("[]",)
    assert seen == [HIGHLIGHT_IFACE, LEGACY_WINDOWS_IFACE, LEGACY_WINDOWS_IFACE]


def test_a_real_failure_is_not_retried_elsewhere() -> None:
    """A bad window id must surface as itself, not send the call to another extension."""
    controller = GnomeWindowController()
    seen = _scripted(controller, {HIGHLIGHT_IFACE: DBusError("no window with id 999999")})
    with pytest.raises(DBusError, match="999999"):
        controller._windows_call("Activate")
    assert seen == [HIGHLIGHT_IFACE]


def test_focused_window_comes_from_the_bundled_extension() -> None:
    """``GetFocused`` is asked first, so Focused Window D-Bus is never needed."""
    controller = GnomeWindowController()
    payload = ('{"id": 7, "title": "kitty", "focus": true}',)
    seen = _scripted(controller, {HIGHLIGHT_IFACE: payload})
    assert controller._focused_payload() == payload
    assert seen == [HIGHLIGHT_IFACE]


def test_empty_getfocused_reply_moves_on() -> None:
    """"Nothing is focused" is not an answer, so the next source gets a turn."""
    controller = GnomeWindowController()
    legacy = controller.focused_iface
    seen = _scripted(controller, {HIGHLIGHT_IFACE: ("",), legacy: ('{"id": 9}',)})
    assert controller._focused_payload() == ('{"id": 9}',)
    assert seen == [HIGHLIGHT_IFACE, legacy]


def test_focused_gives_up_quietly_when_no_extension_answers() -> None:
    """With nothing serving either interface, the caller falls through to the window listing."""
    controller = GnomeWindowController(windows_fallback=False)
    seen = _scripted(
        controller,
        {
            HIGHLIGHT_IFACE: DBusError("GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: no"),
            controller.focused_iface: DBusError("Object does not exist at path"),
        },
    )
    assert controller._focused_payload() is None
    assert seen == [HIGHLIGHT_IFACE, controller.focused_iface]


def test_fallback_can_be_switched_off() -> None:
    """``windows_fallback=False`` fails loudly instead of reaching for Window Calls."""
    controller = GnomeWindowController(windows_fallback=False)
    seen = _scripted(
        controller,
        {HIGHLIGHT_IFACE: DBusError("GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: no")},
    )
    with pytest.raises(DBusError, match="UnknownMethod"):
        controller._windows_call("List")
    assert seen == [HIGHLIGHT_IFACE]


# --------------------------------------------------------------------------------------
# Focus exclusions: pure filtering over a window listing, so no D-Bus needed.
# --------------------------------------------------------------------------------------

#: A listing shaped like `list_windows()` output, with two windows of the same app.
POOL = [
    {"id": 1, "wm_class": "kitty", "wm_class_instance": "kitty", "title": "shell"},
    {"id": 2, "wm_class": "Floorp", "wm_class_instance": "Floorp", "title": "a tab"},
    {"id": 3, "wm_class": "Floorp", "wm_class_instance": "Floorp", "title": "Picture-in-Picture"},
    {"id": 4, "wm_class": "org.gnome.Settings", "wm_class_instance": "Settings", "title": "Sound"},
]


def _kept(exclude: tuple[str, ...], extra: tuple[str, ...] = ()) -> list[int]:
    """
    Return the ids left focusable after applying `exclude` and `extra`.

    Parameters
    ----------
    exclude : tuple of str
        Value for the controller's ``exclude_apps``.
    extra : tuple of str, optional
        Per-call additions.

    Returns
    -------
    list of int
        Ids of the windows a focus command may still land on.

    """
    controller = GnomeWindowController(exclude_apps=exclude)
    return [int(w["id"]) for w in controller.focusable(POOL, extra)]


def test_nothing_is_excluded_by_default_when_the_list_is_empty() -> None:
    """An empty exclusion list leaves the pool untouched."""
    assert _kept(()) == [1, 2, 3, 4]


def test_exclusion_matches_wm_class_case_insensitively() -> None:
    """A name is matched the way --focus matches: substring, any case, class or title."""
    assert _kept(("floorp",)) == [1, 4]
    assert _kept(("FLOORP",)) == [1, 4]
    assert _kept(("gnome.Settings",)) == [1, 2, 3]


def test_exclusion_matches_titles_too() -> None:
    """Excluding by title picks off one window of an app without excluding its siblings."""
    assert _kept(("Picture-in-Picture",)) == [1, 2, 4]


def test_the_built_in_default_skips_picture_in_picture() -> None:
    """The shipped default keeps a floating PiP overlay out of every focus command."""
    assert _kept(DEFAULT_EXCLUDED_APPS) == [1, 2, 4]


def test_extra_names_add_to_the_controller_list() -> None:
    """A per-call exclusion narrows the pool further rather than replacing the standing one."""
    assert _kept(("floorp",), ("kitty",)) == [4]


def test_blank_names_are_ignored() -> None:
    """An empty or whitespace name would match everything, so it is dropped instead."""
    assert _kept(("", "   ")) == [1, 2, 3, 4]


def test_excluding_everything_leaves_nothing_focusable() -> None:
    """When every window is excluded the pool is empty, not silently reset to everything."""
    assert _kept(("o", "i")) == []


def test_exclude_flag_is_documented() -> None:
    """--exclude appears in the help, with the built-in default named."""
    result = run_cli(["--help"])
    assert result.returncode == 0
    assert "--exclude" in result.stdout
    assert DEFAULT_EXCLUDED_APPS[0] in result.stdout


def test_exclude_flag_accepts_repetition() -> None:
    """Both `--exclude a b` and repeated `--exclude` flags collect into one list."""
    from gnome_window_controller.cli import build_parser

    assert build_parser().parse_args(["--exclude", "a", "b"]).exclude == ["a", "b"]
    assert build_parser().parse_args(["--exclude", "a", "--exclude", "b"]).exclude == ["a", "b"]
    assert build_parser().parse_args([]).exclude is None


# --------------------------------------------------------------------------------------
# Monitor geometry: pure functions over layout rectangles, so no D-Bus and no GNOME needed.
# --------------------------------------------------------------------------------------


def _mon(index: int, x: int, y: int, width: int = 1920, height: int = 1080) -> dict[str, int]:
    """
    Build one monitor_layout() entry.

    Parameters
    ----------
    index : int
        Mutter monitor index.
    x, y : int
        Top-left corner in logical pixels.
    width, height : int, optional
        Size in logical pixels. Defaults to 1920x1080.

    Returns
    -------
    dict
        An entry shaped like the ones `monitor_layout()` returns.

    """
    return {"index": index, "x": x, "y": y, "width": width, "height": height}


#: Three side by side, the middle one larger, deliberately not in index order: Mutter numbers
#: monitors however it likes, so nothing may assume index 0 is the leftmost.
ROW = [_mon(0, 4480, 0), _mon(1, 1920, 0, 2560, 1440), _mon(2, 0, 0)]

#: One above the other, the usual laptop-plus-external arrangement.
STACK = [_mon(0, 0, 1080), _mon(1, 0, 0)]

#: Two rows of two.
GRID = [_mon(0, 0, 0), _mon(1, 1920, 0), _mon(2, 0, 1080), _mon(3, 1920, 1080)]

#: An L: two side by side, with a third above the right-hand one.
ELL = [_mon(0, 0, 1080), _mon(1, 1920, 1080), _mon(2, 1920, 0)]


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [("left", "left"), ("RIGHT", "right"), ("up", "up"), ("Down", "down"),
     (1, "right"), (-1, "left"), ("1", "right"), ("-1", "left"),
     ("north", "up"), ("s", "down")],
)
def test_normalize_direction(spelling: str | int, expected: str) -> None:
    """Every accepted spelling lands on one of the four canonical names."""
    assert normalize_direction(spelling) in MONITOR_DIRECTIONS
    assert normalize_direction(spelling) == expected


@pytest.mark.parametrize("bad", [0, "sideways", "", True])
def test_normalize_direction_rejects_nonsense(bad: object) -> None:
    """A direction that means nothing raises rather than silently picking one."""
    with pytest.raises(ValueError, match="direction"):
        normalize_direction(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("layout", "start", "direction", "expected"),
    [
        # A row walks left and right and wraps at both ends.
        (ROW, 2, "right", 1), (ROW, 1, "right", 0), (ROW, 0, "right", 2),
        (ROW, 0, "left", 1), (ROW, 1, "left", 2), (ROW, 2, "left", 0),
        # ...and has nowhere to go vertically, even though its monitors differ in height.
        (ROW, 1, "up", None), (ROW, 1, "down", None),
        (ROW, 2, "up", None), (ROW, 0, "down", None),
        # A stack is the mirror image: vertical works, horizontal does not.
        (STACK, 0, "up", 1), (STACK, 1, "down", 0),
        (STACK, 1, "up", 0), (STACK, 0, "down", 1),
        (STACK, 0, "left", None), (STACK, 1, "right", None),
        # A grid keeps to its row when going sideways and its column when going up or down.
        (GRID, 0, "right", 1), (GRID, 2, "right", 3), (GRID, 3, "left", 2),
        (GRID, 0, "down", 2), (GRID, 3, "up", 1), (GRID, 1, "down", 3),
        # ...including when wrapping.
        (GRID, 1, "right", 0), (GRID, 3, "right", 2), (GRID, 2, "down", 0),
        # An L: the top monitor sits above the *right* one, but going up from the left one
        # still reaches it -- being able to get anywhere beats refusing a diagonal step.
        (ELL, 1, "up", 2), (ELL, 2, "down", 1), (ELL, 0, "up", 2),
        (ELL, 0, "right", 1), (ELL, 2, "left", 0),
    ],
)
def test_neighbor_monitor(
    layout: list[dict[str, int]],
    start: int,
    direction: str,
    expected: int | None,
) -> None:
    """Directional steps follow the rectangles, on every shape of desk."""
    assert neighbor_monitor(layout, start, direction) == expected


@pytest.mark.parametrize("direction", MONITOR_DIRECTIONS)
def test_neighbor_monitor_without_wrap_stops_at_the_edge(direction: str) -> None:
    """With wrap off, the monitor at the end of a row is the end of the line."""
    edges = {"right": 0, "left": 2, "up": None, "down": None}
    start = edges[direction]
    if start is None:
        pytest.skip(f"a single row has no {direction} edge")
    assert neighbor_monitor(ROW, start, direction, wrap=False) is None
    assert neighbor_monitor(ROW, start, direction, wrap=True) is not None


def test_neighbor_monitor_with_one_monitor() -> None:
    """A single monitor has no neighbours in any direction, wrap or not."""
    solo = [_mon(0, 0, 0)]
    for direction in MONITOR_DIRECTIONS:
        assert neighbor_monitor(solo, 0, direction) is None


def test_neighbor_monitor_with_an_unknown_index() -> None:
    """Asking from a monitor that is not in the layout yields nothing, rather than raising."""
    assert neighbor_monitor(ROW, 99, "right") is None


def test_neighbor_monitor_survives_a_layout_without_sizes() -> None:
    """A layout whose sizes could not be read still orders left to right by position."""
    flat = [{"index": i, "x": x, "y": 0, "width": 0, "height": 0}
            for i, x in enumerate((0, 1920, 3840))]
    assert neighbor_monitor(flat, 0, "right") == 1
    assert neighbor_monitor(flat, 2, "right") == 0


def test_cycle_monitor_rings_both_ways() -> None:
    """The flat ring helper still wraps in both directions around an explicit order."""
    order = (2, 1, 0)
    assert cycle_monitor(2, 1, order) == 1
    assert cycle_monitor(0, 1, order) == 2
    assert cycle_monitor(2, -1, order) == 0


def test_cycle_monitor_rejects_an_unknown_monitor() -> None:
    """A monitor outside the order is an error, not a silent wrap to the first entry."""
    with pytest.raises(ValueError, match="not in"):
        cycle_monitor(7, 1, (2, 1, 0))


# --------------------------------------------------------------------------------------
# CLI behaviour that needs no D-Bus.
# --------------------------------------------------------------------------------------


def test_version() -> None:
    """``--version`` succeeds and prints the package version."""
    result = run_cli(["--version"])
    assert result.returncode == 0
    assert MODULE in result.stdout
    assert not ANSI_RE.search(result.stdout)


def test_no_arguments_succeeds() -> None:
    """Running with no arguments is not an error."""
    assert run_cli([], color=None).returncode == 0


def test_invalid_chfocus_is_a_usage_error() -> None:
    """An unknown --chfocus command exits 2, argparse's usage-error code."""
    result = run_cli(["--chfocus", "definitely-not-a-command"])
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


def test_help_lists_the_color_flag() -> None:
    """The --color flag is documented in the help output."""
    result = run_cli(["--help"])
    assert result.returncode == 0
    assert "--color" in result.stdout


# --------------------------------------------------------------------------------------
# Coloring, end to end through the real entry point.
# --------------------------------------------------------------------------------------


def test_color_never_emits_no_escapes(shell_available: None) -> None:
    """``--color never`` produces output free of ANSI escapes."""
    result = run_cli(["--list-windows"], color="never")
    assert result.returncode == 0
    assert not ANSI_RE.search(result.stdout), "escape codes leaked into --color never output"


def test_color_always_emits_escapes(shell_available: None) -> None:
    """``--color always`` colors even when stdout is a pipe rather than a terminal."""
    result = run_cli(["--list-windows"], color="always")
    assert result.returncode == 0
    assert ANSI_RE.search(result.stdout), "expected escape codes with --color always"


def test_gwc_colors_env_disables_color(shell_available: None) -> None:
    """``GWC_COLORS=False`` disables coloring with no command-line flag involved."""
    result = run_cli(["--list-windows"], color=None, extra_env={"GWC_COLORS": "False"})
    assert result.returncode == 0
    assert not ANSI_RE.search(result.stdout)


def test_no_color_env_disables_color(shell_available: None) -> None:
    """The NO_COLOR convention is honored."""
    result = run_cli(["--list-windows"], color=None, extra_env={"NO_COLOR": "1"})
    assert result.returncode == 0
    assert not ANSI_RE.search(result.stdout)


def test_auto_color_is_off_when_piped(shell_available: None) -> None:
    """With no flag and no environment override, a piped run stays uncolored."""
    result = run_cli(["--list-windows"], color=None)
    assert result.returncode == 0
    assert not ANSI_RE.search(result.stdout)


# --------------------------------------------------------------------------------------
# Commands that talk to GNOME Shell.
# --------------------------------------------------------------------------------------


def test_list_windows_mentions_focus(shell_available: None) -> None:
    """``--list-windows`` reports a focus flag for each window."""
    result = run_cli(["--list-windows"])
    assert result.returncode == 0
    assert "focus" in result.stdout


def test_list_windows_json_parses(shell_available: None) -> None:
    """``--list-windows --json`` emits valid JSON and never colors it."""
    result = run_cli(["--list-windows", "--json"], color="always")
    assert result.returncode == 0
    windows = json.loads(result.stdout)
    assert isinstance(windows, list)
    assert not ANSI_RE.search(result.stdout), "JSON output must stay machine readable"
    for win in windows:
        assert {"id", "title", "wm_class"} <= win.keys()


def test_list_monitors(shell_available: None) -> None:
    """``--list-monitors`` succeeds and names the left-to-right order."""
    result = run_cli(["--list-monitors"])
    assert result.returncode == 0
    assert "order" in result.stdout


def test_list_windows_benchmark(benchmark: BenchmarkFixture, shell_available: None) -> None:
    """Benchmark ``python -m gnome_window_controller --list-windows``."""
    result = benchmark(run_cli, ["--list-windows"])
    assert result.returncode == 0
    assert "focus" in result.stdout
