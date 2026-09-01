# Gnome Window Controller

[![GNOME: 50.4](.assets/GNOME.svg)](https://gitlab.gnome.org/GNOME/gnome-control-center/-/releases/50.4)_[![v3.14.7](.assets/python.svg)](https://www.python.org/downloads/release/python-3147/)_[![Email-Me](.assets/email.svg)](mailto:eduardotcampos@hotmail.com)_[![License](.assets/license.svg)](https://opensource.org/license/mit)_[![Tests: passing](.assets/build.svg)](https://github.com/eduardotlc/gnome_window_controller/actions/workflows/tests.yml)

**Control GNOME window focus.**

**Focus changing between monitors, windows, and apps, includes multiple options**

This extension uses Python, and intends to be used by mapping shortcuts or terminal commands.

Designed to use with **Wayland** window system protocol and **GNOME** desktop environment.

This extension adds terminal commands and extra features to existing extensions, but is designed to
use a python backend, therefore requiring the python part install. If your usage is directional
focus changing (left/right/up/down), there is the standalone gnome extension
[Focus Control](https://github.com/itsfernn/FocusControl), give it a look.

1. [Features](#features)

2. [Requirements](#requirements)

3. [Install](#install)

4. [Shortcuts](#shortcuts)

4.1 [Example Keybindings](#example-keybindings)

5. [Usage](#usage)

5.1 [Queries](#queries)

5.2 [Focus](#focus)

5.3 [Focus Highlight](#focus-highlight)

5.4 [Colored Output](#colored-output)

6. [Extras](#extras)

6.1 [Shell Completions](#shell-completions)

7. [Acknowledgments](#Acknowledgments)

---

## Features

- Terminal commands support for every command/feature

- Directional focus changing between monitors (left/right/up/down)

- Highlight border uppon focusing, allowing configuration (this extension focus|any|disabled)

- Given app name focusing

- Same monitor app focus changing (current workspace or include all)

- Same app focus changing

- Excluding given app name from focus changing

- Flash highlight border on currently focused window

## Requirements

- GNOME Shell 45+ (Tested on 50.4, Wayland)

- `gnome-window-controller@eduardotcampos.hotmail.com` extension (see [Install](#install))

- `glib-compile-schemas` (glib2-devel)

- Python 3.12+ (`requires-python = ">=3.12"`).

> [!NOTE]
> `PyGObject` is also a dependency, but is normally supplied by the distribution, case not,
> the better option is to install it from distro packages (dnf, apt, etc.) rather than letting
> pip to compile it.

---

## Install

**Python Package**

```bash
python -m pip install gnome-window-controller
```

or from a checkout:

```bash
git clone https://github.com/eduardotlc/gnome_window_controller
cd gnome_window_controller
python -m pip install .
```

**Highlight Extension**

```sh
gnome-window-controller --highlight install
```

**Log out and back in afterwards.** GNOME Shell only scans for extensions at startup, and
Wayland cannot restart the shell in place.

run/map the command either by

```sh
python -m gnome_window_controller [command] [flags]
```

or

```sh
gnome-window-controller [command] [flags]
```

> [!NOTE]
> The Python package and the GNOME Shell extension are installed separately — the extension
> has to live under `~/.local/share/gnome-shell/extensions/`

> [!NOTE]
> python pip install gets the `gnome-window-controller` command in `~/.local/bin`:

---

## Shortcuts

Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts.

Then map commands like:

Focus right:

```
/home/eduardotc/.local/bin/gnome-window-controller --chfocus right
```

Focus left:

```
/home/eduardotc/.local/bin/gnome-window-controller --chfocus left
```

> [!IMPORTANT]
> gnome-shell's `PATH` does **not** include `~/.local/bin`, so give the
> **absolute path** (substitute `/home/eduardotc` above with **your user home**)

Back up and restore all custom shortcuts with:

```sh
dconf dump /org/gnome/settings-daemon/plugins/media-keys/ > keybindings.conf
dconf load /org/gnome/settings-daemon/plugins/media-keys/ < keybindings.conf
```

### Example Keybindings

| Keys                      | Command                                                        |
| ------------------------- | -------------------------------------------------------------- |
| `Super + L` / `Super + H` | `--chfocus right` / `--chfocus left`                           |
| `Super + J` / `Super + K` | `--chfocus win same_monitor` / `--chfocus win same_monitor_up` |
| `Super + R`               | `--chfocus last`                                               |
| `Super + C`               | `--chfocus monitor top`                                        |
| `Super + X`               | `--chfocus win same_app`                                       |
| `Super + I`               | `--show-focus` — outline the focused window                    |

---

## Usage

Running with no arguments or `-h`/`--help` prints the full help

```sh
gnome-window-controller
```

### Queries

```sh
gnome-window-controller --list-windows            # listing
gnome-window-controller --list-windows --json     # machine readable
gnome-window-controller --details-windows         # full Details(id) per window
gnome-window-controller --list-monitors           # rectangles, order and neighbors
gnome-window-controller --list-monitors --json    # machine readable
```

### Focus

**--focus**

```
--focus <name>
--focus <name> --scope [other-monitor|current-monitor|any]
```

```sh
gnome-window-controller --focus kitty
gnome-window-controller --focus kitty --scope any
```

`--scope` picks where `--focus` searches: `other-monitor` (default), `current-monitor`, or `any`.

**--exclude**

```
--exclude <name> [<name> ...]
```

```sh
gnome-window-controller --chfocus win --exclude floorp
gnome-window-controller --chfocus win --exclude floorp --exclude Slack
```

Windows whose `wm_class`, instance or title contains a given name are never focused — the focus
command steps over them and takes the next candidate instead. Matching is a case-insensitive
substring, the same rule `--focus` uses, so `--exclude floorp` covers every Floorp window and
`--exclude Picture-in-Picture` covers one window of an app without touching its siblings.

It applies to `--focus` and to every `--chfocus` command. Listings are untouched: `--list-windows`
still shows excluded windows, and so does the "which monitor am I on" lookup, which is what keeps
things correct while an excluded app happens to hold focus.

`Picture-in-Picture` is excluded out of the box — a floating video overlay is virtually never what
a focus shortcut is reaching for — and `--exclude` adds to that list rather than replacing it. To
start from nothing, use the Python API:

```python
GnomeWindowController(exclude_apps=())                  # nothing excluded
GnomeWindowController(exclude_apps=("floorp", "Slack"))  # exactly these
```

**--workspace**

```
--workspace [current|prefer-current|any]
```

```sh
gnome-window-controller --chfocus win same_monitor --workspace current
gnome-window-controller --chfocus right --workspace prefer-current
```

How focus commands treat windows sitting on another workspace:

| Scope | Effect |
| --- | --- |
| `current` | only windows on the workspace in view; if there are none, nothing happens |
| `prefer-current` | the same, falling back to every workspace when this one holds nothing focusable |
| `any` | no weighting; workspace is ignored |

Left out, each command keeps the default it has always had: `--chfocus win same_monitor` stays on
the current workspace (that is `prefer-current`), everything else ignores workspaces. Passing
`--workspace any` is what turns that preference off.

The scope narrows the candidate pool without reordering it, so it composes with whatever a
command means by "the next window" — the ring `--chfocus win` walks, and the topmost window
`--chfocus right` lands on, both keep their meaning.

**--chfocus**

```
--chfocus <command> [option]
```

| command          | option                              | effect                                                  |
| ---------------- | ----------------------------------- | ------------------------------------------------------- |
| `monitor`        | `top` (default) / `bottom`          | focus the top- or bottom-most window of another monitor |
| `win`            | `same_app`                          | next window of the focused app                          |
| `win`            | `same_monitor`, `same_monitor_down` | next window on this monitor                             |
| `win`            | `same_monitor_up` (or none)         | previous window on this monitor                         |
| `right` / `left` | —                                   | move focus one monitor right/left, wrapping             |
| `up` / `down`    | —                                   | move focus one monitor up/down, wrapping                |
| `last`           | —                                   | focus the previously focused window                     |

Everyone of them honours `--exclude`.

**Monitor layouts**

The four directions follow the actual monitor rectangles read from
`org.gnome.Mutter.DisplayConfig`, so any arrangement works — a row, a vertical stack, an L, a
grid, mixed resolutions, mixed scales, a screen rotated onto its side. Nothing is hard-coded to a
particular desk, and Mutter's monitor indices are used as they come: they follow no geometric
order of their own.

A direction with no monitor that way leaves focus alone and exits `1`, rather than jumping
somewhere arbitrary — on a single row, `up` means nothing. At the end of a row or column the
search wraps, which is what makes repeated `--chfocus right` cycle. Where a direction has no
aligned monitor but does have an off-axis one, it goes there instead, so no screen on an L-shaped
desk is unreachable.

`--list-monitors` prints exactly what the directions will follow:

```
left-to-right order: [2, 1, 0]
[2] HDMI-1 1920x1080 @ 0,0     scale 1.0
[1] DP-1   2560x1440 @ 1920,0  scale 1.0 (primary) <
[0] DP-2   1920x1080 @ 4480,0  scale 1.0

neighbors (what --chfocus <direction> follows, wrapping at the edges):
   from    left   right      up    down
      2       0       1       -       -
      1       2       0       -       -
      0       1       2       -       -
```

---

### Focus Highlight

```sh
gnome-window-controller --highlight           # status + current options
gnome-window-controller --highlight on
gnome-window-controller --highlight off
gnome-window-controller --highlight flash     # pulse the focused window now
gnome-window-controller --show-focus          # outline the focused window now
gnome-window-controller --highlight clear     # remove the border
gnome-window-controller --highlight uninstall
```

**Options**

```sh
gnome-window-controller --highlight on \
    --highlight-color '#fb4934' \
    --highlight-width 4 \
    --highlight-radius 8 \
    --highlight-duration 900
```

| Flag                   | Default   | Meaning                                               |
| ---------------------- | --------- | ----------------------------------------------------- |
| `--highlight-color`    | `#993c5a` | any CSS color St accepts (`#rgb`, `red`, `rgba(...)`) |
| `--highlight-width`    | `3`       | border thickness, px                                  |
| `--highlight-radius`   | `12`      | corner radius, px                                     |
| `--highlight-inset`    | `2`       | how far outside the frame the border sits, px         |
| `--highlight-duration` | `0`       | ms to stay visible; `0` keeps it until focus moves    |
| `--highlight-mode`     | `always`  | `always`, `commands` or `off` (see above)             |

> [!NOTE]
> Any `--highlight-*` flag applies on its own — no `--highlight ACTION` needed.

Settings persist to `~/.config/gnome-window-controller/highlight.json` and are reloaded when the
shell restarts.

Every window focused by this CLI is also flashed automatically. Pass `--no-highlight` to suppress
that for a single invocation. When the extension is not installed the flash is skipped silently
(~0.3 ms), so nothing else breaks.

### Colored Output

Colors printing can be turned off with:

```sh
gnome-window-controller --list-windows --color never    # plain text
gnome-window-controller --list-windows --color always   # color even into a pipe
gnome-window-controller --list-windows                  # auto: color only a real terminal
GWC_COLORS=False gnome-window-controller --list-windows # same, from the environment
```

---

## Extras

### Shell Completions

Completion files for both shells ship inside the package, under
`src/gnome_window_controller/completions/` in a checkout, and beside the installed package
otherwise.

**Bash**

```sh
mkdir -p ~/.local/share/bash-completion/completions
cp src/gnome_window_controller/completions/gnome-window-controller.bash \
   ~/.local/share/bash-completion/completions/gnome-window-controller
```

The target file has to be named after the command — that is how `bash-completion` finds and
loads it on first use.

**zsh**

```sh
mkdir -p ~/.local/share/zsh/site-functions
cp src/gnome_window_controller/completions/_gnome-window-controller \
   ~/.local/share/zsh/site-functions/
```

and, in `~/.zshrc` **before** `compinit` runs:

```zsh
fpath=(~/.local/share/zsh/site-functions $fpath)
```

Both complete every flag, and the values each flag takes: `--chfocus` offers `monitor`, `win`,
`right`, `left`, `up`, `down` and `last`, then narrows the second word to whatever the first one
allows.
`--focus` and `--exclude` complete against the `wm_class` of the windows **currently open**.

> [!NOTE]
> Installed with pip instead of from a clone? The files are next to the installed package:
>
> ```sh
> python -c 'import gnome_window_controller as m, pathlib as p;\
> print(p.Path(m.__file__).parent / "completions")'
> ```

---

## Acknowledgments

The gnome extensions from which this one wouldn't exist, huge thanks to:

- [focus-window](https://github.com/pcbowers/focus-window)

- [Window Calls](https://github.com/ickyicky/window-calls)

## TODO

- [ ] Upload gnome-extension

- [ ] Create documentation page
