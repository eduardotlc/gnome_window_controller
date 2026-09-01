# CLI reference

Every flag `gnome-window-controller` accepts, generated from the argument parser itself, so it
cannot drift from the program.

The same interface is reachable as `python -m gnome_window_controller`.

```{eval-rst}
.. argparse::
   :module: gnome_window_controller.cli
   :func: build_parser
   :prog: gnome-window-controller
```
