/*
 * Preferences for Gnome Window Controller Highlight.
 *
 * Runs in its own process, so GSettings is the only channel back to the shell: every row here
 * writes a key that extension.js watches with a "changed" handler.
 *
 * @author eduardotc
 * @email eduardotcampos@hotmail.com
 */

import Adw from "gi://Adw";
import Gdk from "gi://Gdk";
import Gtk from "gi://Gtk";

import { ExtensionPreferences } from "resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js";

/** Combo order must match the mode each index maps to. */
const MODES = ["always", "commands", "off"];

const MODE_SUBTITLE =
  "Always: every focus change, mouse clicks included. " +
  "On commands only: just the focus changes gnome_window_controller makes. " +
  "Never: no border at all.";

export default class GnomeWindowControllerPrefs extends ExtensionPreferences {
  fillPreferencesWindow(window) {
    const settings = this.getSettings();

    const page = new Adw.PreferencesPage({
      title: "Highlight",
      icon_name: "focus-windows-symbolic",
    });
    window.add(page);

    // ------------------------------ behaviour ------------------------------
    const behaviour = new Adw.PreferencesGroup({
      title: "Behaviour",
      description: "When the border around the focused window is drawn.",
    });
    page.add(behaviour);

    const mode = new Adw.ComboRow({
      title: "Highlight mode",
      subtitle: MODE_SUBTITLE,
      model: Gtk.StringList.new(["Always", "On commands only", "Never"]),
    });
    behaviour.add(mode);

    // "enabled" and "follow-focus" are two booleans; the combo is the friendly face of both.
    const readMode = () => {
      if (!settings.get_boolean("enabled")) return 2;
      return settings.get_boolean("follow-focus") ? 0 : 1;
    };
    let syncing = false;
    const syncModeFromSettings = () => {
      syncing = true;
      mode.set_selected(readMode());
      syncing = false;
    };
    syncModeFromSettings();

    mode.connect("notify::selected", () => {
      if (syncing) return;
      const picked = MODES[mode.get_selected()] ?? "always";
      if (picked === "off") {
        // Leave follow-focus alone so switching back restores the previous preference.
        settings.set_boolean("enabled", false);
      } else {
        settings.set_boolean("enabled", true);
        settings.set_boolean("follow-focus", picked === "always");
      }
    });
    // Keep the combo honest when the CLI changes the same keys while this window is open.
    const enabledHandle = settings.connect("changed::enabled", syncModeFromSettings);
    const followHandle = settings.connect("changed::follow-focus", syncModeFromSettings);
    window.connect("close-request", () => {
      settings.disconnect(enabledHandle);
      settings.disconnect(followHandle);
      return false;
    });

    // ------------------------------ appearance ------------------------------
    const appearance = new Adw.PreferencesGroup({ title: "Appearance" });
    page.add(appearance);

    appearance.add(this._colorRow(settings));
    appearance.add(this._spinRow(settings, "width", "Thickness", "Border width in pixels.", 0, 32));
    appearance.add(
      this._spinRow(settings, "radius", "Corner radius", "Rounding of the border, in pixels.", 0, 64),
    );
    appearance.add(
      this._spinRow(
        settings, "inset", "Outward offset",
        "How far outside the window frame the border sits, in pixels.", 0, 32,
      ),
    );

    // ------------------------------ timing ------------------------------
    const timing = new Adw.PreferencesGroup({ title: "Timing" });
    page.add(timing);

    timing.add(
      this._spinRow(
        settings, "duration-ms", "Visible for",
        "Milliseconds the border stays up. Zero keeps it until focus moves away.",
        0, 60000, 50,
      ),
    );
    timing.add(
      this._spinRow(
        settings, "fade-ms", "Fade out over",
        "Length of the fade once the visible duration expires, in milliseconds.",
        0, 5000, 10,
      ),
    );

    // ------------------------------ which windows ------------------------------
    const windows = new Adw.PreferencesGroup({
      title: "Which windows",
      description: "Windows excluded from highlighting.",
    });
    page.add(windows);

    windows.add(
      this._switchRow(settings, "only-normal", "Only normal windows",
        "Skip dialogs, popups and other non-toplevel windows."),
    );
    windows.add(
      this._switchRow(settings, "skip-fullscreen", "Skip fullscreen windows",
        "Do not draw a border around fullscreen windows."),
    );
    windows.add(
      this._switchRow(settings, "skip-maximized", "Skip maximized windows",
        "Do not draw a border around fully maximized windows."),
    );
  }

  /** Colour picker bound to the "color" string key, converted through Gdk.RGBA. */
  _colorRow(settings) {
    const row = new Adw.ActionRow({
      title: "Border color",
      subtitle: "Color drawn around the focused window.",
    });

    const rgba = new Gdk.RGBA();
    if (!rgba.parse(settings.get_string("color"))) rgba.parse("#993c5a");

    const button = new Gtk.ColorDialogButton({
      dialog: new Gtk.ColorDialog({ with_alpha: true }),
      rgba,
      valign: Gtk.Align.CENTER,
    });

    button.connect("notify::rgba", () => {
      settings.set_string("color", this._toCss(button.get_rgba()));
    });
    settings.connect("changed::color", () => {
      const next = new Gdk.RGBA();
      if (next.parse(settings.get_string("color")) && !next.equal(button.get_rgba()))
        button.set_rgba(next);
    });

    row.add_suffix(button);
    row.set_activatable_widget(button);
    return row;
  }

  /**
   * Render an RGBA as #rrggbb, or rgba(...) when it is translucent.
   *
   * Gdk.RGBA.to_string() emits rgb()/rgba() with 0-255 components, which St parses, but hex
   * keeps the stored value readable and matches what --highlight-color takes.
   */
  _toCss(rgba) {
    const byte = (v) => Math.round(Math.min(1, Math.max(0, v)) * 255);
    if (rgba.alpha >= 1)
      return `#${[rgba.red, rgba.green, rgba.blue]
        .map((c) => byte(c).toString(16).padStart(2, "0"))
        .join("")}`;
    const alpha = Math.round(rgba.alpha * 100) / 100;
    return `rgba(${byte(rgba.red)},${byte(rgba.green)},${byte(rgba.blue)},${alpha})`;
  }

  _spinRow(settings, key, title, subtitle, lower, upper, step = 1) {
    const row = new Adw.SpinRow({
      title,
      subtitle,
      adjustment: new Gtk.Adjustment({
        lower,
        upper,
        step_increment: step,
        page_increment: step * 10,
        value: settings.get_int(key),
      }),
    });
    settings.bind(key, row, "value", 0 /* Gio.SettingsBindFlags.DEFAULT */);
    return row;
  }

  _switchRow(settings, key, title, subtitle) {
    const row = new Adw.SwitchRow({ title, subtitle, active: settings.get_boolean(key) });
    settings.bind(key, row, "active", 0 /* Gio.SettingsBindFlags.DEFAULT */);
    return row;
  }
}
