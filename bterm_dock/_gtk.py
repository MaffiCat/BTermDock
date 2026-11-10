"""Загрузка GTK и VTE через системный PyGObject."""

import sys


try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Vte", "2.91")
    gi.require_version("PangoCairo", "1.0")
    gi.require_foreign("cairo")
    from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango, PangoCairo, Vte
except (ImportError, ValueError) as error:
    sys.exit(f"Не удалось загрузить GTK/VTE: {error}\n"
             "Запустите sh ./install.sh из каталога проекта для установки зависимостей.")
