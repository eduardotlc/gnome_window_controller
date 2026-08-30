/*
 * Gnome Window Controller.
 *
 * Serves the whole shell-side surface the `gnome_window_controller` Python
 * module needs, over a single D-Bus interface:
 *
 *   - listing, describing and activating windows, and reporting which one has
 *     focus (List, Details, GetTitle, Activate, GetFocused), which used to
 *     require the Window Calls and Focused Window D-Bus extensions;
 *   - a colored border around the focused window, drawn and reconfigured live
 *     (Highlight, FlashFocused, ShowFocused, Clear, Get/SetOptions, GetState).
 *
 * @author eduardotc
 * @email eduardotcampos@hotmail.com
 */

import GLib from "gi://GLib";
import Gio from "gi://Gio";
import Meta from "gi://Meta";
import St from "gi://St";

import { Extension } from "resource:///org/gnome/shell/extensions/extension.js";

const BUS_PATH = "/org/gnome/Shell/Extensions/GnomeWindowController";

const IFACE_XML = `
<node>
  <interface name="org.gnome.Shell.Extensions.GnomeWindowController">
    <method name="Ping">
      <arg type="s" direction="out" name="version"/>
    </method>
    <method name="GetOptions">
      <arg type="s" direction="out" name="options"/>
    </method>
    <method name="SetOptions">
      <arg type="s" direction="in" name="options"/>
      <arg type="s" direction="out" name="effective"/>
    </method>
    <method name="Highlight">
      <arg type="u" direction="in" name="winid"/>
      <arg type="i" direction="in" name="duration_ms"/>
    </method>
    <method name="FlashFocused">
      <arg type="i" direction="in" name="duration_ms"/>
    </method>
    <method name="Clear">
    </method>
    <method name="ShowFocused">
      <arg type="i" direction="in" name="duration_ms"/>
      <arg type="b" direction="in" name="force"/>
      <arg type="b" direction="out" name="shown"/>
    </method>
    <method name="GetState">
      <arg type="s" direction="out" name="state"/>
    </method>
    <method name="List">
      <arg type="s" direction="out" name="windows"/>
    </method>
    <method name="Details">
      <arg type="u" direction="in" name="winid"/>
      <arg type="s" direction="out" name="details"/>
    </method>
    <method name="GetTitle">
      <arg type="u" direction="in" name="winid"/>
      <arg type="s" direction="out" name="title"/>
    </method>
    <method name="GetFocused">
      <arg type="s" direction="out" name="window"/>
    </method>
    <method name="Activate">
      <arg type="u" direction="in" name="winid"/>
    </method>
  </interface>
</node>`;

/** Option defaults; every key is overridable through SetOptions / the config file. */
const DEFAULTS = {
  enabled: true,
  color: "#993c5a",
  width: 3,
  radius: 12,
  inset: 2,
  duration_ms: 0,
  fade_ms: 220,
  follow_focus: true,
  skip_maximized: false,
  skip_fullscreen: true,
  only_normal: true,
};

/** Option key -> GSettings key. GSettings spells keys with dashes. */
const SETTINGS_KEYS = {
  enabled: "enabled",
  color: "color",
  width: "width",
  radius: "radius",
  inset: "inset",
  duration_ms: "duration-ms",
  fade_ms: "fade-ms",
  follow_focus: "follow-focus",
  skip_maximized: "skip-maximized",
  skip_fullscreen: "skip-fullscreen",
  only_normal: "only-normal",
};

/** Pre-GSettings config file, imported once and then left alone. */
const CONFIG_RELPATH = ["gnome-window-controller", "highlight.json"];

export default class GnomeWindowControllerHighlight extends Extension {
  enable() {
    this._opts = Object.assign({}, DEFAULTS);
    this._border = null;
    this._trackedWindow = null;
    this._winHandles = [];
    this._displayHandles = [];
    this._wmHandles = [];
    this._hideTimeout = 0;
    this._grabbing = false;
    this._forced = false;

    this._settings = this.getSettings();
    this._migrateLegacyConfig();
    this._opts = this._readSettings();

    // Preferences run in their own process, so the only thing that reaches us is a GSettings
    // change. Everything -- prefs dialog, D-Bus SetOptions, `gsettings set` -- lands here.
    this._settingsHandle = this._settings.connect("changed", () => {
      this._opts = this._readSettings();
      this._removeBorder();
      this._refresh();
    });

    this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this);
    this._dbus.export(Gio.DBus.session, BUS_PATH);

    this._displayHandles.push(
      global.display.connect("notify::focus-window", () => this._refresh()),
      global.display.connect("grab-op-begin", () => {
        this._grabbing = true;
        this._removeBorder();
      }),
      global.display.connect("grab-op-end", () => {
        this._grabbing = false;
        this._refresh();
      }),
      global.display.connect("restacked", () => this._raiseBorder()),
    );

    this._wmHandles.push(
      global.window_manager.connect("size-changed", () => this._reposition()),
      global.window_manager.connect("minimize", () => this._removeBorder()),
      global.window_manager.connect("unminimize", () => this._refresh()),
      global.window_manager.connect("switch-workspace", () => this._refresh()),
    );

    this._refresh();
  }

  disable() {
    if (this._dbus) {
      this._dbus.unexport();
      this._dbus = null;
    }
    this._displayHandles.splice(0).forEach((h) => global.display.disconnect(h));
    this._wmHandles
      .splice(0)
      .forEach((h) => global.window_manager.disconnect(h));
    if (this._settingsHandle) {
      this._settings.disconnect(this._settingsHandle);
      this._settingsHandle = 0;
    }
    this._settings = null;
    this._untrackWindow();
    this._removeBorder();
    this._opts = null;
  }

  // ----------------------------- D-Bus surface -----------------------------

  Ping() {
    return String(this.metadata.version ?? 1);
  }

  GetOptions() {
    return JSON.stringify(this._opts);
  }

  SetOptions(json) {
    let incoming = {};
    try {
      incoming = JSON.parse(json) ?? {};
    } catch {
      incoming = {};
    }
    // Write through to GSettings so the prefs dialog sees CLI changes and vice versa. The
    // "changed" handler installed in enable() re-reads and redraws.
    for (const [key, skey] of Object.entries(SETTINGS_KEYS)) {
      if (!Object.hasOwn(incoming, key) || incoming[key] === null) continue;
      const value = incoming[key];
      switch (typeof DEFAULTS[key]) {
        case "boolean":
          this._settings.set_boolean(skey, Boolean(value));
          break;
        case "number":
          this._settings.set_int(skey, Number(value) | 0);
          break;
        default:
          this._settings.set_string(skey, String(value));
      }
    }
    this._opts = this._readSettings();
    return JSON.stringify(this._opts);
  }

  Highlight(winid, durationMs) {
    const win = this._windowById(winid);
    if (win) this._drawFor(win, durationMs);
  }

  FlashFocused(durationMs) {
    const win = global.display.focus_window;
    if (win) this._drawFor(win, durationMs);
  }

  /**
   * Outline whatever currently has focus, on demand.
   *
   * With `force`, the automatic rules do not apply: the border is drawn even when the master
   * switch is off or the window is one the skip-* options would normally ignore. Pressing a
   * "where is my focus" shortcut is an explicit request, not the follow-focus behaviour.
   *
   * @param {number} durationMs milliseconds to keep it; <0 uses the configured duration
   * @param {boolean} force ignore `enabled` and the eligibility filters
   * @returns {boolean} whether a border was drawn
   */
  ShowFocused(durationMs, force) {
    const win = global.display.focus_window;
    if (!win) return false;
    this._drawFor(win, durationMs, force);
    return this._border !== null;
  }

  Clear() {
    this._untrackWindow();
    this._removeBorder();
  }

  GetState() {
    const focused = global.display.focus_window;
    return JSON.stringify({
      mode: !this._opts?.enabled
        ? "off"
        : this._opts.follow_focus
          ? "always"
          : "commands",
      border_visible: this._border !== null,
      tracked_window_id: this._trackedWindow?.get_id() ?? null,
      focused_window_id: focused?.get_id() ?? null,
    });
  }

  // --------------------------- Window query surface ---------------------------
  //
  // These methods replace the two third-party extensions the Python module used to require:
  // Window Calls (org.gnome.Shell.Extensions.Windows) for List/Details/GetTitle/Activate, and
  // Focused Window D-Bus (org.gnome.shell.extensions.FocusedWindow) for GetFocused. Records
  // travel as JSON strings for the same reason those two used them: D-Bus has no comfortable way
  // to return a heterogeneous record, and the caller already parses JSON.

  /**
   * List every window the compositor manages, in stacking order.
   *
   * @returns {string} JSON array of window summaries
   */
  List() {
    return JSON.stringify(this._managedWindows().map((win) => this._summarize(win)));
  }

  /**
   * Describe a single window, including the fields List() leaves out.
   *
   * @param {number} winid window id
   * @returns {string} JSON object
   */
  Details(winid) {
    return JSON.stringify(this._detail(this._requireWindow(winid)));
  }

  /**
   * @param {number} winid window id
   * @returns {string} the window title, or "" when it has none
   */
  GetTitle(winid) {
    return this._requireWindow(winid).get_title() ?? "";
  }

  /**
   * Describe the window that currently owns focus.
   *
   * Same record shape as one List() entry, so callers need no second code path. An empty string
   * means nothing is focused, which is a normal state rather than an error.
   *
   * @returns {string} JSON object, or "" when no window has focus
   */
  GetFocused() {
    const win = global.display.focus_window;
    return win ? JSON.stringify(this._summarize(win)) : "";
  }

  /**
   * Focus a window, switching to its workspace when it lives on another one.
   *
   * @param {number} winid window id
   */
  Activate(winid) {
    const win = this._requireWindow(winid);
    const time = global.get_current_time();
    const workspace = win.get_workspace();
    if (workspace) workspace.activate_with_focus(win, time);
    else win.activate(time);
  }

  // ------------------------------- Internals -------------------------------

  _configFile() {
    return Gio.File.new_for_path(
      GLib.build_filenamev([GLib.get_user_config_dir(), ...CONFIG_RELPATH]),
    );
  }

  _readSettings() {
    const opts = {};
    for (const [key, skey] of Object.entries(SETTINGS_KEYS)) {
      switch (typeof DEFAULTS[key]) {
        case "boolean":
          opts[key] = this._settings.get_boolean(skey);
          break;
        case "number":
          opts[key] = this._settings.get_int(skey);
          break;
        default:
          opts[key] = this._settings.get_string(skey);
      }
    }
    return opts;
  }

  /** Import the pre-GSettings highlight.json exactly once, so settings survive the upgrade. */
  _migrateLegacyConfig() {
    if (this._settings.get_boolean("config-migrated")) return;
    try {
      const file = Gio.File.new_for_path(
        GLib.build_filenamev([GLib.get_user_config_dir(), ...CONFIG_RELPATH]),
      );
      const [ok, bytes] = file.load_contents(null);
      if (ok) {
        const stored = JSON.parse(new TextDecoder().decode(bytes));
        this.SetOptions(JSON.stringify(stored));
      }
    } catch {
      // No legacy config, or unreadable: schema defaults stand.
    }
    this._settings.set_boolean("config-migrated", true);
  }

  _windowById(winid) {
    return (
      global.display
        .list_all_windows()
        .find((w) => w.get_id() === winid) ?? null
    );
  }

  /**
   * Like _windowById, but for the D-Bus surface, where a bad id must be an error.
   *
   * @param {number} winid window id
   * @returns {Meta.Window} the matching window
   */
  _requireWindow(winid) {
    const win = this._windowById(winid);
    if (!win) throw new Error(`no window with id ${winid}`);
    return win;
  }

  /**
   * Windows that currently have an actor, i.e. the ones a user can see and act on.
   *
   * global.display.list_all_windows() also reports windows without an actor, which nothing
   * downstream can focus or measure, so listing goes through the actors instead.
   *
   * @returns {Meta.Window[]} managed windows, bottom to top
   */
  _managedWindows() {
    return global.get_window_actors().map((actor) => actor.meta_window);
  }

  /**
   * Flatten a rectangle into a plain object.
   *
   * Boxed structs carry no enumerable properties, so JSON.stringify turns them into `{}` unless
   * their fields are copied out by hand.
   *
   * @param {Mtk.Rectangle|null} rect rectangle to copy
   * @returns {object|null} `{x, y, width, height}`, or null when there is no rectangle
   */
  _rect(rect) {
    if (!rect) return null;
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
  }

  /**
   * Describe a window with the fields List() reports for every entry.
   *
   * @param {Meta.Window} win window to describe
   * @returns {object} JSON-ready summary
   */
  _summarize(win) {
    const frame = win.get_frame_rect();
    const workspace = win.get_workspace();
    return {
      in_current_workspace:
        win.located_on_workspace?.(
          global.workspace_manager.get_active_workspace(),
        ) ?? false,
      workspace: workspace ? workspace.index() : -1,
      monitor: win.get_monitor(),
      wm_class: win.get_wm_class() ?? "",
      wm_class_instance: win.get_wm_class_instance() ?? "",
      title: win.get_title() ?? "",
      pid: win.get_pid(),
      id: win.get_id(),
      frame_type: win.get_frame_type(),
      window_type: win.get_window_type(),
      focus: win.has_focus(),
      minimized: win.minimized,
      x: frame.x,
      y: frame.y,
      width: frame.width,
      height: frame.height,
    };
  }

  /**
   * Describe a window in full, for Details().
   *
   * @param {Meta.Window} win window to describe
   * @returns {object} JSON-ready record
   */
  _detail(win) {
    const monitor = win.get_monitor();
    return Object.assign(this._summarize(win), {
      role: win.get_role() ?? null,
      layer: win.get_layer(),
      maximized: win.get_maximized?.() ?? 0,
      maximized_horizontally: win.maximized_horizontally,
      maximized_vertically: win.maximized_vertically,
      fullscreen: win.is_fullscreen(),
      moveable: win.allows_move(),
      resizeable: win.allows_resize(),
      canclose: win.can_close(),
      canmaximize: win.can_maximize(),
      canminimize: win.can_minimize(),
      canshade: win.can_shade?.() ?? false,
      area: this._rect(win.get_work_area_current_monitor()),
      area_all: this._rect(win.get_work_area_all_monitors()),
      area_cust: this._rect(win.get_work_area_for_monitor?.(monitor)),
    });
  }

  _eligible(win, force = false) {
    if (!win) return false;
    if (win.minimized) return false;
    if (force) return true;
    if (this._opts.only_normal && win.window_type !== Meta.WindowType.NORMAL)
      return false;
    if (this._opts.skip_fullscreen && win.is_fullscreen()) return false;
    if (
      this._opts.skip_maximized &&
      win.maximized_horizontally &&
      win.maximized_vertically
    )
      return false;
    return !win.minimized;
  }

  _clearTimeout() {
    if (this._hideTimeout) {
      GLib.Source.remove(this._hideTimeout);
      this._hideTimeout = 0;
    }
  }

  _untrackWindow() {
    if (this._trackedWindow) {
      this._winHandles
        .splice(0)
        .forEach((h) => this._trackedWindow.disconnect(h));
      this._trackedWindow = null;
    }
    this._winHandles.length = 0;
  }

  _trackWindow(win) {
    if (this._trackedWindow === win) return;
    this._untrackWindow();
    this._trackedWindow = win;
    this._winHandles.push(
      win.connect("position-changed", () => this._reposition()),
      win.connect("size-changed", () => this._reposition()),
      win.connect("unmanaged", () => {
        this._untrackWindow();
        this._removeBorder();
      }),
    );
  }

  _removeBorder() {
    this._clearTimeout();
    this._forced = false;
    if (this._border) {
      this._border.destroy();
      this._border = null;
    }
  }

  _raiseBorder() {
    if (this._border?.get_parent())
      global.window_group.set_child_above_sibling(this._border, null);
  }

  /** Redraw the border for whatever currently owns focus. */
  _refresh() {
    if (!this._opts?.enabled || this._grabbing) {
      this._removeBorder();
      return;
    }

    const win = global.display.focus_window;

    // Command-driven mode (follow_focus = false): never draw for a focus change we did not
    // cause. The border a Highlight()/FlashFocused() call just drew is kept, because the
    // focus notification for that same activation can land *after* the D-Bus call that drew
    // it -- removing it unconditionally here would erase it instantly. It goes away as soon
    // as focus moves to any other window.
    if (!this._opts.follow_focus) {
      if (win !== this._trackedWindow) {
        this._untrackWindow();
        this._removeBorder();
      }
      return;
    }

    if (!this._eligible(win)) {
      this._untrackWindow();
      this._removeBorder();
      return;
    }
    this._drawFor(win, -1);
  }

  /** Move the existing border to the tracked window's current frame rect. */
  _reposition() {
    if (!this._border || !this._trackedWindow) return;
    if (!this._eligible(this._trackedWindow, this._forced)) {
      this._removeBorder();
      return;
    }
    const rect = this._trackedWindow.get_frame_rect();
    const inset = Number(this._opts.inset) || 0;
    this._border.set_position(rect.x - inset, rect.y - inset);
    this._border.set_size(rect.width + inset * 2, rect.height + inset * 2);
  }

  /**
   * Draw the border around `win`.
   *
   * @param {Meta.Window} win window to outline
   * @param {number} durationMs milliseconds to keep it; <0 uses the configured
   *   duration, 0 keeps it until focus moves away
   */
  _drawFor(win, durationMs, force = false) {
    if (!force && !this._opts?.enabled) return;
    if (!this._eligible(win, force)) return;

    this._clearTimeout();
    this._trackWindow(win);
    // Remembered so _reposition() does not drop a forced border on a window the skip-*
    // filters would normally reject.
    this._forced = Boolean(force);

    const width = Math.max(0, Number(this._opts.width) || 0);
    const radius = Math.max(0, Number(this._opts.radius) || 0);
    const color = String(this._opts.color || DEFAULTS.color);
    const style = `border: ${width}px solid ${color}; border-radius: ${radius}px;`;

    if (!this._border) {
      this._border = new St.Widget({
        style,
        reactive: false,
        can_focus: false,
        track_hover: false,
      });
      global.window_group.add_child(this._border);
    } else {
      this._border.set_style(style);
      this._border.remove_all_transitions();
      this._border.opacity = 255;
    }

    this._reposition();
    this._raiseBorder();
    this._border.show();

    const hold =
      durationMs < 0 ? Number(this._opts.duration_ms) || 0 : durationMs;
    if (hold <= 0) return;

    this._hideTimeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, hold, () => {
      this._hideTimeout = 0;
      const fade = Math.max(0, Number(this._opts.fade_ms) || 0);
      if (this._border && fade > 0) {
        this._border.ease({
          opacity: 0,
          duration: fade,
          onComplete: () => this._removeBorder(),
        });
      } else {
        this._removeBorder();
      }
      return GLib.SOURCE_REMOVE;
    });
  }
}
