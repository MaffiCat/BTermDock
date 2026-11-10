"""Главное окно, компоновка виджетов и горячие клавиши."""

from math import pi

import cairo

from . import APP_NAME, settings
from ._gtk import Gdk, GLib, Gtk
from .background import edit_background
from .images import load_image
from .preferences import edit_preferences
from .sidebar import SSHSidebar
from .terminal import TerminalWorkspace
from .widgets import button


def draw_sidebar_icon(icon, context):
    toggle = icon.get_parent()
    color = toggle.get_style_context().get_color(toggle.get_state_flags())
    Gdk.cairo_set_source_rgba(context, color)
    context.translate((icon.get_allocated_width() - 18) / 2, (icon.get_allocated_height() - 18) / 2)
    context.set_line_width(1.25)
    for x, y, corner in ((13, 6, -1), (13, 12, 0), (5, 12, 1), (5, 6, 2)):
        context.arc(x, y, 2.5, corner * pi / 2, (corner + 1) * pi / 2)
    context.close_path()
    context.stroke()
    context.set_line_cap(cairo.LINE_CAP_ROUND)
    if toggle.get_active():
        context.move_to(7, 4)
        context.line_to(7, 14)
    else:
        context.move_to(6, 6.5)
        context.line_to(6, 11.5)
    context.stroke()
    return False


class DockWindow(Gtk.ApplicationWindow):
    def __init__(self, application, preferences, config_error, session=None):
        super().__init__(application=application, title=APP_NAME)
        self.settings = preferences
        self.config_error = config_error
        self.set_default_size(1120, 700)
        self.get_style_context().add_class("bterm-dock")

        self.header = Gtk.HeaderBar(title=APP_NAME, show_close_button=True)
        self.header.get_style_context().add_class("dock-header")
        self.set_titlebar(self.header)
        toggle = Gtk.ToggleButton(active=True, focus_on_click=False)
        toggle.get_style_context().add_class("sidebar-toggle")
        icon = Gtk.DrawingArea(width_request=18, height_request=18)
        icon.connect("draw", draw_sidebar_icon)
        toggle.add(icon)
        self.header.pack_start(toggle)
        self.header.pack_end(button("Настройки", lambda _: edit_preferences(self), "preferences-system-symbolic"))
        self.header.pack_end(button("Фон", lambda _: edit_background(self), "image-x-generic-symbolic"))

        layout = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        layout.get_style_context().add_class("dock-split")
        self.add(layout)
        self.sidebar = SSHSidebar(self)
        layout.pack1(self.sidebar, resize=False, shrink=True)
        toggle.connect("toggled", self.toggle_sidebar)
        self.toggle_sidebar(toggle)
        self.workspace = TerminalWorkspace(self)
        layout.pack2(self.workspace, resize=True, shrink=False)
        layout.set_position(278)
        layout.connect("notify::position", self.sidebar.update_width)

        shortcuts = Gtk.AccelGroup()
        self.add_accel_group(shortcuts)
        for keys, action in (("<Primary>t", self.workspace.open_shell),
                             ("<Primary>w", self.workspace.close_current_tab),
                             ("<Primary><Shift>c", self.workspace.copy_terminal),
                             ("<Primary><Shift>v", self.workspace.paste_terminal)):
            keyval, modifiers = Gtk.accelerator_parse(keys)
            shortcuts.connect(keyval, modifiers, Gtk.AccelFlags.VISIBLE,
                              self.accelerator(action))
        self.connect("key-press-event", self.workspace.cycle_tabs)
        self.connect("delete-event", application.remember_closing_window)
        self.sidebar.refresh_profiles()
        self.show_all()
        self.workspace.restore_session(session)
        if config_error:
            self.show_error("Ошибка чтения настроек", config_error)
        else:
            path = self.settings["background"]
            try:
                image = load_image(path) if path else None
            except (GLib.Error, ValueError) as error:
                self.workspace.set_wallpaper(None)
                self.show_error("Фон не загрузился", str(error))
            else:
                self.workspace.set_wallpaper(image)

    def toggle_sidebar(self, toggle):
        visible = toggle.get_active()
        self.sidebar.set_visible(visible)
        tooltip = "Убрать боковую панель" if visible else "Показать боковую панель"
        toggle.set_tooltip_text(tooltip)
        toggle.get_accessible().set_name(tooltip)
        toggle.get_child().queue_draw()

    @staticmethod
    def accelerator(action):
        def invoke(*_):
            action()
            return True
        return invoke

    def show_error(self, title, detail):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                   message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.CLOSE, text=title)
        dialog.format_secondary_text(detail)
        dialog.run()
        dialog.destroy()

    def save(self, **changes):
        if self.config_error:
            self.show_error("Настройки защищены от перезаписи",
                            f"Исправьте файл {settings.CONFIG} и перезапустите терминал.")
            return False
        updated = self.settings | changes
        try:
            settings.write_settings(updated)
        except OSError as error:
            self.show_error("Настройки не сохранились", str(error))
            return False
        self.settings.update(changes)
        if "profiles" in changes:
            for window in self.get_application().get_windows():
                if window is not self:
                    window.sidebar.refresh_profiles()
        return True
