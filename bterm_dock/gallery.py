"""Локальный выбор изображения с миниатюрами и крупным предпросмотром."""

from collections import OrderedDict
import os
from pathlib import Path
from queue import SimpleQueue
from threading import Thread

from ._gtk import Gdk, GdkPixbuf, GLib, Gtk, Pango
from .widgets import button, icon_button, label

IMAGE_FORMATS = ("png", "gif", "jpeg")
IMAGE_SUFFIXES = (".png", ".gif", ".jpg", ".jpeg")
IMAGE_PREVIEW_SIZE = 350
THUMBNAIL_SIZE = (102, 86)
THUMBNAIL_CACHE_SIZE = 96
THUMBNAIL_BATCH_SIZE = 4


class ImageGallery(Gtk.Dialog):
    def __init__(self, parent, title, filename, last_folder=None):
        super().__init__(title=title, transient_for=parent, modal=True,
                         destroy_with_parent=True, use_header_bar=True)
        self.get_style_context().add_class("dock-dialog")
        self.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Выбрать", Gtk.ResponseType.ACCEPT)
        self.accept = self.get_widget_for_response(Gtk.ResponseType.ACCEPT)
        self.accept.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.ACCEPT)
        self.set_default_size(1070, 640)
        self.filename = None
        self.folder = None
        self.history = []
        self.history_index = -1
        self.query = ""
        self.items = []
        self.jobs = SimpleQueue()
        self.worker = None
        self.busy = False
        self.closed = False
        self.thumbnail_cache = OrderedDict()
        self.thumbnail_timer = 0
        self.folder_version = object()
        self.preview_version = object()
        self.preview_request = None
        self.open_when_ready = False
        self.connect("destroy", self.stop_loading)
        self.connect("key-press-event", self.shortcuts)

        layout = Gtk.Box()
        self.get_content_area().pack_start(layout, True, True, 0)
        layout.pack_start(self.build_places(), False, False, 0)
        layout.pack_start(self.build_browser(), True, True, 0)
        layout.pack_end(self.build_preview(), False, False, 0)

        selected = Path(filename) if filename else None
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        initial = selected.parent if selected and selected.parent.is_dir() else last_folder
        if not initial or not self.visit(initial):
            if not pictures or not self.visit(Path(pictures)):
                self.visit(Path.home())
        if selected:
            self.select_file(selected)

    def build_places(self):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, width_request=158)
        column.get_style_context().add_class("gallery-places")
        column.pack_start(label("Папки", "setting-title"), False, False, 6)
        entries = [("Домашняя", "user-home-symbolic", Path.home())]
        for title, icon, directory in (
            ("Изображения", "folder-pictures-symbolic", GLib.UserDirectory.DIRECTORY_PICTURES),
            ("Загрузки", "folder-download-symbolic", GLib.UserDirectory.DIRECTORY_DOWNLOAD),
            ("Документы", "folder-documents-symbolic", GLib.UserDirectory.DIRECTORY_DOCUMENTS),
        ):
            if (path := GLib.get_user_special_dir(directory)) and Path(path).is_dir():
                entries.append((title, icon, Path(path)))
        for title, icon, path in entries:
            place = button(title, lambda _, target=path: self.visit(target), icon)
            place.get_style_context().add_class("gallery-place")
            column.pack_start(place, False, False, 0)
        return column

    def build_browser(self):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9, margin=13)
        tools = Gtk.Box(spacing=5)
        self.back = icon_button("go-previous-symbolic", "Предыдущая папка", lambda _: self.move_history(-1))
        self.forward = icon_button("go-next-symbolic", "Следующая папка", lambda _: self.move_history(1))
        self.up = icon_button("go-up-symbolic", "Родительская папка", lambda _: self.parent_folder())
        for control in (self.back, self.forward, self.up):
            tools.pack_start(control, False, False, 0)
        self.path_bar = Gtk.Stack(hhomogeneous=False)
        self.breadcrumbs = Gtk.Box(spacing=3)
        self.breadcrumbs.get_style_context().add_class("gallery-path")
        self.path_bar.add_named(self.breadcrumbs, "breadcrumbs")
        self.location = Gtk.Entry(placeholder_text="Путь к папке или файлу")
        self.location.get_accessible().set_name("Путь к папке или файлу")
        self.location.connect("activate", self.open_location)
        self.path_bar.add_named(self.location, "location")
        tools.pack_start(self.path_bar, True, True, 0)
        tools.pack_start(icon_button("document-edit-symbolic", "Ввести путь (Ctrl+L)",
                                     lambda _: self.focus_location()), False, False, 0)
        tools.pack_start(icon_button("view-refresh-symbolic", "Обновить папку (F5)",
                                     lambda _: self.refresh_folder()), False, False, 0)
        column.pack_start(tools, False, False, 0)
        self.search = Gtk.SearchEntry(placeholder_text="Поиск в этой папке")
        self.search.connect("search-changed", self.filter_items)
        column.pack_start(self.search, False, False, 0)

        theme = Gtk.IconTheme.get_default()
        self.folder_icon = theme.load_icon("folder", 48, Gtk.IconLookupFlags.USE_BUILTIN)
        self.file_icon = theme.load_icon("image-x-generic", 48, Gtk.IconLookupFlags.USE_BUILTIN)
        self.store, self.rows = self.file_model()
        self.grid = Gtk.IconView(model=self.store, pixbuf_column=0, text_column=1,
                                 item_width=108, item_padding=8, row_spacing=8, column_spacing=8, margin=9)
        self.grid.get_accessible().set_name("Файлы и папки")
        self.grid.get_style_context().add_class("gallery-grid")
        self.grid.set_tooltip_column(4)
        for cell in self.grid.get_cells():
            if isinstance(cell, Gtk.CellRendererPixbuf):
                cell.set_fixed_size(*THUMBNAIL_SIZE)
        self.grid.connect("selection-changed", self.update_preview)
        self.grid.connect("item-activated", self.activate_item)
        self.grid.connect("map", self.queue_thumbnails)
        self.grid.connect("size-allocate", self.queue_thumbnails)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.get_vadjustment().connect("value-changed", self.queue_thumbnails)
        scrolled.get_style_context().add_class("gallery-files")
        scrolled.add(self.grid)
        column.pack_start(scrolled, True, True, 0)
        self.message = label("", "muted", wrap=True)
        column.pack_end(self.message, False, False, 0)
        return column

    def file_model(self):
        store = Gtk.ListStore(GdkPixbuf.Pixbuf, str, str, bool, str)
        rows = {}
        for name, path, is_folder in self.items:
            if self.query in name.casefold():
                icon = self.folder_icon if is_folder else self.thumbnail_cache.get(path) or self.file_icon
                rows[path] = store.append((icon, name, path, is_folder, GLib.markup_escape_text(name)))
        return store, rows

    def show_items(self):
        self.store, self.rows = self.file_model()
        self.grid.set_model(self.store)
        self.update_preview()
        if self.rows:
            self.message.set_text(f"Элементов — {len(self.rows)}")
        else:
            self.message.set_text("Совпадений нет." if self.query else "В папке нет изображений.")
        self.queue_thumbnails()

    def build_preview(self):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, width_request=IMAGE_PREVIEW_SIZE + 24)
        column.get_style_context().add_class("gallery-preview")
        column.pack_start(label("Предпросмотр", "dialog-heading"), False, False, 0)
        self.preview = Gtk.Image()
        self.preview.set_valign(Gtk.Align.CENTER)
        column.pack_start(self.preview, True, True, 0)
        self.preview_name = label("", "setting-title")
        self.preview_name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.preview_name.set_max_width_chars(35)
        self.preview_detail = label("", "muted", wrap=True)
        column.pack_end(self.preview_detail, False, False, 0)
        column.pack_end(self.preview_name, False, False, 0)
        self.update_preview()
        return column

    def visit(self, folder, remember=True, *, keep_search=False):
        folder = Path(os.path.abspath(folder))
        try:
            with os.scandir(folder) as entries:
                items = []
                for entry in entries:
                    if entry.name.startswith("."):
                        continue
                    try:
                        is_folder = entry.is_dir()
                        if is_folder or (entry.name.casefold().endswith(IMAGE_SUFFIXES) and entry.is_file()):
                            items.append((entry.name, entry.path, is_folder))
                    except OSError:
                        continue
        except OSError:
            self.message.set_text("Не удалось открыть папку.")
            return False
        if not keep_search:
            self.search.set_text("")
            self.query = ""
        self.folder = folder
        if remember and (self.history_index < 0 or self.history[self.history_index] != folder):
            self.history = self.history[:self.history_index + 1] + [folder]
            self.history_index += 1
        self.update_navigation()
        self.update_breadcrumbs()
        self.folder_version = object()
        self.thumbnail_cache.clear()
        self.items = sorted(items, key=lambda item: (not item[2], item[0].casefold()))
        self.show_items()
        return True

    def stop_loading(self, _):
        self.closed = True
        if self.thumbnail_timer:
            GLib.source_remove(self.thumbnail_timer)
            self.thumbnail_timer = 0
        if self.worker is not None:
            self.jobs.put(None)

    def queue_thumbnails(self, *_):
        if not self.closed and not self.thumbnail_timer:
            self.thumbnail_timer = GLib.timeout_add(60, self.refresh_thumbnails)

    def refresh_thumbnails(self):
        self.thumbnail_timer = 0
        self.start_loading()
        return GLib.SOURCE_REMOVE

    def start_loading(self):
        if self.closed or self.busy:
            return
        if self.preview_request is not None:
            version, filename = self.preview_request
            self.preview_request = None
            self.submit(self.read_preview, filename, lambda result: self.show_preview(version, filename, result))
            return
        if not self.grid.get_mapped():
            return
        visible_range = self.grid.get_visible_range()
        if not visible_range:
            return
        first, last = visible_range[-2:]
        if first is None or last is None:
            return
        jobs = []
        start, end = first.get_indices()[0], last.get_indices()[0]
        for index in range(start, end + 1):
            row = self.store[index]
            if row[3]:
                continue
            path = row[2]
            if path in self.thumbnail_cache:
                self.thumbnail_cache.move_to_end(path)
            elif len(jobs) < THUMBNAIL_BATCH_SIZE:
                jobs.append(path)
        if jobs:
            version = self.folder_version
            limit = max(THUMBNAIL_CACHE_SIZE, end - start + 1)
            self.submit(self.load_thumbnails, jobs, lambda result: self.show_thumbnails(version, limit, result))

    def submit(self, load, argument, display):
        if self.worker is None:
            self.worker = Thread(target=self.decode_images, daemon=True)
            self.worker.start()
        self.busy = True
        self.jobs.put((load, argument, display))

    def decode_images(self):
        while (job := self.jobs.get()) is not None:
            load, argument, display = job
            GLib.idle_add(self.finish_loading, display, load(argument))

    def finish_loading(self, display, result):
        self.busy = False
        if not self.closed:
            display(result)
            self.start_loading()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def load_thumbnails(jobs):
        results = []
        for path in jobs:
            try:
                thumbnail = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, *THUMBNAIL_SIZE, True)
            except GLib.Error:
                thumbnail = None
            results.append((path, thumbnail))
        return results

    def show_thumbnails(self, version, limit, results):
        if version is not self.folder_version:
            return
        for path, thumbnail in results:
            self.thumbnail_cache[path] = thumbnail
            if thumbnail is not None and path in self.rows:
                self.store[self.rows[path]][0] = thumbnail
        while len(self.thumbnail_cache) > limit:
            path, thumbnail = self.thumbnail_cache.popitem(last=False)
            if thumbnail is not None and path in self.rows:
                self.store[self.rows[path]][0] = self.file_icon

    def filter_items(self, *_):
        query = self.search.get_text().strip().casefold()
        if query != self.query:
            selected = self.selected_path()
            self.query = query
            self.show_items()
            if selected:
                self.select_file(selected)

    def update_navigation(self):
        self.back.set_sensitive(self.history_index > 0)
        self.forward.set_sensitive(self.history_index + 1 < len(self.history))
        self.up.set_sensitive(self.folder != self.folder.parent)

    def update_breadcrumbs(self):
        for child in self.breadcrumbs.get_children():
            child.destroy()
        trail = list(reversed((self.folder, *self.folder.parents)[:3]))
        for index, folder in enumerate(trail):
            if index:
                self.breadcrumbs.pack_start(Gtk.Image.new_from_icon_name("go-next-symbolic", Gtk.IconSize.MENU),
                                            False, False, 0)
            caption = label(folder.name or folder.anchor)
            caption.set_max_width_chars(13)
            caption.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            crumb = Gtk.Button(tooltip_text=str(folder), relief=Gtk.ReliefStyle.NONE)
            crumb.get_accessible().set_name(str(folder))
            crumb.add(caption)
            if folder == self.folder:
                crumb.get_style_context().add_class("gallery-current")
                crumb.connect("clicked", lambda _: self.focus_location())
            else:
                crumb.connect("clicked", lambda _, target=folder: self.visit(target))
            self.breadcrumbs.pack_start(crumb, False, False, 0)
        self.breadcrumbs.show_all()
        self.path_bar.set_visible_child_name("breadcrumbs")

    def focus_location(self):
        if self.path_bar.get_visible_child_name() != "location":
            self.location.set_text(str(self.folder or Path.home()))
        self.path_bar.set_visible_child_name("location")
        self.location.grab_focus()
        self.location.select_region(0, -1)

    def parent_folder(self):
        if self.folder and self.folder != self.folder.parent:
            self.visit(self.folder.parent)

    def refresh_folder(self):
        if self.folder:
            path = self.selected_path()
            if self.visit(self.folder, remember=False, keep_search=True) and path:
                self.select_file(path)

    def move_history(self, offset):
        index = self.history_index + offset
        if 0 <= index < len(self.history) and self.visit(self.history[index], remember=False):
            self.history_index = index
            self.update_navigation()

    def open_location(self, entry):
        try:
            path = Path(entry.get_text().strip()).expanduser()
        except RuntimeError:
            self.message.set_text("Не удалось открыть этот путь.")
            return
        if not path.is_absolute():
            path = (self.folder or Path.home()) / path
        path = Path(os.path.abspath(path))
        if path.is_file():
            if self.visit(path.parent) and not self.select_file(path):
                self.message.set_text("Выберите изображение PNG, GIF, JPG или JPEG.")
        else:
            self.visit(path)

    def select_file(self, path):
        row = self.rows.get(os.path.abspath(path))
        if row is None:
            return False
        tree_path = self.store.get_path(row)
        self.grid.select_path(tree_path)
        self.grid.scroll_to_path(tree_path, False, 0, 0)
        return True

    def selected_path(self):
        selected = self.grid.get_selected_items()
        return self.store[selected[0]][2] if selected else None

    def update_preview(self, *_):
        self.preview_version = object()
        self.preview_request = None
        self.open_when_ready = False
        self.filename = None
        self.accept.set_sensitive(False)
        selected = self.grid.get_selected_items()
        if not selected:
            self.preview.set_from_icon_name("image-x-generic-symbolic", Gtk.IconSize.DIALOG)
            self.preview_name.set_text("Выберите изображение")
            self.preview_detail.set_text("PNG, GIF, JPG или JPEG")
            return
        row = self.store[selected[0]]
        name, filename, is_folder = row[1], row[2], row[3]
        self.preview_name.set_text(name)
        if is_folder:
            self.preview.set_from_icon_name("folder-symbolic", Gtk.IconSize.DIALOG)
            self.preview_detail.set_text("Двойной щелчок открывает папку.")
            return
        self.preview.set_from_icon_name("image-x-generic-symbolic", Gtk.IconSize.DIALOG)
        self.preview_detail.set_text("Загрузка предпросмотра…")
        self.preview_request = (self.preview_version, filename)
        self.start_loading()

    @staticmethod
    def read_preview(filename):
        try:
            image_format, width, height = GdkPixbuf.Pixbuf.get_file_info(filename)
            if not image_format or image_format.get_name() not in IMAGE_FORMATS:
                return None
            image = GdkPixbuf.Pixbuf.new_from_file_at_scale(filename, IMAGE_PREVIEW_SIZE, 420, True)
            size = GLib.format_size(Path(filename).stat().st_size)
        except (GLib.Error, OSError):
            return None
        return image, f"{width} × {height}  ·  {image_format.get_name().upper()}  ·  {size}"

    def show_preview(self, version, filename, result):
        if version is not self.preview_version:
            return
        if result is None:
            self.preview.set_from_icon_name("image-missing-symbolic", Gtk.IconSize.DIALOG)
            self.preview_detail.set_text("Не удалось прочитать изображение.")
            return
        image, detail = result
        self.preview.set_from_pixbuf(image)
        self.preview_detail.set_text(detail)
        self.filename = filename
        self.accept.set_sensitive(True)
        if self.open_when_ready:
            self.response(Gtk.ResponseType.ACCEPT)

    def activate_item(self, _grid, tree_path):
        row = self.store[tree_path]
        path, is_folder = row[2], row[3]
        if is_folder:
            self.visit(Path(path))
        elif self.filename == path:
            self.response(Gtk.ResponseType.ACCEPT)
        elif self.grid.path_is_selected(tree_path):
            self.open_when_ready = True

    def shortcuts(self, _widget, event):
        modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
        if modifiers == Gdk.ModifierType.CONTROL_MASK:
            if event.keyval == Gdk.KEY_l:
                self.focus_location()
                return True
            if event.keyval == Gdk.KEY_f:
                self.search.grab_focus()
                return True
        if modifiers == Gdk.ModifierType.MOD1_MASK:
            if event.keyval == Gdk.KEY_Up:
                self.parent_folder()
                return True
            if event.keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
                self.move_history(-1 if event.keyval == Gdk.KEY_Left else 1)
                return True
        if not modifiers and event.keyval == Gdk.KEY_F5:
            self.refresh_folder()
            return True
        if not modifiers and event.keyval == Gdk.KEY_Escape and self.location.is_focus():
            self.path_bar.set_visible_child_name("breadcrumbs")
            self.grid.grab_focus()
            return True
        return False
