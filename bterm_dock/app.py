"""Жизненный цикл приложения и обработка команд запуска."""

from contextlib import suppress
import logging
from pathlib import Path
import sys

from . import APP_NAME, settings
from ._gtk import Gdk, Gio, GLib, Gtk
from .appearance import is_light, stylesheet, theme_palette
from .session import read_session, write_session
from .window import DockWindow
from .widgets import color


class Dock(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="local.BTermDock",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.session_ready = False
        self.session_save_source = 0
        self.connect("notify::active-window", self.queue_session_save)
        self.connect("window-removed", self.queue_session_save)
        self.add_main_option("new-window", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Открыть новое окно терминала")
        self.add_main_option("new-tab", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Открыть вкладку в последнем активном окне терминала")

    def do_startup(self):
        Gtk.Application.do_startup(self)
        gtk_settings = Gtk.Settings.get_default()
        gtk_settings.set_property("gtk-theme-name", "Adwaita")
        self.style_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self.style_provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.update_appearance(settings.default_settings())

    def update_appearance(self, preferences):
        self.theme = preferences["theme"]
        palette = theme_palette(preferences, self.theme)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme",
                                                not is_light(color(palette["base"])))
        self.style_provider.load_from_data(stylesheet(preferences, palette))

    def do_activate(self):
        if window := self.get_active_window():
            window.present()
        else:
            self.open_window(restore=True)

    def do_command_line(self, command_line):
        options = command_line.get_options_dict()
        if options.contains("new-window"):
            self.open_window()
        elif options.contains("new-tab") and (window := self.get_active_window()):
            window.workspace.open_shell()
            window.present()
        else:
            self.activate()
        return 0

    def open_window(self, restore=False):
        if window := self.get_active_window():
            DockWindow(self, window.settings, window.config_error).present()
            return
        try:
            preferences, error = settings.read_settings(), None
        except (OSError, ValueError) as failure:
            preferences = settings.default_settings()
            error = f"{settings.CONFIG}\n{failure}"
        self.update_appearance(preferences)
        saved = read_session() if restore and not self.session_ready else []
        for state in reversed(saved or [None]):
            DockWindow(self, preferences, error, state).present()
        self.session_ready = True
        self.queue_session_save()

    def queue_session_save(self, *_):
        if self.session_ready and not self.session_save_source:
            self.session_save_source = GLib.idle_add(self.flush_session)

    def flush_session(self):
        self.session_save_source = 0
        self.save_session(self.get_windows())
        return GLib.SOURCE_REMOVE

    def save_session(self, windows):
        if not self.session_ready or not windows:
            return
        states = [window.workspace.session_state() for window in windows]
        try:
            write_session([state for state in states if state["tabs"]])
        except (OSError, UnicodeError) as error:
            logging.getLogger(__name__).warning("Не удалось сохранить вкладки: %s", error)

    def remember_closing_window(self, closing, _event):
        other_windows = [window for window in self.get_windows() if window is not closing]
        self.save_session(other_windows or [closing])
        return False

    def do_shutdown(self):
        if self.session_save_source:
            GLib.source_remove(self.session_save_source)
            self.session_save_source = 0
        self.save_session(self.get_windows())
        Gtk.Application.do_shutdown(self)


def main():
    GLib.set_application_name(APP_NAME)
    # Fastfetch определяет терминал по имени процесса, запустившего оболочку.
    with suppress(OSError):
        Path("/proc/self/comm").write_text(APP_NAME, encoding="utf-8")
    return Dock().run(sys.argv)
