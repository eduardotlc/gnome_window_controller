# Development

Wayland-safe — every operation goes through a GNOME Shell extension over D-Bus, never through
X11 window coordinates. The extension is bundled here.

## Install

```sh
git clone https://github.com/eduardotlc/gnome_window_controller
cd gnome_window_controller
python -m pip install --user -e '.[dev]'  # editable, plus pytest / ruff / build / twine
```

The project uses a `src/` layout: the importable package lives in `src/gnome_window_controller/`,
the tests in `tests/`. Nothing at the repository root is part of the distribution, which is what
keeps `gi_utils.py` — a module from a different project that happens to live here — out of the
wheel.

---

## Testing

```sh
pytest              # unit tests and doctests (the doctests need a live GNOME session)
pytest tests/       # unit tests only; this is what CI runs
```

`pytest` with no arguments picks up `testpaths` from `pyproject.toml` and adds
`--doctest-modules`, so it exercises the examples in every docstring against the running shell.
Anywhere headless, that will fail by design — use `pytest tests/`, where everything needing a
GNOME session skips itself.

---

## Packaging

```sh
python -m build                      # sdist + wheel into dist/
python -m twine check --strict dist/*  # the metadata and README checks PyPI applies
python -m twine upload dist/*
```

The wheel has to carry the shell extension and the completions as package data; without the
extension the module cannot talk to GNOME at all. To confirm a build did:

```sh
python -c 'import zipfile,glob; print(*sorted(zipfile.ZipFile(glob.glob("dist/*.whl")[0]).namelist()), sep="\n")'
```

The version is read from `src/gnome_window_controller/__init__.py`, so a release is a bump to
`__version__` and nothing else.

---

## Exit Codes

`0` success

`1` nothing focused / extension unreachable

`2` bad usage.

---

## Shortcuts

- GNOME does **not** run the command through a shell. It is parsed with `g_shell_parse_argv` and
  spawned directly — no pipes, no `~`, no `$VAR`, no `&&`, no shell builtins.

- gnome-shell's `PATH` does **not** include `~/.local/bin`, so give the **absolute path**:

To make the bare name work instead, expose the directory to the session at login:

```sh
mkdir -p ~/.config/environment.d
echo 'PATH=$HOME/.local/bin:$PATH' > ~/.config/environment.d/local-bin.conf
```

---

## Colored Printing

Colored printing may be disable, both for terminal running command, or for pytest testing.

Precedence, highest first:

| #   | Source                             | Effect                                                 |
| --- | ---------------------------------- | ------------------------------------------------------ |
| 1   | `--color always` / `--color never` | wins outright                                          |
| 2   | `GWC_COLORS`                       | `1/true/yes/on` → on; `0/false/no/off` → off           |
| 3   | `NO_COLOR` nonempty                | off ([no-color.org](https://no-color.org))             |
| 4   | `FORCE_COLOR` nonempty             | on                                                     |
| 5   | auto                               | on when stdout is a tty and `TERM` looks color-capable |

`--json` output is never colored, whatever the mode.

In Python, the palette is a live object rather than a set of module constants:

```python
from gnome_window_controller import COLORS

COLORS.configure("never")     # or "always" / "auto"
COLORS.enabled = False        # same thing, directly
print(f"{COLORS.YLW}title{COLORS.RST}")   # -> "title"
```

Every name is served from one lookup table, so a color can never be defined while colors are on
and missing while they are off — write `COLORS.NOT_A_COLOR` and you get an `AttributeError`, not a
silently empty string.

---

## Structure

```sh
gnome-window-controller highlight --install
```

Copies the bundled extension out of the package, compiles its GSettings schema and enables it.
The extension is **required**, not optional: it serves every window query, including which
window has focus, as well as the border. Turning the border off (`--highlight off`) is a setting,
and leaves the extension loaded; `gnome-extensions disable` would take the window API down with it.

**A note on `PYTHONPATH` and `.pth` files**

The `src/` layout means the repository root no longer holds an importable
`gnome_window_controller` package, so having the parent directory on `PYTHONPATH` no longer
shadows the installed copy: the root directory is at most a namespace-package portion, and a real
package anywhere on the path beats it. Check which copy is live with:

```sh
python -c "import gnome_window_controller as g; print(g.__file__)"
```

---

## Focused-Window Highlight Extensions

A colored border is drawn around the focused window and follows it as focus, position and size
change.

Mutter implements neither `wlr-layer-shell` nor unrestricted `org.gnome.Shell.Eval`, so an
overlay drawn from a plain GTK process cannot be positioned, raised above other windows or made
click-through under GNOME/Wayland. The border is therefore painted by the GNOME Shell extension
bundled in `shell_extension/`, which the CLI installs and drives over D-Bus. That same extension
answers the window queries (`List`, `Details`, `GetTitle`, `Activate`, `GetFocused`), so
installing it once covers both jobs.

> **Log out and back in after installing.** GNOME Shell only scans for new extensions at
> startup, and Wayland cannot restart the shell in place. The same applies after _editing_
> `extension.js` — GJS caches the ES module, so neither `gnome-extensions disable/enable` nor
> `ReloadExtension` picks up JS changes. Option changes via D-Bus do apply live.

---

### When Is the Border Drawn?

The extension cannot know _who_ moved the focus, but it can distinguish a focus change it was
merely notified about from one this module explicitly asked it to draw. That is what the mode
selects:

```sh
gnome-window-controller --highlight-mode always     # default
gnome-window-controller --highlight-mode commands   # only our own focus changes
gnome-window-controller --highlight-mode off        # never draw
```

| mode       | mouse click / alt-tab | `--chfocus`, `--focus`, `--highlight flash` |
| ---------- | --------------------- | ------------------------------------------- |
| `always`   | border                | border                                      |
| `commands` | _nothing_             | border, cleared once focus moves elsewhere  |
| `off`      | _nothing_             | _nothing_                                   |

`--highlight-mode` maps onto two extension flags: `enabled` (the master switch) and
`follow_focus` (redraw on every focus notification). `off` deliberately leaves `follow_focus`
alone, so `--highlight on` returns you to whichever of `always`/`commands` you last used.

In `commands` mode the border a command drew is kept until focus moves to a _different_ window.
That matters because Mutter's focus notification for an activation can arrive _after_ the D-Bus
call that drew the border — dropping it on every notification would erase it instantly.

For a single invocation, `--no-highlight` suppresses the flash without touching the mode.

### Preferences Dialog

The extension ships a normal GNOME preferences dialog — the gear icon next to it in the
**Extensions** app, or:

```sh
gnome-extensions prefs gnome-window-controller@eduardotcampos.hotmail.com
```

It exposes the highlight mode, colour (a real colour picker), thickness, corner radius, outward
offset, visible/fade durations, and which window kinds to skip.

Preferences run in a **separate process** from GNOME Shell, so they cannot call the extension
directly. GSettings is the shared store and the single source of truth:

```
prefs.js ──┐
           ├──> GSettings ──> extension.js ("changed" handler) ──> redraw
CLI ───────┘    (dconf)
```

`SetOptions` over D-Bus writes the same keys, so changing something with `--highlight-color` is
immediately reflected in an open preferences dialog, and vice versa. Read the keys directly with:

```sh
export GSETTINGS_SCHEMA_DIR=~/.local/share/gnome-shell/extensions/gnome-window-controller@eduardotcampos.hotmail.com/schemas
gsettings list-recursively org.gnome.shell.extensions.gnome-window-controller
```

Settings previously kept in `~/.config/gnome-window-controller/highlight.json` are imported once,
on the first start after upgrading (guarded by the internal `config-migrated` key). The JSON file
is then no longer read or written; delete it whenever you like.

### Show the Focused Window on Demand**

```sh
gnome-window-controller --show-focus
```

Outlines whatever has focus right now — the "where am I?" command, meant for a keyboard shortcut.
It is bound to `Super + I` in the suggested set below.

It **forces** the border: it draws even when the mode is `off`, and even for windows the
`skip_*`/`only_normal` filters would normally ignore. Pressing a shortcut is an explicit request,
so the automatic rules should not silently swallow it. The mode itself is left untouched.

`--highlight-duration` applies as usual; with the configured duration at `0` the border stays
until focus moves away. Exit status is `1` when nothing had focus.

Use `--highlight flash` instead if you want the mode-respecting version (draws nothing when the
mode is `off`).

---

## Python API

Imports of `gi` are lazy, so `--help` and `--version` never pay for them.

```python
from gnome_window_controller import GnomeWindowController, HighlightOptions

ctl = GnomeWindowController()

ctl.list_windows(with_monitor=True)   # list[dict]
ctl.get_focused()                     # dict | None
ctl.details(win_id)                   # dict, cached per call cycle
ctl.monitor_order()                   # e.g. (1, 0, 2) — Mutter indices, left to right
ctl.monitor_layout()                  # rects, scales, connectors

ctl.focus_named_window("kitty")
ctl.cycle_monitors(direction=1)
ctl.focus_same_monitor_window()
ctl.focus_same_name_window()
ctl.focus_last_window()

ctl.highlight.configure(HighlightOptions(color="#fabd2f", width=4))
ctl.highlight.configure(HighlightOptions.from_mode("commands"))
ctl.highlight.flash()                     # respects the mode
ctl.highlight.show_focused()              # forces, even when the mode is off
ctl.highlight.state()      # {'mode', 'border_visible', 'tracked_window_id', ...}
```

Failures raise `WindowControllerError`, with `DBusError` for unreachable extensions and
`HighlightError` for highlight-specific problems.

Monitor order is read from `org.gnome.Mutter.DisplayConfig` and sorted by physical position, so
`--chfocus right`/`left` follow your actual layout instead of Mutter's arbitrary indices. It falls
back to the `MONITOR_ORDER` constant if the layout cannot be read.

---

## Notes

- `get_current_workspace_index()` reads the focused window's `workspace` field, which works
  natively on Wayland; it only falls back to `xprop` when the extension omits it.

- `list_windows()` seeds the title cache, so matching a window by title costs no extra D-Bus
  round trips.

- Everything shell-side lives on one interface, `org.gnome.Shell.Extensions.GnomeWindowController`
  at `/org/gnome/Shell/Extensions/GnomeWindowController`.

  - `gdbus introspect --session --dest org.gnome.Shell --object-path /org/gnome/Shell/Extensions/GnomeWindowController`
    lists it.

- `list_windows()` already reports each window's `monitor`, so `with_monitor=True` (one `Details()`
  per window) is only needed against an older extension build.
