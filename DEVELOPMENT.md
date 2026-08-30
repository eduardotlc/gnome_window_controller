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

## Packaging and Releasing

The version is read from `src/gnome_window_controller/__init__.py`, so a release is a bump to
`__version__` and nothing else. **A version number on PyPI can never be reused or replaced**, even
after deleting the release — every mistake costs a version number, which is why the rehearsal on
TestPyPI below is worth the five minutes.

### 1. Accounts and 2FA

Register on both, with the *same* username if you like — the two are entirely separate databases:

- <https://pypi.org/account/register/>
- <https://test.pypi.org/account/register/>

Two-factor authentication is mandatory on PyPI. Add an authenticator app under
**Account settings → Two factor authentication**, and save the recovery codes somewhere you can
reach without your phone. You cannot create an API token until 2FA is on.

### 2. Generate an API token

A token is a password that carries its own scope, so it can be revoked without touching the
account. Tokens are shown **once**, at creation.

1. Go to <https://pypi.org/manage/account/token/> (and
   <https://test.pypi.org/manage/account/token/> for the rehearsal).
2. **Token name**: something you will recognise in six months, e.g. `laptop-gwc-release`.
3. **Scope**: `Entire account`. A project-scoped token cannot exist for a project that has never
   been uploaded — narrow it after the first release, see step 7.
4. Copy the value. It starts with `pypi-` and is the *whole* credential.

### 3. Add the token

The token goes in `~/.pypirc`. The username is the literal string `__token__` for every token —
it is not your account name.

```sh
umask 077                      # so the file is created 0600, not world readable
cat > ~/.pypirc <<'EOF'
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-AgEIcHlwaS5vcmc...      # the PyPI token

[testpypi]
repository = https://test.pypi.org/legacy/
username = __token__
password = pypi-AgENdGVzdC5weXBpLm9yZw...   # the TestPyPI token, a different one
EOF
chmod 600 ~/.pypirc
```

For a one-off upload, or on a machine where you would rather not leave the token on disk,
environment variables take precedence and need no file:

```sh
TWINE_USERNAME=__token__ TWINE_PASSWORD='pypi-...' python -m twine upload dist/*
```

> Never commit `~/.pypirc`, and never paste a token into an issue or a shell history you sync.
> If one leaks, revoke it at <https://pypi.org/manage/account/> — revoking is instant and
> generating a replacement costs nothing.

### 4. Build

```sh
python -m pip install --upgrade build twine
rm -rf dist/                          # stale artifacts get uploaded too; twine takes dist/*
python -m build                       # sdist + wheel
python -m twine check --strict dist/*  # the metadata and README rendering PyPI enforces
```

`build` runs in an isolated environment that installs only `setuptools`, so PyGObject is never
needed to package the project — the version is read from the source by AST, not by importing it.

Confirm the wheel carries the shell extension and the completions. Without the extension the
module cannot talk to GNOME at all, and a wheel missing it installs perfectly and then does
nothing:

```sh
python -c 'import zipfile,glob; print(*sorted(zipfile.ZipFile(glob.glob("dist/*.whl")[0]).namelist()), sep="\n")'
```

### 5. Rehearse on TestPyPI

```sh
python -m twine upload -r testpypi dist/*
```

Then install it somewhere disposable. `--extra-index-url` matters: TestPyPI has no copy of
PyGObject, so the dependency has to resolve from the real index.

```sh
python -m venv /tmp/gwc-test && /tmp/gwc-test/bin/pip install \
    --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    gnome-window-controller
/tmp/gwc-test/bin/gnome-window-controller --version
```

Check the rendered page at `https://test.pypi.org/project/gnome-window-controller/` — this is the
last chance to catch a README that looks wrong.

### 6. Upload

```sh
python -m twine upload dist/*
```

Verify from a clean environment:

```sh
python -m venv /tmp/gwc-real && /tmp/gwc-real/bin/pip install gnome-window-controller
/tmp/gwc-real/bin/gnome-window-controller --version
```

### 7. Narrow the token

Now that the project exists, replace the account-wide token with one scoped to it: create a new
token at <https://pypi.org/manage/account/token/> with **Scope: Project → gnome-window-controller**,
put it in `~/.pypirc`, and delete the account-wide one. A leak then costs one project rather than
every project on the account.

### Publishing from GitHub Actions instead

`.github/workflows/publish.yml` uploads on a published GitHub release using **trusted
publishing**: GitHub hands PyPI a short-lived OIDC token, PyPI exchanges it for an upload token
that expires in fifteen minutes. There is no secret to store, leak or rotate.

It has to be registered before the first run. Because the project does not exist on PyPI yet, use
a *pending* publisher at <https://pypi.org/manage/account/publishing/>:

| Field | Value |
| --- | --- |
| PyPI Project Name | `gnome-window-controller` |
| Owner | `eduardotlc` |
| Repository name | `gnome_window_controller` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

Then, in the repository, **Settings → Environments → New environment → `pypi`**. Adding a required
reviewer there means a release waits for your approval before anything is uploaded.

Releasing is then: bump `__version__`, commit, tag, and publish a GitHub release for that tag.

```sh
git tag -a v1.3.0 -m "v1.3.0" && git push origin v1.3.0
gh release create v1.3.0 --generate-notes
```

Once the publisher has run once it stops being "pending" and becomes a normal one.

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
