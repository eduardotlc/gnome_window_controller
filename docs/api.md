# Python API

```python
from gnome_window_controller import GnomeWindowController, HighlightOptions

ctl = GnomeWindowController(exclude_apps=("Picture-in-Picture", "Slack"))

ctl.get_focused()                     # dict | None
ctl.details(win_id)                   # dict, cached per call cycle
ctl.monitor_order()                   # e.g. (1, 0, 2) — Mutter indices, left to right
ctl.monitor_layout()                  # rects, scales, connectors

ctl.cycle_monitors("up")
ctl.focus_named_window("kitty")
ctl.cycle_monitors(direction=1)
ctl.focus_same_monitor_window()
ctl.focus_same_name_window()

ctl.highlight.configure(HighlightOptions(color="#fabd2f", width=4))


ctl.focus_last_window()

ctl.highlight.configure(HighlightOptions(color="#fabd2f", width=4))
ctl.highlight.configure(HighlightOptions.from_mode("commands"))
ctl.highlight.flash()                     # respects the mode
ctl.highlight.show_focused()              # forces, even when the mode is off
ctl.highlight.state()      # {'mode', 'border_visible', 'tracked_window_id', ...}
```

Importing the package pulls in no PyGObject: `gi` is lazy loaded on the first call that actually
needs D-Bus, which is why `--help` and `--version` are instant, also allowing docs pages build
on a machine with no GNOME.

Monitor order is read from `org.gnome.Mutter.DisplayConfig` and sorted by physical position, so
`--chfocus right`/`left` follow your actual layout instead of Mutter's arbitrary indices. It falls
back to the `MONITOR_ORDER` constant if the layout cannot be read.

## Controller

```{eval-rst}
.. automodule:: gnome_window_controller.gnome_window_controller
   :members:
   :show-inheritance:
```

## Focused-window highlight

```{eval-rst}
.. automodule:: gnome_window_controller.highlight
   :members:
   :show-inheritance:
```

## Command line

```{eval-rst}
.. automodule:: gnome_window_controller.cli
   :members:
```

## Colored output

```{eval-rst}
.. automodule:: gnome_window_controller.colors
   :members:
```

## Exceptions

```{eval-rst}
.. automodule:: gnome_window_controller.errors
   :members:
   :show-inheritance:
```
