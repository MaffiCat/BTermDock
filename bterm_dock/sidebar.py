"""Панель SSH-профилей, редактор, поиск и порядок подключений."""

from ._gtk import Gdk, GLib, Gtk, Pango
from .auth import confirm_account_password
from .images import ImagePicker, load_profile_icon, profile_avatar
from .profile_backup import export_dialog, import_dialog
from .profile_drag import ReorderPreview
from .ssh import hidden_profile_name, ssh_address, validate_profile
from .widgets import button, icon_button, label

SIDEBAR_MIN_WIDTH = 56
SIDEBAR_LIST_WIDTH = 160
SIDEBAR_FULL_WIDTH = 200
PROFILE_DRAG_TYPE = "application/x-bterm-dock-profile"


class SSHSidebar(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.settings = window.settings
        self.compact = False
        self.narrow = False
        self.addresses_visible = False
        self.get_style_context().add_class("dock-side")
        self.content = self.build_content()
        self.pack_start(self.content, True, True, 0)

    def build_content(self):
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        top_row = Gtk.Box(spacing=4)
        eyebrow = label("ПОДКЛЮЧЕНИЯ", "eyebrow")
        eyebrow.set_ellipsize(Pango.EllipsizeMode.END)
        top_row.pack_start(eyebrow, True, True, 0)
        transfer = Gtk.MenuButton()
        transfer.set_popover(self.backup_popover(transfer))
        transfer.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        transfer.set_relief(Gtk.ReliefStyle.NONE)
        transfer.set_tooltip_text("Импорт и экспорт SSH-профилей")
        transfer.get_accessible().set_name("Импорт и экспорт SSH-профилей")
        top_row.pack_end(transfer, False, False, 0)
        heading.pack_start(top_row, False, False, 0)
        title = Gtk.Box(spacing=8)
        title.pack_start(label("SSH", "side-title"), True, True, 0)
        title.pack_end(icon_button("list-add-symbolic", "Добавить профиль",
                                   lambda _: self.edit_profile()), False, False, 0)
        self.address_button = icon_button("view-reveal-symbolic", "Показать SSH-адреса",
                                          self.toggle_addresses)
        title.pack_end(self.address_button, False, False, 0)
        heading.pack_start(title, False, False, 0)
        sidebar.pack_start(heading, False, False, 0)

        self.search = Gtk.SearchEntry(placeholder_text="Поиск по имени", width_chars=1)
        self.search.connect("search-changed", lambda _: self.refresh_profiles(self.selected()))
        self.search.connect("activate", lambda _: self.connect_profile())
        sidebar.pack_start(self.search, False, False, 0)

        self.profiles = Gtk.ListBox(activate_on_single_click=False)
        self.profiles.get_style_context().add_class("profile-list")
        self.profiles.connect("row-activated", lambda _, row: self.connect_profile(row.index))
        self.profile_drag_targets = [
            Gtk.TargetEntry.new(PROFILE_DRAG_TYPE, Gtk.TargetFlags.SAME_APP, 0)
        ]
        self.profile_scroll_timer = 0
        self.profiles.connect("destroy", lambda *_: self.end_profile_drag())
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=20)
        self.empty_title = label("", "profile-name", wrap=True)
        self.empty_hint = label("", "muted", wrap=True)
        empty.pack_start(self.empty_title, False, False, 0)
        empty.pack_start(self.empty_hint, False, False, 0)
        self.profiles.set_placeholder(empty)
        empty.show_all()
        self.profile_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.profile_scroll.add(self.profiles)
        viewport = Gtk.Overlay()
        viewport.add(self.profile_scroll)
        self.reorder_preview = ReorderPreview(self.profiles)
        viewport.add_overlay(self.reorder_preview)
        viewport.set_overlay_pass_through(self.reorder_preview, True)
        viewport.drag_dest_set(Gtk.DestDefaults(0), self.profile_drag_targets, Gdk.DragAction.MOVE)
        viewport.connect("drag-motion", self.drag_profile_over_list)
        viewport.connect("drag-leave", lambda *_: self.leave_profile_drag())
        viewport.connect("drag-drop", self.drop_profile)
        sidebar.pack_start(viewport, True, True, 0)

        actions = self.build_actions()
        sidebar.pack_end(actions, False, False, 0)
        self.details = (heading, self.search, actions, empty)
        self.profiles.connect("row-selected", self.profile_selected)
        return sidebar

    def build_actions(self):
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        actions.get_style_context().add_class("dock-actions")
        connect = button("Подключиться", lambda _: self.connect_profile(), "network-server-symbolic")
        connect.get_style_context().add_class("profile-connect")
        actions.pack_start(connect, False, False, 0)

        secondary = Gtk.Box(homogeneous=True, spacing=2)
        secondary.get_style_context().add_class("profile-action-group")
        buttons = [connect]
        for caption, tooltip, icon, callback in (
            ("Правка", "Изменить профиль", "document-edit-symbolic",
             lambda _: self.edit_profile(self.selected())),
            ("Адрес", "Копировать адрес", "edit-copy-symbolic", self.copy_address),
            ("Удалить", "Удалить профиль", "user-trash-symbolic", self.delete_profile),
        ):
            action = button(caption, callback, icon)
            action.set_image_position(Gtk.PositionType.TOP)
            action.set_tooltip_text(tooltip)
            action.get_accessible().set_name(tooltip)
            action.get_style_context().add_class("profile-action")
            secondary.pack_start(action, True, True, 0)
            buttons.append(action)
        buttons[-1].get_style_context().add_class("profile-delete")
        actions.pack_start(secondary, False, False, 0)
        self.profile_actions = tuple(buttons)
        self.profile_action_captions = tuple(action.get_label() for action in buttons[1:])
        return actions

    def backup_popover(self, anchor):
        popover = Gtk.Popover.new(anchor)
        popover.get_style_context().add_class("dock-menu")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, margin=9)
        heading = label("Перенос подключений", "setting-title")
        heading.set_margin_start(8)
        heading.set_margin_top(5)
        heading.set_margin_bottom(5)
        content.pack_start(heading, False, False, 0)

        def run_action(widget, action):
            popover.popdown()
            action(widget)

        for title, detail, icon, action in (
            ("Экспорт в JSON…", "Сохранить список в файл", "document-save-symbolic", self.export_profiles),
            ("Импорт из JSON…", "Добавить или восстановить список", "document-open-symbolic", self.import_profiles),
        ):
            item = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
            item.get_style_context().add_class("menu-action")
            item.get_accessible().set_name(title)
            row = Gtk.Box(spacing=10)
            row.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON), False, False, 0)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            text.pack_start(label(title, "setting-title"), False, False, 0)
            text.pack_start(label(detail, "muted"), False, False, 0)
            row.pack_start(text, True, True, 0)
            item.add(row)
            item.connect("clicked", run_action, action)
            content.pack_start(item, False, False, 0)
        popover.add(content)
        content.show_all()
        return popover

    def update_width(self, layout, _):
        width = layout.get_position()
        if width < SIDEBAR_MIN_WIDTH:
            layout.set_position(SIDEBAR_MIN_WIDTH)
            return
        compact = width < SIDEBAR_LIST_WIDTH
        narrow = width < SIDEBAR_FULL_WIDTH
        if (compact, narrow) == (self.compact, self.narrow):
            return
        style = self.get_style_context()
        if narrow != self.narrow:
            self.narrow = narrow
            if narrow:
                style.add_class("narrow")
            else:
                style.remove_class("narrow")
            for action, caption in zip(self.profile_actions[1:], self.profile_action_captions):
                action.set_label("" if narrow else caption)
        self.content.set_property("margin", 6 if compact else 8 if narrow else 16)
        if compact == self.compact:
            return
        self.compact = compact
        if compact:
            style.add_class("compact")
        else:
            style.remove_class("compact")
        for widget in self.details:
            widget.set_visible(not compact)
        self.profiles.set_activate_on_single_click(compact)
        self.refresh_profiles(self.selected())

    def profile_selected(self, _, row):
        for action in self.profile_actions:
            action.set_sensitive(row is not None)

    def selected(self):
        row = self.profiles.get_selected_row()
        return row.index if row else None

    def set_addresses_visible(self, visible):
        selected = self.selected()
        self.addresses_visible = visible
        if not visible:
            self.search.set_text("")
        icon = "view-conceal-symbolic" if visible else "view-reveal-symbolic"
        tooltip = "Скрыть SSH-адреса" if visible else "Показать SSH-адреса"
        self.address_button.get_image().set_from_icon_name(icon, Gtk.IconSize.BUTTON)
        self.address_button.set_tooltip_text(tooltip)
        self.address_button.get_accessible().set_name(tooltip)
        self.search.set_placeholder_text("Поиск по имени или адресу" if visible else "Поиск по имени")
        self.refresh_profiles(selected)

    def authorize_address_access(self):
        return self.addresses_visible or confirm_account_password(self.window)

    def toggle_addresses(self, _):
        if self.authorize_address_access():
            self.set_addresses_visible(not self.addresses_visible)

    def export_profiles(self, _):
        if self.authorize_address_access():
            export_dialog(self.window)

    def import_profiles(self, _):
        if import_dialog(self.window):
            self.search.set_text("")
            self.refresh_profiles()

    def refresh_profiles(self, selected=None):
        self.end_profile_drag()
        profiles = self.settings["profiles"]
        query = "" if self.compact else self.search.get_text().strip().casefold()
        for row in self.profiles.get_children():
            self.profiles.remove(row)
        self.empty_title.set_text("Нет совпадений" if query else "Здесь будут ваши серверы")
        search_hint = ("Попробуйте другое имя или адрес." if self.addresses_visible else
                       "Попробуйте другое имя.")
        self.empty_hint.set_text(search_hint if query else
                                 "Нажмите «+», чтобы сохранить первое SSH-подключение.")
        for index, profile in enumerate(profiles):
            name = profile["name"] if self.addresses_visible else hidden_profile_name(profile)
            address = ssh_address(profile) if self.addresses_visible else ""
            if query not in name.casefold() and query not in address.casefold():
                continue
            row = Gtk.ListBoxRow()
            row.index = index
            row.set_tooltip_text(f"{name}\n{address}" if address else name)
            row.get_accessible().set_name(name)
            contents = Gtk.Box(spacing=10)
            contents.pack_start(profile_avatar(profile, name), False, False, 0)
            if self.compact:
                contents.set_halign(Gtk.Align.CENTER)
            else:
                details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER)
                for line, style in ((name, "profile-name"), (address, "profile-address")):
                    if not line:
                        continue
                    item = label(line, style)
                    item.set_ellipsize(Pango.EllipsizeMode.END)
                    details.pack_start(item, False, False, 0)
                contents.pack_start(details, True, True, 0)
            drag_area = Gtk.EventBox(visible_window=False, above_child=True)
            drag_area.add(contents)
            drag_area.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, self.profile_drag_targets,
                                      Gdk.DragAction.MOVE)
            drag_area.connect("button-press-event", self.capture_drag_hotspot, row)
            drag_area.connect("drag-begin", self.begin_profile_drag, row)
            drag_area.connect("drag-end", lambda *_: self.end_profile_drag())
            row.add(drag_area)
            self.profiles.add(row)
            row.show_all()
            if index == selected:
                self.profiles.select_row(row)
        if self.selected() is None:
            self.profiles.select_row(self.profiles.get_row_at_index(0))
        self.profile_selected(self.profiles, self.profiles.get_selected_row())

    def profile_drop_target(self, y):
        rows = self.profiles.get_children()
        for row in rows:
            _, top = row.translate_coordinates(self.profiles, 0, 0)[-2:]
            if y < top + row.get_allocated_height() / 2:
                return row, False
        return rows[-1], True

    @staticmethod
    def capture_drag_hotspot(area, event, row):
        coordinates = area.translate_coordinates(row, int(event.x), int(event.y))
        row.drag_hotspot = coordinates[-2:] if coordinates else None
        return False

    def begin_profile_drag(self, _area, context, row):
        self.reorder_preview.begin(row, context, getattr(row, "drag_hotspot", None))

    def profile_drag_source(self, context):
        drag_area = Gtk.drag_get_source_widget(context)
        row = drag_area.get_ancestor(Gtk.ListBoxRow) if drag_area else None
        return row if row is not None and row.get_parent() is self.profiles else None

    def preview_profile_drop(self, y):
        row, after = self.profile_drop_target(y)
        self.reorder_preview.move_to(row.get_index() + int(after))

    def drag_profile_over_list(self, viewport, context, x, y, timestamp):
        if self.profile_drag_source(context) is None:
            return False
        _, list_y = viewport.translate_coordinates(self.profiles, x, y)[-2:]
        self.preview_profile_drop(list_y)
        adjustment = self.profile_scroll.get_vadjustment()
        self.profile_drag_y = list_y - adjustment.get_value()
        if not self.profile_scroll_timer and adjustment.get_upper() > adjustment.get_page_size():
            self.profile_scroll_timer = GLib.timeout_add(40, self.scroll_profile_drag)
        Gdk.drag_status(context, Gdk.DragAction.MOVE, timestamp)
        return True

    def scroll_profile_drag(self):
        adjustment = self.profile_scroll.get_vadjustment()
        if self.profile_drag_y < 24:
            step = -12
        elif self.profile_drag_y > adjustment.get_page_size() - 24:
            step = 12
        else:
            return GLib.SOURCE_CONTINUE
        previous = adjustment.get_value()
        adjustment.set_value(previous + step)
        if adjustment.get_value() != previous:
            self.preview_profile_drop(self.profile_drag_y + adjustment.get_value())
            self.reorder_preview.queue_draw()
        return GLib.SOURCE_CONTINUE

    def stop_profile_scroll(self):
        if self.profile_scroll_timer:
            GLib.source_remove(self.profile_scroll_timer)
            self.profile_scroll_timer = 0

    def leave_profile_drag(self):
        self.stop_profile_scroll()
        self.reorder_preview.reset_slot()

    def end_profile_drag(self):
        self.stop_profile_scroll()
        self.reorder_preview.end()

    def drop_profile(self, viewport, context, x, y, timestamp):
        self.end_profile_drag()
        source = self.profile_drag_source(context)
        if source is None:
            return False
        _, list_y = viewport.translate_coordinates(self.profiles, x, y)[-2:]
        target, after = self.profile_drop_target(list_y)
        destination = target.index + int(after)
        if source.index < destination:
            destination -= 1
        if source.index == destination:
            Gtk.drag_finish(context, True, False, timestamp)
            return True
        profiles = self.settings["profiles"].copy()
        profiles.insert(destination, profiles.pop(source.index))
        saved = self.window.save(profiles=profiles)
        Gtk.drag_finish(context, saved, False, timestamp)
        if saved:
            self.refresh_profiles(destination)
        return True

    def edit_profile(self, index=None):
        if index is not None and not self.authorize_address_access():
            return
        profiles = self.settings["profiles"].copy()
        dialog = Gtk.Dialog(title="SSH-профиль", transient_for=self.window, modal=True)
        dialog.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Сохранить", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=12, row_spacing=10, margin=16)
        dialog.get_content_area().add(grid)
        fields = {}
        specifications = (
            ("name", "Имя", "Например, Тестовый сервер"),
            ("host", "IP или хост", "192.0.2.10 или алиас из ~/.ssh/config"),
            ("user", "Логин", "По умолчанию OpenSSH"),
            ("port", "Порт", "По умолчанию OpenSSH"),
            ("key", "Файл ключа", "~/.ssh/id_ed25519"),
        )
        for row, (field, title, placeholder) in enumerate(specifications):
            entry = Gtk.Entry(width_chars=36, activates_default=True, placeholder_text=placeholder)
            if index is not None:
                entry.set_text(profiles[index][field])
            caption = label(title)
            caption.set_mnemonic_widget(entry)
            grid.attach(caption, 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
            fields[field] = entry
        previous_icon = profiles[index].get("icon", "") if index is not None else ""
        icon_picker = ImagePicker(dialog, "Выберите значок профиля")
        if previous_icon:
            icon_picker.set_filename(previous_icon)
        icon_controls = Gtk.Box(spacing=8)
        icon_controls.pack_start(icon_picker, True, True, 0)
        icon_controls.pack_end(button("Убрать", lambda _: icon_picker.unselect_all()),
                               False, False, 0)
        grid.attach(label("Значок"), 0, len(fields), 1, 1)
        grid.attach(icon_controls, 1, len(fields), 1, 1)
        error_label = label("", wrap=True)
        error_label.get_style_context().add_class("error")
        grid.attach(error_label, 0, len(fields) + 1, 2, 1)
        dialog.show_all()
        fields["name"].grab_focus()
        while dialog.run() == Gtk.ResponseType.OK:
            profile = {field: entry.get_text().strip() for field, entry in fields.items()}
            try:
                validate_profile(profile)
                if icon := icon_picker.get_filename():
                    if icon != previous_icon:
                        load_profile_icon(icon)
                    profile["icon"] = icon
            except (GLib.Error, ValueError) as error:
                error_label.set_text(str(error))
                continue
            if index is None:
                index = len(profiles)
                profiles.append(profile)
            else:
                profiles[index] = profile
            if self.window.save(profiles=profiles):
                self.search.set_text("")
                self.refresh_profiles(index)
                break
        dialog.destroy()

    def delete_profile(self, _):
        index = self.selected()
        if index is None:
            return
        profile = self.settings["profiles"][index]
        name = profile["name"] if self.addresses_visible else hidden_profile_name(profile)
        if not confirm_account_password(self.window, title="Удаление SSH-профиля", action="Удалить",
                                        subject=name, detail="Профиль будет удалён из списка SSH.",
                                        destructive=True):
            return
        profiles = self.settings["profiles"].copy()
        for index, current in enumerate(profiles):
            if current is profile:
                profiles.pop(index)
                if self.window.save(profiles=profiles):
                    self.refresh_profiles()
                break

    def copy_address(self, _):
        index = self.selected()
        if index is not None and self.authorize_address_access():
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(ssh_address(self.settings["profiles"][index]), -1)

    def connect_profile(self, index=None):
        index = self.selected() if index is None else index
        if index is None:
            return
        self.window.workspace.open_ssh(self.settings["profiles"][index])
