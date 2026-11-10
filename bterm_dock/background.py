"""Отрисовка и предпросмотр фона терминала."""

import cairo

from ._gtk import Gdk, GLib, GObject, Gtk
from .images import ImagePicker, load_image
from .settings import MAX_BACKGROUND_ZOOM, MIN_BACKGROUND_ZOOM
from .widgets import button, color, label


class Wallpaper(Gtk.DrawingArea):
    __gsignals__ = {"wallpaper-changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, background):
        super().__init__()
        self.animation = self.frame = None
        self.timer = 0
        self.dim = 0.72
        self.zoom = 1.0
        self.position = (0.0, 0.0)
        self.fill = None
        self.base = background
        self.window_state_handler = None
        self.connect("draw", self.draw_image)
        self.connect("realize", self.watch_window)
        self.connect("unrealize", self.unwatch_window)
        self.connect("map", self.resume_animation)
        self.connect("unmap", lambda _: self.stop_timer())

    def watch_window(self, _):
        window = self.get_toplevel()
        self.window_state_handler = (window, window.connect("window-state-event", self.window_state_changed))

    def unwatch_window(self, _):
        self.stop_timer()
        if self.window_state_handler:
            window, handler = self.window_state_handler
            window.disconnect(handler)
            self.window_state_handler = None

    def window_state_changed(self, _, event):
        if event.changed_mask & Gdk.WindowState.ICONIFIED:
            if event.new_window_state & Gdk.WindowState.ICONIFIED:
                self.stop_timer()
            else:
                self.resume_animation()
        return False

    def resume_animation(self, *_):
        if self.frame and not self.timer:
            if self.frame.advance(None):
                self.redraw()
            self.schedule_frame()

    def stop_timer(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0

    def set_image(self, animation, appearance):
        self.dim = appearance["dim"]
        self.zoom = appearance["background_zoom"]
        self.position = (appearance["background_x"], appearance["background_y"])
        self.fill = color(appearance["background_color"]) if appearance["background_color"] else None
        if animation is not self.animation:
            self.stop_timer()
            self.animation = animation
            self.frame = None
            if animation and not animation.is_static_image():
                self.frame = animation.get_iter(None)
                self.schedule_frame()
        self.redraw()

    def redraw(self):
        self.queue_draw()
        self.emit("wallpaper-changed")

    @property
    def has_custom_background(self):
        return self.animation is not None or self.fill is not None

    def schedule_frame(self):
        if not self.get_mapped() or self.get_toplevel().get_window().get_state() & Gdk.WindowState.ICONIFIED:
            return
        delay = self.frame.get_delay_time()
        if delay >= 0:
            self.timer = GLib.timeout_add(max(delay, 20), self.next_frame)

    def next_frame(self):
        self.timer = 0
        if self.frame.advance(None):
            self.redraw()
        else:
            self.schedule_frame()
        return GLib.SOURCE_REMOVE

    def draw_image(self, widget, context):
        self.paint(context, widget.get_allocated_width(), widget.get_allocated_height())
        if self.frame and not self.timer:
            self.schedule_frame()
        return False

    def paint(self, context, width, height):
        fill = self.fill or self.base
        context.set_source_rgb(fill.red, fill.green, fill.blue)
        context.paint()
        if not self.animation:
            return
        pixbuf = self.frame.get_pixbuf() if self.frame else self.animation.get_static_image()
        scale = self.zoom * max(width / pixbuf.get_width(), height / pixbuf.get_height())
        offset_x, offset_y = self.position
        context.save()
        context.translate((width - pixbuf.get_width() * scale) / 2 + offset_x * width,
                          (height - pixbuf.get_height() * scale) / 2 + offset_y * height)
        context.scale(scale, scale)
        if self.fill is None:
            Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
            context.paint_with_alpha(1 - self.dim)
        else:
            # Затемнение применяется к фото, сохраняя цвет заливки и прозрачные участки.
            context.rectangle(0, 0, pixbuf.get_width(), pixbuf.get_height())
            context.clip()
            context.push_group()
            Gdk.cairo_set_source_pixbuf(context, pixbuf, 0, 0)
            context.paint()
            context.set_operator(cairo.OPERATOR_ATOP)
            context.set_source_rgba(self.base.red, self.base.green, self.base.blue, self.dim)
            context.paint()
            context.pop_group_to_source()
            context.paint()
        context.restore()


class BackgroundPreview(Gtk.AspectFrame):
    def __init__(self, workspace, on_move):
        super().__init__(xalign=0.5, yalign=0.5, obey_child=False, shadow_type=Gtk.ShadowType.IN)
        self.set_size_request(-1, 148)
        self.wallpaper = workspace.wallpaper
        page = workspace.current_page()
        self.terminal = page.terminal if page else self.wallpaper
        self.on_move = on_move
        self.drag_start = self.wallpaper.position
        self.canvas = Gtk.DrawingArea()
        self.canvas.get_accessible().set_name("Предпросмотр фона")
        self.canvas.connect("draw", self.draw_preview)
        self.canvas.connect("realize", lambda _: self.update_cursor())
        self.add(self.canvas)
        self.drag = Gtk.GestureDrag.new(self.canvas)
        self.drag.set_button(1)
        self.drag.connect("drag-begin", self.start_drag)
        self.drag.connect("drag-update", self.move_photo)
        self.drag.connect("drag-end", lambda *_: self.update_cursor())
        self.wallpaper_handler = self.wallpaper.connect("wallpaper-changed", self.update_preview)
        self.size_handler = self.terminal.connect_after("size-allocate", self.update_size)
        self.connect("destroy", self.detach)
        self.update_size()

    def detach(self, _):
        if self.wallpaper.handler_is_connected(self.wallpaper_handler):
            self.wallpaper.disconnect(self.wallpaper_handler)
        if self.terminal.handler_is_connected(self.size_handler):
            self.terminal.disconnect(self.size_handler)

    def update_size(self, *_):
        self.set_property("ratio", max(1, self.terminal.get_allocated_width())
                          / max(1, self.terminal.get_allocated_height()))
        self.canvas.queue_draw()

    def update_preview(self, _):
        self.canvas.queue_draw()

    def update_cursor(self, dragging=False):
        if window := self.canvas.get_window():
            name = "grabbing" if dragging else "grab"
            cursor = None
            if self.wallpaper.animation is not None:
                cursor = Gdk.Cursor.new_from_name(window.get_display(), name)
            window.set_cursor(cursor)

    def draw_preview(self, canvas, context):
        coordinates = self.terminal.translate_coordinates(self.wallpaper, 0, 0)
        x, y = coordinates[-2:] if coordinates else (0, 0)
        context.scale(canvas.get_allocated_width() / max(1, self.terminal.get_allocated_width()),
                      canvas.get_allocated_height() / max(1, self.terminal.get_allocated_height()))
        context.translate(-x, -y)
        self.wallpaper.paint(context, self.wallpaper.get_allocated_width(),
                             self.wallpaper.get_allocated_height())
        return False

    def start_drag(self, gesture, _x, _y):
        if self.wallpaper.animation is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.drag_start = self.wallpaper.position
        self.update_cursor(True)

    def move_photo(self, _, dx, dy):
        start_x, start_y = self.drag_start
        dx *= self.terminal.get_allocated_width() / max(1, self.canvas.get_allocated_width())
        dy *= self.terminal.get_allocated_height() / max(1, self.canvas.get_allocated_height())
        x = start_x + dx / max(1, self.wallpaper.get_allocated_width())
        y = start_y + dy / max(1, self.wallpaper.get_allocated_height())
        self.on_move(max(-1, min(1, x)), max(-1, min(1, y)))


def edit_background(window, parent=None):
    dialog = Gtk.Dialog(title="Фон терминала", transient_for=parent or window, modal=True,
                        destroy_with_parent=True, use_header_bar=True)
    dialog.get_style_context().add_class("dock-dialog")
    dialog.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Убрать фото", 1,
                       "Применить", Gtk.ResponseType.OK)
    dialog.get_widget_for_response(Gtk.ResponseType.OK).get_style_context().add_class("suggested-action")
    dialog.set_default_response(Gtk.ResponseType.OK)
    layout = Gtk.Box(spacing=18, margin=20)
    dialog.get_content_area().pack_start(layout, True, True, 0)

    stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    stage.get_style_context().add_class("wallpaper-stage")
    layout.pack_start(stage, True, True, 0)
    preview_controls = Gtk.Box(spacing=8)
    preview_controls.pack_start(label("Предпросмотр", "dialog-heading"), True, True, 0)
    reset = button("По центру", lambda _: move_photo(0.0, 0.0))
    preview_controls.pack_end(reset, False, False, 0)
    stage.pack_start(preview_controls, False, False, 0)
    preview = BackgroundPreview(window.workspace, lambda x, y: move_photo(x, y))
    preview.set_size_request(350, 260)
    preview.get_style_context().add_class("wallpaper-preview")
    stage.pack_start(preview, True, True, 0)
    stage.pack_start(label("Перетащите фото в превью, чтобы настроить кадр.", "muted", wrap=True),
                     False, False, 0)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, width_request=252)
    box.get_style_context().add_class("wallpaper-controls")
    layout.pack_end(box, False, False, 0)
    box.pack_start(label("Изображение", "dialog-heading"), False, False, 0)
    box.pack_start(label("PNG, GIF, JPG или JPEG", "muted"), False, False, 0)
    picker = ImagePicker(dialog, "Выберите фон")
    if window.settings["background"]:
        picker.set_filename(window.settings["background"])
    box.pack_start(picker, False, False, 0)
    box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
    box.pack_start(label("Масштаб фото", "setting-title"), False, False, 0)
    zoom = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, MIN_BACKGROUND_ZOOM * 100,
                                   MAX_BACKGROUND_ZOOM * 100, 1)
    zoom.set_value(window.settings["background_zoom"] * 100)
    zoom.set_digits(0)
    zoom.get_accessible().set_name("Масштаб фото")
    zoom.connect("format-value", lambda _, value: f"{value:.0f}%")
    zoom.add_mark(100, Gtk.PositionType.BOTTOM, None)
    zoom.set_tooltip_text("При 100% фото заполняет область терминала.")
    box.pack_start(zoom, False, False, 0)
    box.pack_start(label("Приглушение фото", "setting-title"), False, False, 0)
    dim = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 15, 95, 1)
    dim.set_value(window.settings["dim"] * 100)
    dim.set_digits(0)
    dim.get_accessible().set_name("Приглушение фото")
    dim.connect("format-value", lambda _, value: f"{value:.0f}%")
    box.pack_start(dim, False, False, 0)
    wallpaper = window.workspace.wallpaper
    fill_row = Gtk.Box(spacing=12)
    use_fill = Gtk.CheckButton(label="Свой цвет фона")
    use_fill.set_active(wallpaper.fill is not None)
    fill_picker = Gtk.ColorButton.new_with_rgba(wallpaper.fill or wallpaper.base)
    fill_picker.set_title("Цвет фона под фото")
    fill_picker.get_accessible().set_name("Цвет фона под фото")
    fill_picker.set_sensitive(use_fill.get_active())
    fill_row.pack_start(use_fill, True, True, 0)
    fill_row.pack_end(fill_picker, False, False, 0)
    box.pack_start(fill_row, False, False, 0)
    error_label = label("", "error", wrap=True)
    error_label.set_no_show_all(True)
    box.pack_start(error_label, False, False, 0)
    original_image = preview_image = wallpaper.animation
    position = wallpaper.position
    original_appearance = {
        "dim": wallpaper.dim,
        "background_zoom": wallpaper.zoom,
        "background_color": wallpaper.fill.to_string() if wallpaper.fill else "",
        "background_x": position[0],
        "background_y": position[1],
    }
    preview_path = window.settings["background"] if preview_image else ""

    def selected_appearance():
        return {
            "dim": dim.get_value() / 100,
            "background_zoom": zoom.get_value() / 100,
            "background_color": fill_picker.get_rgba().to_string() if use_fill.get_active() else "",
            "background_x": position[0],
            "background_y": position[1],
        }

    def update_preview(_=None):
        fill_picker.set_sensitive(use_fill.get_active())
        reset.set_sensitive(preview_image is not None and position != (0.0, 0.0))
        window.workspace.set_wallpaper(preview_image, selected_appearance())

    def move_photo(x, y):
        nonlocal position
        if position != (x, y):
            position = (x, y)
            update_preview()

    def selected_image(path):
        nonlocal preview_image, preview_path, position
        if path != preview_path:
            preview_image = load_image(path) if path else None
            preview_path = path
            position = (0.0, 0.0)
        return preview_image

    def preview_selected(button):
        try:
            selected_image(button.get_filename() or "")
        except (GLib.Error, ValueError) as error:
            error_label.set_text(str(error))
            error_label.show()
            return
        error_label.hide()
        update_preview()
        preview.update_cursor()

    picker.connect("file-set", preview_selected)
    dim.connect("value-changed", update_preview)
    zoom.connect("value-changed", update_preview)
    use_fill.connect("toggled", update_preview)
    fill_picker.connect("color-set", update_preview)
    update_preview()
    dialog.set_default_size(820, 480)
    dialog.show_all()
    while (response := dialog.run()) in (1, Gtk.ResponseType.OK):
        path = (picker.get_filename() or "") if response == Gtk.ResponseType.OK else ""
        try:
            image = selected_image(path) if response == Gtk.ResponseType.OK else None
        except (GLib.Error, ValueError) as error:
            error_label.set_text(str(error))
            error_label.show()
            continue
        appearance = selected_appearance()
        if image is None:
            appearance.update(background_x=0.0, background_y=0.0)
        if window.save(background=path, **appearance):
            for app_window in window.get_application().get_windows():
                app_window.workspace.set_wallpaper(image)
            break
    else:
        window.workspace.set_wallpaper(original_image, original_appearance)
    dialog.destroy()
