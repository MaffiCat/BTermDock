"""Локальный выбор изображения с миниатюрами и крупным предпросмотром."""

from collections import OrderedDict
import os
from pathlib import Path
from queue import SimpleQueue
from threading import Event, Thread

from ._gtk import Gdk, GdkPixbuf, GLib, Gtk, Pango
from .dialogs import DockDialog
from .media import IMAGE_FORMATS, VideoPlayer, VideoSource, download_media, load_thumbnail
from .widgets import button, icon_button, label

IMAGE_SUFFIXES = (".png", ".gif", ".jpg", ".jpeg")
IMAGE_PREVIEW_SIZE = 350
THUMBNAIL_SIZE = (94, 62)
THUMBNAIL_CACHE_SIZE = 96
THUMBNAIL_BATCH_SIZE = 4


class ImageGallery(DockDialog):
    def __init__(self, parent, title, filename, last_folder=None, *, allow_video=False):
        super().__init__(parent, title)
        self.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Выбрать", Gtk.ResponseType.ACCEPT)
        self.accept = self.get_widget_for_response(Gtk.ResponseType.ACCEPT)
        self.accept.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.ACCEPT)
        self.set_default_size(1040, 580)
        self.get_style_context().add_class("gallery-dialog")
        self.filename = None
        self.allow_video = allow_video
        self.suffixes = IMAGE_SUFFIXES + (".mp4",) if allow_video else IMAGE_SUFFIXES
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
        self.preview_video = self.preview_gif = None
        self.preview_frame = None
        self.preview_timer = 0
        self.link_cancel = self.link_request = None
        self.connect("destroy", self.stop_loading)
        self.connect("key-press-event", self.shortcuts)

        layout = Gtk.Box(spacing=12, margin=12)
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
        self.initial_focus = self.grid if self.rows else self.get_widget_for_response(Gtk.ResponseType.CANCEL)

    def build_places(self):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, width_request=148)
        column.get_style_context().add_class("gallery-places")
        title = label("МЕСТА", "eyebrow")
        title.get_style_context().add_class("muted")
        title.set_margin_start(7)
        column.pack_start(title, False, False, 8)
        entries = [("Домашняя", "user-home-symbolic", Path.home())]
        for title, icon, directory in (
            ("Изображения", "folder-pictures-symbolic", GLib.UserDirectory.DIRECTORY_PICTURES),
            ("Загрузки", "folder-download-symbolic", GLib.UserDirectory.DIRECTORY_DOWNLOAD),
            ("Документы", "folder-documents-symbolic", GLib.UserDirectory.DIRECTORY_DOCUMENTS),
        ):
            if (path := GLib.get_user_special_dir(directory)) and Path(path).is_dir():
                entries.append((title, icon, Path(path)))
        self.places = {}
        for title, icon, path in entries:
            place = Gtk.Button(tooltip_text=str(path))
            place.get_accessible().set_name(title)
            place.get_style_context().add_class("gallery-place")
            row = Gtk.Box(spacing=8)
            row.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON), False, False, 0)
            row.pack_start(label(title), True, True, 0)
            place.add(row)
            place.connect("clicked", lambda _, target=path: self.visit(target))
            self.places[path] = place
            column.pack_start(place, False, False, 0)
        if self.allow_video:
            column.pack_end(button("По ссылке…", self.focus_link, "insert-link-symbolic"), False, False, 0)
        return column

    def build_browser(self):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9, width_request=244)
        tools = Gtk.Box(spacing=8)
        navigation = Gtk.Box()
        navigation.get_style_context().add_class("gallery-navigation")
        self.back = icon_button("go-previous-symbolic", "Предыдущая папка", lambda _: self.move_history(-1))
        self.forward = icon_button("go-next-symbolic", "Следующая папка", lambda _: self.move_history(1))
        self.up = icon_button("go-up-symbolic", "Родительская папка", lambda _: self.parent_folder())
        for control in (self.back, self.forward, self.up):
            navigation.pack_start(control, False, False, 0)
        tools.pack_start(navigation, False, False, 0)
        address = Gtk.Box()
        address.get_style_context().add_class("gallery-address")
        self.path_bar = Gtk.Stack(hhomogeneous=False)
        self.breadcrumbs = Gtk.Box()
        self.breadcrumbs.get_style_context().add_class("gallery-path")
        self.path_bar.add_named(self.breadcrumbs, "breadcrumbs")
        self.location = Gtk.Entry(
            width_chars=1, placeholder_text="Путь или прямая ссылка" if self.allow_video else "Путь к файлу",
        )
        self.location.get_accessible().set_name("Путь к папке или файлу")
        self.location.connect("activate", self.open_location)
        self.path_bar.add_named(self.location, "location")
        address.pack_start(self.path_bar, True, True, 0)
        address.pack_end(icon_button("document-edit-symbolic", "Ввести путь (Ctrl+L)",
                                     lambda _: self.focus_location()), False, False, 0)
        tools.pack_start(address, True, True, 0)
        tools.pack_start(icon_button("view-refresh-symbolic", "Обновить папку (F5)",
                                     lambda _: self.refresh_folder()), False, False, 0)
        column.pack_start(tools, False, False, 0)
        self.search = Gtk.SearchEntry(placeholder_text="Поиск в этой папке")
        self.search.get_style_context().add_class("gallery-search")
        self.search.connect("search-changed", self.filter_items)
        column.pack_start(self.search, False, False, 0)

        self.folder_icon = self.tile_icon("folder-symbolic", "dock_accent")
        self.file_icon = self.tile_icon("image-x-generic-symbolic", "dock_muted")
        self.video_icon = self.tile_icon("video-x-generic-symbolic", "dock_muted")
        self.store, self.rows = self.file_model()
        self.grid = Gtk.IconView(model=self.store, item_width=110, item_padding=6,
                                 row_spacing=7, column_spacing=7, margin=8, spacing=3)
        self.grid.get_accessible().set_name("Файлы и папки")
        self.grid.get_style_context().add_class("gallery-grid")
        self.grid.set_tooltip_column(4)
        thumbnail = Gtk.CellRendererPixbuf(xalign=0.5, yalign=0.5)
        thumbnail.set_fixed_size(*THUMBNAIL_SIZE)
        self.grid.pack_start(thumbnail, False)
        self.grid.add_attribute(thumbnail, "pixbuf", 0)
        caption = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.MIDDLE, xalign=0.5, yalign=0.5)
        caption.set_fixed_size(110, 22)
        self.grid.pack_start(caption, False)
        self.grid.add_attribute(caption, "text", 1)
        self.grid.connect("selection-changed", self.update_preview)
        self.grid.connect("item-activated", self.activate_item)
        self.grid.connect("map", self.queue_thumbnails)
        self.grid.connect("size-allocate", self.queue_thumbnails)
        scrolled = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.get_vadjustment().connect("value-changed", self.queue_thumbnails)
        scrolled.add(self.grid)
        self.browser = Gtk.Stack()
        self.browser.get_style_context().add_class("gallery-files")
        self.browser.add_named(scrolled, "files")
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, margin=16)
        empty_icon = Gtk.Image.new_from_icon_name("folder-open-symbolic", Gtk.IconSize.DIALOG)
        empty_icon.set_pixel_size(32)
        empty_icon.get_style_context().add_class("muted")
        empty.pack_start(empty_icon, False, False, 0)
        self.empty_title = Gtk.Label()
        self.empty_title.get_style_context().add_class("setting-title")
        self.empty_detail = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.empty_detail.get_style_context().add_class("muted")
        empty.pack_start(self.empty_title, False, False, 0)
        empty.pack_start(self.empty_detail, False, False, 0)
        self.browser.add_named(empty, "empty")
        self.browser.show_all()
        column.pack_start(self.browser, True, True, 0)
        self.message = label("", "muted", wrap=True)
        column.pack_end(self.message, False, False, 0)
        return column

    def tile_icon(self, name, shade):
        theme = Gtk.IconTheme.get_default()
        icon = theme.lookup_icon(name, 32, Gtk.IconLookupFlags.FORCE_SYMBOLIC)
        if icon is None:
            return None
        found, tint = self.get_style_context().lookup_color(shade)
        if not found:
            tint = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        try:
            return icon.load_symbolic(tint, None, None, None)[0]
        except GLib.Error:
            return None

    def file_model(self):
        store = Gtk.ListStore(GdkPixbuf.Pixbuf, str, str, bool, str)
        rows = {}
        for name, path, is_folder in self.items:
            if self.query in name.casefold():
                if is_folder:
                    icon = self.folder_icon
                elif name.lower().endswith(".mp4"):
                    icon = self.video_icon
                else:
                    icon = self.thumbnail_cache.get(path) or self.file_icon
                rows[path] = store.append((icon, name, path, is_folder, GLib.markup_escape_text(name)))
        return store, rows

    def show_items(self):
        self.store, self.rows = self.file_model()
        self.grid.set_model(self.store)
        self.update_preview()
        self.browser.set_visible_child_name("files" if self.rows else "empty")
        if self.rows:
            self.message.set_text(f"Элементов: {len(self.rows)}")
        else:
            self.empty_title.set_text("Ничего не найдено" if self.query else "Здесь пока пусто")
            self.empty_detail.set_text("Попробуйте другой запрос." if self.query else
                                       "В папке нет файлов подходящего формата.")
            self.message.set_text("")
        self.queue_thumbnails()

    def build_preview(self):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, width_request=270)
        column.get_style_context().add_class("gallery-preview")
        column.pack_start(label("Предпросмотр", "setting-title"), False, False, 0)
        self.preview_stage = Gtk.Stack()
        self.preview_stage.get_style_context().add_class("gallery-preview-stage")
        self.preview = Gtk.DrawingArea()
        self.preview.get_accessible().set_name("Изображение или видео")
        self.preview.connect("draw", self.draw_preview)
        self.preview_stage.add_named(self.preview, "media")
        placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, margin=12)
        placeholder.get_style_context().add_class("gallery-preview-placeholder")
        self.preview_icon = Gtk.Image(halign=Gtk.Align.CENTER, pixel_size=28)
        self.preview_hint = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.preview_hint.set_max_width_chars(27)
        self.preview_hint.get_style_context().add_class("muted")
        placeholder.pack_start(self.preview_icon, False, False, 0)
        placeholder.pack_start(self.preview_hint, False, False, 0)
        self.preview_stage.add_named(placeholder, "placeholder")
        self.preview_stage.show_all()
        column.pack_start(self.preview_stage, True, True, 0)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        details.get_style_context().add_class("gallery-preview-details")
        self.preview_name = label("", "setting-title")
        self.preview_name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.preview_name.set_max_width_chars(29)
        self.preview_detail = label("", "muted", wrap=True)
        details.pack_start(self.preview_name, False, False, 0)
        details.pack_start(self.preview_detail, False, False, 0)
        column.pack_end(details, False, False, 0)
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
                        if is_folder or (entry.name.casefold().endswith(self.suffixes) and entry.is_file()):
                            items.append((entry.name, entry.path, is_folder))
                    except OSError:
                        continue
        except OSError:
            self.message.set_text("Не удалось открыть папку.")
            return False
        self.cancel_link()
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
        self.stop_preview()
        self.cancel_link()
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
        if self.link_request is not None:
            request = self.link_request
            self.link_request = None
            self.submit(self.read_link, request, lambda result: self.show_link(request[1], result))
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
            if row[3] or row[2].lower().endswith(".mp4"):
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
                thumbnail = load_thumbnail(path, *THUMBNAIL_SIZE)
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
        for path, place in self.places.items():
            style = place.get_style_context()
            if self.folder == path:
                style.add_class("gallery-current")
            else:
                style.remove_class("gallery-current")

    def update_breadcrumbs(self):
        for child in self.breadcrumbs.get_children():
            child.destroy()
        trail = list(reversed((self.folder, *self.folder.parents)[:2]))
        home = Path.home()
        if home in trail:
            trail = trail[trail.index(home):]
        for index, folder in enumerate(trail):
            if index:
                self.breadcrumbs.pack_start(Gtk.Image.new_from_icon_name("go-next-symbolic", Gtk.IconSize.MENU),
                                            False, False, 0)
            caption = label("Домашняя" if folder == home else folder.name or folder.anchor)
            caption.set_max_width_chars(12)
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

    def focus_link(self, _):
        self.focus_location()
        self.location.set_text("")
        self.message.set_text("Вставьте прямую ссылку на PNG, JPG, GIF или MP4 и нажмите Enter.")

    def cancel_link(self):
        if self.link_cancel is not None:
            self.link_cancel.set()
            self.link_cancel = self.link_request = None

    def open_link(self, url):
        self.cancel_link()
        self.link_cancel = Event()
        self.link_request = (url, self.link_cancel)
        self.message.set_text("Загрузка файла по ссылке…")
        self.start_loading()

    @staticmethod
    def read_link(request):
        try:
            return download_media(*request), None
        except (GLib.Error, ValueError) as error:
            return None, str(error)
        except OSError:
            return None, "Не удалось скачать или сохранить файл."

    def show_link(self, cancelled, result):
        if cancelled.is_set():
            return
        self.link_cancel = None
        path, error = result
        if error:
            self.message.set_text(error)
        elif path and self.visit(path.parent):
            self.select_file(path)

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
        value = entry.get_text().strip()
        if self.allow_video and value.lower().startswith(("https://", "http://")):
            self.open_link(value)
            return
        try:
            path = Path(value).expanduser()
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
        self.stop_preview()
        self.preview_version = object()
        self.preview_request = None
        self.open_when_ready = False
        self.filename = None
        self.accept.set_sensitive(False)
        selected = self.grid.get_selected_items()
        if not selected:
            self.show_preview_placeholder("image-x-generic-symbolic", "Выберите файл для просмотра")
            self.preview_name.set_text("Форматы файлов")
            self.preview_name.set_tooltip_text(None)
            self.preview_detail.set_text("PNG  ·  JPG  ·  GIF  ·  MP4" if self.allow_video else "PNG  ·  JPG  ·  GIF")
            return
        row = self.store[selected[0]]
        name, filename, is_folder = row[1], row[2], row[3]
        self.preview_name.set_text(name)
        self.preview_name.set_tooltip_text(name)
        if is_folder:
            self.show_preview_placeholder("folder-symbolic", "Откройте папку двойным щелчком")
            self.preview_detail.set_text("Папка")
            return
        self.show_preview_placeholder("image-x-generic-symbolic", "Загрузка предпросмотра…")
        self.preview_detail.set_text("")
        self.preview_request = (self.preview_version, filename)
        self.start_loading()

    def show_preview_placeholder(self, icon, hint):
        self.preview_icon.set_from_icon_name(icon, Gtk.IconSize.DIALOG)
        self.preview_hint.set_text(hint)
        self.preview_stage.set_visible_child_name("placeholder")

    @staticmethod
    def read_preview(filename):
        try:
            size = GLib.format_size(Path(filename).stat().st_size)
            if filename.lower().endswith(".mp4"):
                video = VideoSource(filename)
                return video, f"{video.width} × {video.height}  ·  MP4  ·  {size}"
            image_format, width, height = GdkPixbuf.Pixbuf.get_file_info(filename)
            if not image_format or image_format.get_name() not in IMAGE_FORMATS:
                return None, "Не удалось прочитать изображение."
            format_name = image_format.get_name()
            if format_name == "gif":
                image = GdkPixbuf.PixbufAnimation.new_from_file(filename)
            else:
                image = GdkPixbuf.Pixbuf.new_from_file_at_scale(filename, IMAGE_PREVIEW_SIZE, 420, True)
        except ValueError as error:
            return None, str(error)
        except (GLib.Error, OSError):
            return None, "Не удалось прочитать файл."
        return image, f"{width} × {height}  ·  {format_name.upper()}  ·  {size}"

    def show_preview(self, version, filename, result):
        if version is not self.preview_version:
            return
        image, detail = result
        if image is None:
            self.show_preview_placeholder("image-missing-symbolic", detail)
            return
        self.preview_detail.set_text(detail)
        if isinstance(image, VideoSource):
            try:
                self.preview_video = VideoPlayer(image, lambda pixbuf: self.show_video_frame(pixbuf, filename),
                                                 self.preview_video_error)
            except ValueError as error:
                self.preview_video_error(str(error))
                return
            self.preview_video.play()
            return
        elif isinstance(image, GdkPixbuf.PixbufAnimation):
            self.preview_gif = image.get_iter(None)
            self.show_gif_frame()
        else:
            self.show_preview_frame(image)
        self.accept_preview(filename)

    def accept_preview(self, filename):
        self.filename = filename
        self.accept.set_sensitive(True)
        if self.open_when_ready:
            self.response(Gtk.ResponseType.ACCEPT)

    def show_preview_frame(self, pixbuf):
        self.preview_frame = pixbuf
        if self.preview_stage.get_visible_child() is not self.preview:
            self.preview_stage.set_visible_child(self.preview)
        self.preview.queue_draw()

    def draw_preview(self, area, context):
        pixbuf = self.preview_frame
        if pixbuf is None:
            return False
        width, height = area.get_allocated_width(), area.get_allocated_height()
        image_width, image_height = pixbuf.get_width(), pixbuf.get_height()
        scale = min(1, max(1, width - 16) / image_width, max(1, height - 16) / image_height)
        context.translate((width - image_width * scale) / 2, (height - image_height * scale) / 2)
        context.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
        context.paint()
        return False

    def show_video_frame(self, pixbuf, filename):
        self.show_preview_frame(pixbuf)
        if self.filename is None:
            self.accept_preview(filename)

    def show_gif_frame(self):
        self.preview_timer = 0
        self.preview_gif.advance(None)
        self.show_preview_frame(self.preview_gif.get_pixbuf())
        delay = self.preview_gif.get_delay_time()
        if delay >= 0:
            self.preview_timer = GLib.timeout_add(max(20, delay), self.show_gif_frame)
        return GLib.SOURCE_REMOVE

    def stop_preview(self):
        if self.preview_timer:
            GLib.source_remove(self.preview_timer)
            self.preview_timer = 0
        self.preview_gif = None
        self.preview_frame = None
        if self.preview_video:
            self.preview_video.close()
            self.preview_video = None

    def preview_video_error(self, message):
        self.filename = None
        self.accept.set_sensitive(False)
        self.show_preview_placeholder("image-missing-symbolic", message)
        self.preview_detail.set_text("")

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
