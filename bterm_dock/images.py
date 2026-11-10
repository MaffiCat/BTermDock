"""Загрузка изображений, выбор файла и значки SSH-профилей."""

import math
from pathlib import Path

from ._gtk import Gdk, GdkPixbuf, GLib, GObject, Gtk, Pango
from .gallery import IMAGE_FORMATS, ImageGallery
from .widgets import label

PROFILE_ICON_SIZE = 36


def load_image(path):
    image_format, _, _ = GdkPixbuf.Pixbuf.get_file_info(path)
    if not image_format or image_format.get_name() not in IMAGE_FORMATS:
        raise ValueError("Выберите доступный файл PNG, GIF, JPG или JPEG.")
    return GdkPixbuf.PixbufAnimation.new_from_file(path)


class ImagePicker(Gtk.Button):
    __gsignals__ = {"file-set": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, parent, title):
        super().__init__()
        self.parent = parent
        self.title = title
        self.filename = None
        self.last_folder = None
        self.get_accessible().set_name(title)
        row = Gtk.Box(spacing=8)
        row.pack_start(Gtk.Image.new_from_icon_name("image-x-generic-symbolic", Gtk.IconSize.BUTTON),
                       False, False, 0)
        self.caption = label("Выбрать изображение…")
        self.caption.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.caption.set_max_width_chars(24)
        row.pack_start(self.caption, True, True, 0)
        row.pack_end(Gtk.Image.new_from_icon_name("folder-open-symbolic", Gtk.IconSize.BUTTON),
                     False, False, 0)
        self.add(row)
        self.connect("clicked", self.choose)

    def get_filename(self):
        return self.filename

    def set_filename(self, filename):
        self.filename = filename
        self.caption.set_text(Path(filename).name if filename else "Выбрать изображение…")
        self.set_tooltip_text(filename or self.title)
        return True

    def unselect_all(self):
        self.set_filename(None)

    def choose(self, _):
        gallery = ImageGallery(self.parent, self.title, self.filename, self.last_folder)
        gallery.show_all()
        response = gallery.run()
        filename = gallery.filename
        self.last_folder = gallery.folder
        gallery.destroy()
        if response == Gtk.ResponseType.ACCEPT and filename:
            self.set_filename(filename)
            self.emit("file-set")


def load_profile_icon(path):
    return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 2 * PROFILE_ICON_SIZE,
                                                2 * PROFILE_ICON_SIZE, True)


def draw_profile_icon(_, context, pixbuf):
    radius = PROFILE_ICON_SIZE / 2
    width, height = pixbuf.get_width(), pixbuf.get_height()
    scale = PROFILE_ICON_SIZE / min(width, height)
    context.arc(radius, radius, radius, 0, math.tau)
    context.clip()
    context.translate((PROFILE_ICON_SIZE - width * scale) / 2,
                      (PROFILE_ICON_SIZE - height * scale) / 2)
    context.scale(scale, scale)
    Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
    context.paint()
    return False


def profile_avatar(profile, name):
    pixbuf = None
    if path := profile.get("icon"):
        try:
            pixbuf = load_profile_icon(path)
        except GLib.Error:
            pass
    if pixbuf:
        avatar = Gtk.DrawingArea()
        avatar.connect("draw", draw_profile_icon, pixbuf)
    else:
        avatar = Gtk.Label(label=name[:1].upper() or "?")
        avatar.get_style_context().add_class("profile-avatar")
    avatar.set_size_request(PROFILE_ICON_SIZE, PROFILE_ICON_SIZE)
    avatar.set_valign(Gtk.Align.CENTER)
    return avatar
