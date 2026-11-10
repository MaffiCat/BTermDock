"""Перемещаемые диалоги внутри главного окна."""

from math import exp

from ._gtk import Atk, Gdk, GLib, Gtk, Pango
from .widgets import label

DIALOG_MARGIN = 12
DRAG_SMOOTHING_US = 12_000


class DockDialog(Gtk.EventBox):
    def __init__(self, parent, title):
        super().__init__()
        self.window = parent.window if isinstance(parent, DockDialog) else parent
        self.get_accessible().set_name(title)
        self.get_accessible().set_role(Atk.Role.DIALOG)
        self.get_style_context().add_class("dock-dialog")
        self.get_style_context().add_class("dock-dialog-panel")
        self.layer = Gtk.EventBox()
        self.overlay = Gtk.Overlay()
        self.layer.add(self.overlay)
        scrim = Gtk.EventBox()
        scrim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        scrim.get_style_context().add_class("dock-dialog-scrim")
        self.overlay.add(scrim)
        self.overlay.add_overlay(self)
        self.overlay.connect("get-child-position", self.place)
        self.layer.connect("button-press-event", self.click_outside)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(panel)
        self.header = Gtk.HeaderBar()
        self.header.get_style_context().add_class("dock-dialog-header")
        heading = Gtk.Label(label=title, ellipsize=Pango.EllipsizeMode.END)
        heading.get_style_context().add_class("title")
        self.header.set_custom_title(heading)
        panel.pack_start(self.header, False, False, 0)
        scroll = Gtk.ScrolledWindow(propagate_natural_width=True, propagate_natural_height=True)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll.add(self.content)
        panel.pack_start(scroll, True, True, 0)

        self.buttons = {}
        self.default = None
        self.initial_focus = None
        self.default_size = (0, 0)
        self.position = self.drag_origin = self.drag_target = None
        self.drag_tick = 0
        self.drag_frame_time = None
        self.loop = None
        self.previous_dialog = None
        self.reply = Gtk.ResponseType.NONE
        # Keep the drag coordinates relative to the stationary layer as the panel moves.
        self.drag = Gtk.GestureDrag.new(self.layer)
        self.drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.drag.connect("drag-begin", self.start_drag)
        self.drag.connect("drag-update", self.move_dialog)
        self.drag.connect("drag-end", self.finish_drag)
        self.connect_after("key-press-event", self.close_on_escape)
        self.connect("destroy", self.detach)

    def get_content_area(self):
        return self.content

    def set_default_size(self, width, height):
        self.default_size = (width, height)

    def add_buttons(self, *pairs):
        for caption, response in zip(pairs[::2], pairs[1::2]):
            control = Gtk.Button(label=caption, can_default=True)
            control.connect("clicked", lambda _, result=response: self.response(result))
            self.buttons[response] = control
            if response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.CLOSE):
                self.header.pack_start(control)
            else:
                self.header.pack_end(control)

    def get_widget_for_response(self, response):
        return self.buttons[response]

    def set_response_sensitive(self, response, sensitive):
        self.buttons[response].set_sensitive(sensitive)

    def set_default_response(self, response):
        self.default = self.buttons[response]

    def response(self, response):
        self.reply = response
        if self.loop is not None:
            self.loop.quit()

    def run(self):
        self.reply = Gtk.ResponseType.NONE
        self.loop = GLib.MainLoop()
        if self.layer.get_parent() is None:
            self.previous_dialog = self.window.active_dialog
            self.previous_focus = self.window.get_focus()
            self.previous_default = self.window.get_default_widget()
            self.window.active_dialog = self
            self.set_underlay_sensitive(False)
            self.window.dialog_host.add_overlay(self.layer)
            self.layer.show_all()
            self.layer.grab_add()
            self.window.set_default(self.default)
            if self.initial_focus is not None:
                self.initial_focus.grab_focus()
            else:
                self.child_focus(Gtk.DirectionType.TAB_FORWARD)
        try:
            if self.reply == Gtk.ResponseType.NONE:
                self.loop.run()
            return self.reply
        finally:
            self.loop = None

    def set_underlay_sensitive(self, sensitive):
        if self.previous_dialog is not None:
            self.previous_dialog.set_sensitive(sensitive)
        else:
            self.window.dialog_host.get_child().set_sensitive(sensitive)
            self.window.header.set_sensitive(sensitive)

    def detach(self, _):
        self.stop_drag_animation()
        if self.loop is not None:
            self.reply = Gtk.ResponseType.CANCEL
            self.loop.quit()
        if self.layer.has_grab():
            self.layer.grab_remove()
        if self.window.active_dialog is self:
            if self.window.in_destruction():
                self.window.active_dialog = None
            else:
                self.window.active_dialog = self.previous_dialog
                self.set_underlay_sensitive(True)
                self.window.set_default(self.previous_default)
                if self.previous_focus is not None and self.previous_focus.get_mapped():
                    self.previous_focus.grab_focus()
        if not self.layer.in_destruction():
            self.layer.destroy()

    def click_outside(self, _, event):
        target = Gtk.get_event_widget(event)
        if target is self or (target is not None and target.is_ancestor(self)):
            return False
        self.response(Gtk.ResponseType.CANCEL)
        return True

    def close_on_escape(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.response(Gtk.ResponseType.CANCEL)
            return True
        return False

    def place(self, overlay, _, rect):
        room_width = overlay.get_allocated_width()
        room_height = overlay.get_allocated_height()
        _, natural_width = self.get_preferred_width()
        width = min(max(1, room_width - 2 * DIALOG_MARGIN), max(natural_width, self.default_size[0]))
        _, natural_height = self.get_preferred_height_for_width(width)
        height = min(max(1, room_height - 2 * DIALOG_MARGIN), max(natural_height, self.default_size[1]))
        x, y = self.position or ((room_width - width) // 2, (room_height - height) // 2)
        x, y = self.clamp_position(x, y, width, height)
        rect.x, rect.y = round(x), round(y)
        rect.width, rect.height = width, height
        return True

    def clamp_position(self, x, y, width, height):
        right = self.overlay.get_allocated_width() - width - DIALOG_MARGIN
        bottom = self.overlay.get_allocated_height() - height - DIALOG_MARGIN
        return min(max(DIALOG_MARGIN, x), max(0, right)), min(max(DIALOG_MARGIN, y), max(0, bottom))

    def contains(self, widget, x, y):
        coordinates = widget.translate_coordinates(self.layer, 0, 0)
        if coordinates is None:
            return False
        left, top = coordinates
        return (left <= x < left + widget.get_allocated_width()
                and top <= y < top + widget.get_allocated_height())

    def start_drag(self, gesture, x, y):
        if (not self.contains(self.header, x, y)
                or any(self.contains(control, x, y) for control in self.buttons.values())):
            self.drag_origin = None
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self.stop_drag_animation()
        self.position = self.drag_origin = self.translate_coordinates(self.layer, 0, 0)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def move_dialog(self, _, dx, dy):
        if self.drag_origin is None:
            return
        x, y = self.drag_origin
        width, height = self.get_allocated_width(), self.get_allocated_height()
        self.drag_target = self.clamp_position(x + dx, y + dy, width, height)
        if not self.drag_tick and self.drag_target != self.position:
            self.drag_tick = self.add_tick_callback(self.animate_drag)

    def finish_drag(self, gesture, dx, dy):
        self.move_dialog(gesture, dx, dy)
        self.drag_origin = None

    def animate_drag(self, _, clock):
        frame_time = clock.get_frame_time()
        if self.drag_frame_time is None:
            elapsed, _ = clock.get_refresh_info(frame_time)
        else:
            elapsed = frame_time - self.drag_frame_time
        self.drag_frame_time = frame_time
        fraction = 1 - exp(-elapsed / DRAG_SMOOTHING_US)
        x, y = self.position
        target_x, target_y = self.drag_target
        next_x = x + (target_x - x) * fraction
        next_y = y + (target_y - y) * fraction
        if max(abs(target_x - next_x), abs(target_y - next_y)) <= 0.5:
            next_x, next_y = self.drag_target
        self.position = next_x, next_y
        if (round(x), round(y)) != (round(next_x), round(next_y)):
            self.overlay.queue_allocate()
        if self.position == self.drag_target:
            self.drag_tick = 0
            self.drag_frame_time = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def stop_drag_animation(self):
        if self.drag_tick:
            self.remove_tick_callback(self.drag_tick)
            self.drag_tick = 0
            self.drag_frame_time = None


def message_dialog(window, title, detail):
    dialog = DockDialog(window, title)
    dialog.set_default_size(420, -1)
    text = label(detail, wrap=True)
    text.set_margin_start(20)
    text.set_margin_end(20)
    text.set_margin_top(20)
    text.set_margin_bottom(20)
    dialog.get_content_area().add(text)
    return dialog


class DockColorButton(Gtk.ColorButton):
    def do_clicked(self):
        dialog = DockDialog(self.get_toplevel(), self.get_title())
        dialog.get_style_context().add_class("background-color-chooser")
        dialog.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Выбрать", Gtk.ResponseType.OK)
        dialog.get_widget_for_response(Gtk.ResponseType.OK).get_style_context().add_class("suggested-action")
        dialog.set_default_response(Gtk.ResponseType.OK)

        chooser = Gtk.ColorChooserWidget(use_alpha=self.get_use_alpha())
        chooser.set_rgba(self.get_rgba())
        chooser.props.show_editor = self.props.show_editor
        chooser.connect("color-activated", lambda *_: dialog.response(Gtk.ResponseType.OK))
        toggle = Gtk.Button(halign=Gtk.Align.START, margin=8)

        def update_toggle(*_):
            toggle.set_label("Показать палитру" if chooser.props.show_editor else "Свернуть палитру")

        def toggle_palette(_):
            rgba = chooser.get_rgba()
            chooser.props.show_editor = not chooser.props.show_editor
            if not chooser.get_rgba().equal(rgba):
                chooser.set_rgba(rgba)

        update_toggle()
        chooser.connect("notify::show-editor", update_toggle)
        toggle.connect("clicked", toggle_palette)
        dialog.content.pack_start(toggle, False, False, 0)
        dialog.content.pack_start(chooser, True, True, 8)
        if dialog.run() == Gtk.ResponseType.OK:
            rgba = chooser.get_rgba()
            chooser.set_rgba(rgba)
            self.set_rgba(rgba)
            self.emit("color-set")
        dialog.destroy()


class DockFontButton(Gtk.FontButton):
    def do_clicked(self):
        dialog = DockDialog(self.get_toplevel(), self.get_title())
        dialog.set_default_size(560, 420)
        dialog.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Выбрать", Gtk.ResponseType.OK)
        dialog.get_widget_for_response(Gtk.ResponseType.OK).get_style_context().add_class("suggested-action")
        dialog.set_default_response(Gtk.ResponseType.OK)
        chooser = Gtk.FontChooserWidget(margin=8)
        chooser.set_font(self.get_font())
        chooser.connect("font-activated", lambda *_: dialog.response(Gtk.ResponseType.OK))
        dialog.content.pack_start(chooser, True, True, 0)
        if dialog.run() == Gtk.ResponseType.OK and (font := chooser.get_font()):
            self.set_font(font)
            self.emit("font-set")
        dialog.destroy()
