"""Превью перестановки строк SSH во время перетаскивания GTK."""

from math import exp

import cairo

from ._gtk import Gtk


class ReorderPreview(Gtk.DrawingArea):
    def __init__(self, listbox):
        super().__init__(no_show_all=True)
        self.listbox = listbox
        self.source = None
        self.tick = 0
        self.get_style_context().add_class("reorder-preview")
        self.connect("draw", self.draw_rows)

    def begin(self, row, context, hotspot):
        width, height = row.get_allocated_width(), row.get_allocated_height()
        scale = row.get_scale_factor()
        image = cairo.ImageSurface(cairo.FORMAT_ARGB32, width * scale, height * scale)
        image.set_device_scale(scale, scale)
        style = row.get_style_context()
        style.add_class("drag-card")
        row.draw(cairo.Context(image))
        style.remove_class("drag-card")
        x, y = hotspot or (width // 2, height // 2)
        image.set_device_offset(-x * scale, -y * scale)
        Gtk.drag_set_icon_surface(context, image)

        self.rows = self.listbox.get_children()
        self.source = row
        self.source_index = row.get_index()
        self.slot = self.source_index
        self.offsets = [0.0] * len(self.rows)
        self.targets = self.offsets.copy()
        spacing = 0
        if len(self.rows) > 1:
            first, second = self.rows[:2]
            spacing = self.row_origin(second)[1] - self.row_origin(first)[1] - first.get_allocated_height()
        self.stride = height + spacing
        self.show()
        self.queue_draw()

    def row_origin(self, row):
        return row.translate_coordinates(self.listbox, 0, 0)[-2:]

    def move_to(self, slot):
        if self.source is None or self.slot == slot:
            return
        self.slot = slot
        source = self.source_index
        self.targets = [self.stride if slot <= index < source else
                        -self.stride if source < index < slot else 0
                        for index in range(len(self.rows))]
        if not Gtk.Settings.get_default().get_property("gtk-enable-animations"):
            self.offsets = self.targets.copy()
            self.queue_draw()
        elif not self.tick:
            self.last_frame = 0
            self.tick = self.add_tick_callback(self.animate)

    def animate(self, _widget, frame_clock):
        now = frame_clock.get_frame_time()
        elapsed = now - self.last_frame if self.last_frame else 16000
        self.last_frame = now
        step = 1 - exp(-elapsed / 52000)
        for index, target in enumerate(self.targets):
            distance = target - self.offsets[index]
            self.offsets[index] += distance if abs(distance) < 0.5 else step * distance
        self.queue_draw()
        if self.offsets == self.targets:
            self.tick = 0
            return False
        return True

    def draw_rows(self, _, context):
        if self.source is None:
            return False
        origin = self.listbox.translate_coordinates(self, 0, 0)
        if not origin:
            return False
        left, top = origin[-2:]
        height = self.get_allocated_height()
        width = self.listbox.get_allocated_width()
        context.save()
        context.rectangle(left, 0, width, height)
        context.clip()
        Gtk.render_background(self.get_style_context(), context, left, 0, width, height)
        for row, offset in zip(self.rows, self.offsets):
            if row is self.source:
                continue
            x, y = self.row_origin(row)
            y += top + round(offset)
            if y < height and y + row.get_allocated_height() > 0:
                context.save()
                context.translate(left + x, y)
                row.draw(context)
                context.restore()
        context.restore()
        return False

    def reset_slot(self):
        if self.source is not None:
            self.move_to(self.source_index)

    def end(self):
        if self.source is None:
            return
        if self.tick:
            self.remove_tick_callback(self.tick)
            self.tick = 0
        self.source = None
        self.rows = []
        self.hide()
