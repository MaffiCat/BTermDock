"""Общие элементы интерфейса."""

from ._gtk import Gdk, Gtk


def color(value):
    rgba = Gdk.RGBA()
    return rgba if rgba.parse(value) else None


def label(value, style=None, wrap=False):
    widget = Gtk.Label(label=value, xalign=0)
    widget.set_line_wrap(wrap)
    if style:
        widget.get_style_context().add_class(style)
    return widget


def icon_button(icon, tooltip, on_click):
    button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
    button.set_tooltip_text(tooltip)
    button.get_accessible().set_name(tooltip)
    button.connect("clicked", on_click)
    return button


def button(title, on_click, icon=None):
    widget = Gtk.Button(label=title)
    if icon:
        widget.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))
        widget.set_always_show_image(True)
    widget.connect("clicked", on_click)
    return widget
