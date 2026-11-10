"""Вкладки, сеансы VTE и параметры оформления терминала."""

import os
from pathlib import Path
import pwd
import re
import shutil

from ._gtk import Gdk, Gio, GLib, Gtk, Pango, PangoCairo, Vte
from .auth import confirm_account_password
from .background import Wallpaper
from .session import ssh_profile_key
from .ssh import hidden_profile_name, ssh_command
from .tabs import TabStrip
from .widgets import button, color, label

LOCAL_TAB_TITLE = "Терминал"
HAS_TERMPROPS = hasattr(Vte, "TERMPROP_XTERM_TITLE")


def local_shell():
    try:
        account_shell = pwd.getpwuid(os.getuid()).pw_shell
    except KeyError:
        account_shell = ""
    for candidate in (os.environ.get("SHELL"), account_shell, "/bin/sh"):
        if candidate and (executable := shutil.which(candidate)):
            if Path(executable).name not in ("nologin", "false", "true"):
                return executable
    return None


def gnome_settings(schema_id, path=None):
    schemas = Gio.SettingsSchemaSource.get_default()
    schema = schemas.lookup(schema_id, True) if schemas else None
    return Gio.Settings.new_full(schema, None, path) if schema else None


def default_terminal_profile():
    profiles = gnome_settings("org.gnome.Terminal.ProfilesList")
    profile_id = profiles.get_string("default") if profiles else ""
    if re.fullmatch(r"[a-fA-F0-9-]+", profile_id):
        path = f"/org/gnome/terminal/legacy/profiles:/:{profile_id}/"
        return gnome_settings("org.gnome.Terminal.Legacy.Profile", path)
    return None


def default_font(profile):
    if profile:
        if profile.get_boolean("use-system-font"):
            desktop = gnome_settings("org.gnome.desktop.interface")
            if desktop and (font := desktop.get_string("monospace-font-name")):
                return font
        elif font := profile.get_string("font"):
            return font
    families = PangoCairo.FontMap.get_default().list_families()
    if any(family.get_name() == "MesloLGLDZ Nerd Font" for family in families):
        return "MesloLGLDZ Nerd Font 12"
    return "Monospace 12"


def terminal_colors(profile):
    foreground, background = color("#fff"), color("#000")
    if profile and not profile.get_boolean("use-theme-colors"):
        foreground = color(profile.get_string("foreground-color")) or foreground
        background = color(profile.get_string("background-color")) or background
    return foreground, background


def apply_terminal_profile(terminal, profile, foreground, background):
    palette = []
    if profile:
        palette = [color(value) for value in profile.get_strv("palette")]
        if len(palette) not in (8, 16, 232, 256) or any(value is None for value in palette):
            palette = []
        terminal.set_cell_width_scale(profile.get_double("cell-width-scale"))
        terminal.set_cell_height_scale(profile.get_double("cell-height-scale"))
        terminal.set_bold_is_bright(profile.get_boolean("bold-is-bright"))
        blink = profile.get_string("cursor-blink-mode").upper()
        terminal.set_cursor_blink_mode(getattr(Vte.CursorBlinkMode, blink,
                                              Vte.CursorBlinkMode.SYSTEM))
    terminal.set_colors(foreground, background, palette)
    if profile and not profile.get_boolean("use-theme-colors"):
        if not profile.get_boolean("bold-color-same-as-fg"):
            terminal.set_color_bold(color(profile.get_string("bold-color")))
        if profile.get_boolean("cursor-colors-set"):
            terminal.set_color_cursor(color(profile.get_string("cursor-background-color")))
            terminal.set_color_cursor_foreground(color(profile.get_string("cursor-foreground-color")))
        if profile.get_boolean("highlight-colors-set"):
            terminal.set_color_highlight(color(profile.get_string("highlight-background-color")))
            terminal.set_color_highlight_foreground(color(profile.get_string("highlight-foreground-color")))


class TerminalWorkspace(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.settings = window.settings
        self.set_size_request(230, -1)
        self.terminal_profile = default_terminal_profile()
        self.foreground, self.background = terminal_colors(self.terminal_profile)

        overlay = Gtk.Overlay()
        self.wallpaper = Wallpaper(self.background)
        overlay.add(self.wallpaper)
        self.tabs = Gtk.Notebook(show_tabs=False, show_border=False)
        self.tabs.get_style_context().add_class("dock-tabs")
        self.tab_strip = TabStrip(self.tabs, self.open_shell, self.close_tab)
        self.pack_start(self.tab_strip, False, False, 0)
        self.pack_start(overlay, True, True, 0)
        self.tabs.connect_after("switch-page", self.switch_tab)
        for signal in ("page-added", "page-removed", "page-reordered"):
            self.tabs.connect_after(signal, self.number_local_tabs)
            self.tabs.connect_after(signal, self.queue_session_save)
        overlay.add_overlay(self.tabs)
        self.status_bar = Gtk.Box(spacing=12)
        self.status_bar.get_style_context().add_class("dock-status")
        self.status_bar.set_no_show_all(True)
        self.status = label("")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_bar.pack_start(self.status, True, True, 0)
        self.status.show()
        self.reconnect_button = button("Подключиться", self.reconnect_current_tab, "network-server-symbolic")
        self.reconnect_button.set_no_show_all(True)
        self.status_bar.pack_end(self.reconnect_button, False, False, 0)
        self.pack_end(self.status_bar, False, False, 0)

    def set_wallpaper(self, animation, appearance=None):
        self.wallpaper.set_image(animation, self.settings if appearance is None else appearance)
        for page in self.tabs.get_children():
            page.terminal.set_clear_background(not self.wallpaper.has_custom_background)

    def configure_terminal(self, terminal):
        font = self.settings["terminal_font"] or default_font(self.terminal_profile)
        terminal.set_font(Pango.FontDescription.from_string(font))
        terminal.set_scrollback_lines(self.settings["scrollback_lines"])
        shape = self.settings["cursor_shape"]
        if shape == "system":
            shape = self.terminal_profile.get_string("cursor-shape") if self.terminal_profile else "ibeam"
        terminal.set_cursor_shape(getattr(Vte.CursorShape, shape.upper(), Vte.CursorShape.IBEAM))

    def open_shell(self, working_directory=""):
        executable = local_shell()
        if not executable:
            self.window.show_error("Оболочка не найдена", "Проверьте $SHELL и наличие /bin/sh.")
            return None
        cwd = str(Path.home())
        if (os.path.isabs(working_directory) and os.path.isdir(working_directory)
                and os.access(working_directory, os.X_OK)):
            cwd = working_directory
        local_title = f"{GLib.get_user_name()}@{GLib.get_host_name()}:~"
        return self.open_terminal([executable], None, "", env=[f"SHELL={executable}"],
                                  window_title=local_title, cwd=cwd,
                                  session={"kind": "local", "cwd": cwd})

    def open_ssh(self, profile):
        argv = self.ssh_arguments(profile)
        if argv is None:
            return
        title = hidden_profile_name(profile)
        return self.open_terminal(argv, title, f"SSH  ·  {title}",
                                  session={"kind": "ssh", "profile": ssh_profile_key(profile), "title": title})

    def ssh_arguments(self, profile):
        try:
            return ssh_command(profile)
        except ValueError as error:
            self.window.show_error("Не удалось запустить SSH", str(error))
            return None

    def open_terminal(self, argv, title, description, env=None, window_title=None, cwd=None, session=None):
        page = self.create_terminal(title, description, session, window_title)
        self.spawn_terminal(page, argv, env, cwd)
        return page

    def create_terminal(self, title, description, session, window_title=None, *, awaiting_reconnect=False):
        numbered = title is None
        title = LOCAL_TAB_TITLE if numbered else title
        # Перенос старых строк при смене ширины сбивает перерисовку приглашения zsh.
        terminal = Vte.Terminal(rewrap_on_resize=False)
        apply_terminal_profile(terminal, self.terminal_profile, self.foreground, self.background)
        self.configure_terminal(terminal)
        terminal.set_audible_bell(False)
        terminal.set_mouse_autohide(True)
        terminal.set_clear_background(not self.wallpaper.has_custom_background)
        terminal.set_margin_start(8)
        terminal.set_margin_end(4)
        terminal.set_margin_top(6)
        terminal.set_margin_bottom(4)

        page = Gtk.Box()
        page.terminal = terminal
        page.title = title
        page.window_title = window_title or title
        page.description = description
        page.session = session
        page.pid = None
        page.awaiting_reconnect = awaiting_reconnect
        page.cancel = Gio.Cancellable()
        page.connect("destroy", lambda _: page.cancel.cancel())
        page.pack_start(terminal, True, True, 0)
        page.scrollbar = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL,
                                       adjustment=terminal.get_vadjustment())
        terminal.set_no_show_all(awaiting_reconnect)
        page.scrollbar.set_no_show_all(awaiting_reconnect)
        page.scrollbar.get_style_context().add_class("dock-scroll")
        page.pack_end(page.scrollbar, False, False, 0)
        caption = self.tab_strip.add_tab(page, title, numbered)
        page.numbered_caption = caption if numbered else None
        index = self.tabs.append_page(page, None)
        page.show_all()
        self.tabs.set_current_page(index)
        (self.reconnect_button if awaiting_reconnect else terminal).grab_focus()
        title_signal = (f"termprop-changed::{Vte.TERMPROP_XTERM_TITLE}"
                        if HAS_TERMPROPS else "notify::window-title")
        terminal.connect(title_signal, self.title_changed, page)
        if session and session["kind"] == "local":
            directory_signal = (f"termprop-changed::{Vte.TERMPROP_CURRENT_DIRECTORY_URI}"
                                if HAS_TERMPROPS else "notify::current-directory-uri")
            terminal.connect(directory_signal, self.directory_changed, page)
        return page

    def spawn_terminal(self, page, argv, env=None, cwd=None):
        description = page.description or page.title

        def finished(_, status):
            page.pid = None
            code = os.waitstatus_to_exitcode(status)
            page.description = f"{description}  ·  Завершено ({code})"
            if self.current_page() is page:
                self.update_status(page)

        def started(_, pid, error, *_data):
            if pid > 0:
                page.pid = pid
            if error is not None and not page.cancel.is_cancelled():
                page.description = f"{description}  ·  Ошибка запуска"
                if self.current_page() is page:
                    self.update_status(page)
                self.window.show_error("Не удалось открыть сеанс", error.message)

        page.terminal.connect("child-exited", finished)
        page.terminal.spawn_async(Vte.PtyFlags.DEFAULT, cwd or str(Path.home()), argv, env,
                                  GLib.SpawnFlags.DEFAULT, None, None, -1, page.cancel, started, None)

    def restore_session(self, state):
        if state:
            restored = []
            for tab in state["tabs"]:
                page = self.open_shell(tab["cwd"]) if tab["kind"] == "local" else self.restore_ssh(tab)
                restored.append(page)
            if selected := restored[state["selected"]]:
                self.tabs.set_current_page(self.tabs.page_num(selected))
        if not self.tabs.get_n_pages():
            self.open_shell()

    def find_ssh_profile(self, session):
        matches = [profile for profile in self.settings["profiles"]
                   if ssh_profile_key(profile) == session["profile"]]
        return next((profile for profile in matches if hidden_profile_name(profile) == session["title"]),
                    matches[0] if matches else None)

    def restore_ssh(self, session):
        profile = self.find_ssh_profile(session)
        title = hidden_profile_name(profile) if profile else session["title"]
        return self.create_terminal(title, "SSH  ·  Вкладка восстановлена", dict(session, title=title),
                                    awaiting_reconnect=True)

    def reconnect_current_tab(self, _button):
        page = self.current_page()
        if page is None or not page.awaiting_reconnect:
            return
        if not confirm_account_password(self.window, title="Восстановление SSH-подключения",
                                        action="Подключиться", subject=page.title):
            return
        if self.tabs.page_num(page) < 0 or not page.awaiting_reconnect:
            return
        profile = self.find_ssh_profile(page.session)
        if profile is None:
            self.window.show_error("SSH-профиль не найден", "Откройте подключение из панели SSH.")
            return
        argv = self.ssh_arguments(profile)
        if argv is None:
            return
        page.awaiting_reconnect = False
        for widget in (page.terminal, page.scrollbar):
            widget.set_no_show_all(False)
            widget.show()
        page.description = f"SSH  ·  {hidden_profile_name(profile)}"
        if self.current_page() is page:
            self.update_status(page)
            page.terminal.grab_focus()
        self.spawn_terminal(page, argv)

    def directory_changed(self, terminal, _property, page):
        if HAS_TERMPROPS:
            uri = terminal.ref_termprop_uri(Vte.TERMPROP_CURRENT_DIRECTORY_URI)
            value = uri.to_string() if uri else None
        else:
            value = terminal.get_current_directory_uri()
        if not value:
            return
        try:
            directory, host = GLib.filename_from_uri(value)
        except GLib.Error:
            return
        if host in (None, "", "localhost", GLib.get_host_name()):
            page.session["cwd"] = directory
            self.queue_session_save()

    def queue_session_save(self, *_):
        application = self.window.get_application()
        if application is not None:
            application.queue_session_save()

    def session_state(self):
        records = []
        selected = 0
        current = self.current_page()
        for page in self.tabs.get_children():
            if page.session is None:
                continue
            if page is current:
                selected = len(records)
            if page.session["kind"] == "local" and page.pid:
                try:
                    directory = os.readlink(f"/proc/{page.pid}/cwd")
                    directory.encode("utf-8")
                except (OSError, UnicodeError):
                    pass
                else:
                    if os.path.isdir(directory):
                        page.session["cwd"] = directory
            records.append(page.session.copy())
        return {"tabs": records, "selected": selected}

    @staticmethod
    def number_local_tabs(tabs, _page, _index):
        local_tabs = (page for page in tabs.get_children() if page.numbered_caption is not None)
        for number, page in enumerate(local_tabs, 1):
            title = f"{LOCAL_TAB_TITLE} {number}"
            page.numbered_caption.set_text(title)
            page.tab.set_tooltip_text(title)

    def switch_tab(self, _, page, _index):
        self.title_changed(page.terminal, None, page)
        self.update_status(page)
        (self.reconnect_button if page.awaiting_reconnect else page.terminal).grab_focus()
        self.queue_session_save()

    def title_changed(self, terminal, _, page):
        if self.current_page() is page:
            if HAS_TERMPROPS:
                title, _ = terminal.get_termprop_string(Vte.TERMPROP_XTERM_TITLE)
            else:
                title = terminal.get_window_title()
            self.window.header.set_subtitle(title or page.window_title)

    def update_status(self, page):
        self.status.set_text(page.description)
        self.reconnect_button.set_visible(page.awaiting_reconnect)
        self.status_bar.set_visible(bool(page.description) or page.awaiting_reconnect)

    def cycle_tabs(self, _, event):
        ctrl = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK
        modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
        if (event.keyval not in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab)
                or modifiers not in (ctrl, ctrl | shift)):
            return False
        count = self.tabs.get_n_pages()
        if count > 1:
            direction = -1 if modifiers & shift else 1
            self.tabs.set_current_page((self.tabs.get_current_page() + direction) % count)
        return True

    def current_page(self):
        return self.tabs.get_nth_page(self.tabs.get_current_page())

    def close_tab(self, page):
        page.destroy()
        if not self.tabs.get_n_pages():
            self.window.close()

    def close_current_tab(self):
        if page := self.current_page():
            self.close_tab(page)

    def copy_terminal(self):
        if page := self.current_page():
            page.terminal.copy_clipboard_format(Vte.Format.TEXT)

    def paste_terminal(self):
        if page := self.current_page():
            page.terminal.paste_clipboard()
