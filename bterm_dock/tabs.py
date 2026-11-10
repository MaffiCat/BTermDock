"""Полоса вкладок терминала с компактными браузерными вкладками."""

from ._gtk import Gdk, GLib, Gtk, Pango
from .widgets import icon_button, label


class TabStrip(Gtk.ScrolledWindow):
    def __init__(self, notebook, on_new, on_close):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.EXTERNAL, vscrollbar_policy=Gtk.PolicyType.NEVER)
        self.get_style_context().add_class("chrome-tabs")
        self.notebook = notebook
        self.on_close = on_close
        self.active = None
        self.targets = [Gtk.TargetEntry.new("application/x-bterm-dock-tab", Gtk.TargetFlags.SAME_APP, 0)]
        self.row = Gtk.Box()
        self.add(self.row)
        self.plus = icon_button("list-add-symbolic", "Новая вкладка", lambda _: on_new())
        self.plus.get_style_context().add_class("chrome-plus")
        self.plus.set_valign(Gtk.Align.CENTER)
        self.row.pack_start(self.plus, False, False, 2)
        self.connect("size-allocate", lambda *_: self.size_tabs())
        self.connect("scroll-event", self.scroll_tabs)
        notebook.connect_after("page-added", lambda *_: self.size_tabs())
        notebook.connect_after("page-removed", self.remove_tab)
        notebook.connect_after("page-reordered", self.move_tab)
        notebook.connect_after("switch-page", self.select_tab)

    def add_tab(self, page, title, local):
        page.tab = tab = Gtk.EventBox(visible_window=False)
        tab.get_style_context().add_class("chrome-tab")
        content = Gtk.Box(margin_start=12, margin_end=10)
        select = Gtk.Button(relief=Gtk.ReliefStyle.NONE, focus_on_click=False)
        select.get_style_context().add_class("chrome-select")
        select.get_accessible().set_name(title)
        select.connect("clicked", lambda _: self.notebook.set_current_page(self.notebook.page_num(page)))
        select.tab_page = page
        item = Gtk.Box(spacing=7)
        icon = "utilities-terminal-symbolic" if local else "network-server-symbolic"
        item.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU), False, False, 0)
        caption = label(title)
        caption.set_ellipsize(Pango.EllipsizeMode.END)
        caption.connect("notify::label", lambda widget, _: select.get_accessible().set_name(widget.get_text()))
        item.pack_start(caption, True, True, 0)
        select.add(item)
        content.pack_start(select, True, True, 0)
        close = icon_button("window-close-symbolic", "Закрыть вкладку", lambda _: self.on_close(page))
        close.get_style_context().add_class("chrome-close")
        close.set_focus_on_click(False)
        close.set_valign(Gtk.Align.CENTER)
        content.pack_end(close, False, False, 0)
        select.connect("state-flags-changed", self.update_hover)
        close.connect("state-flags-changed", self.update_hover)
        tab.add(content)
        tab.set_tooltip_text(title)
        tab.connect("draw", self.draw_tab, page)
        self.row.pack_start(tab, False, False, 0)
        self.row.reorder_child(self.plus, -1)
        tab.show_all()
        self.row.queue_draw()
        select.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, self.targets, Gdk.DragAction.MOVE)
        select.drag_source_set_icon_name(icon)
        tab.drag_dest_set(Gtk.DestDefaults(0), self.targets, Gdk.DragAction.MOVE)
        tab.connect("drag-motion", self.drag_over)
        tab.connect("drag-drop", self.drop_tab, page)
        return caption

    def size_tabs(self):
        pages = self.notebook.get_children()
        width = max(96, min(184, (self.get_allocated_width() - 42) // max(1, len(pages))))
        for page in pages:
            if page.tab.get_size_request()[0] != width:
                page.tab.set_size_request(width, -1)

    def remove_tab(self, _, page, _index):
        page.tab.destroy()
        self.size_tabs()
        self.row.queue_draw()

    def move_tab(self, _, page, index):
        self.row.reorder_child(page.tab, index)
        self.row.queue_draw()

    def select_tab(self, _, page, _index):
        self.active = page
        self.row.queue_draw()
        GLib.idle_add(self.reveal_active, page)

    def reveal_active(self, page):
        if self.active is page and page.tab.get_parent() is self.row:
            adjustment = self.get_hadjustment()
            bounds = page.tab.get_allocation()
            adjustment.clamp_page(bounds.x, bounds.x + bounds.width)
        return GLib.SOURCE_REMOVE

    def scroll_tabs(self, _, event):
        adjustment = self.get_hadjustment()
        if adjustment.get_upper() <= adjustment.get_page_size():
            return False
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            _, dx, dy = event.get_scroll_deltas()
            distance = (dx or dy) * 45
        else:
            distance = -45 if event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT) else 45
        adjustment.set_value(adjustment.get_value() + distance)
        return True

    @staticmethod
    def update_hover(button, _previous):
        content = button.get_parent()
        tab = content.get_parent() if content else None
        if tab is None:
            return
        hovered = any(child.get_state_flags() & Gtk.StateFlags.PRELIGHT for child in content.get_children())
        style = tab.get_style_context()
        if hovered != style.has_class("hovered-tab"):
            if hovered:
                style.add_class("hovered-tab")
            else:
                style.remove_class("hovered-tab")
            tab.queue_draw()

    def draw_tab(self, tab, context, page):
        width, height = tab.get_allocated_width(), tab.get_allocated_height()
        style = tab.get_style_context()
        active = page is self.active
        hovered = style.has_class("hovered-tab")
        shade = "dock_panel" if active else "dock_tab_hover" if hovered else "dock_border"
        ok, rgba = style.lookup_color(shade)
        if not ok:
            return False
        Gdk.cairo_set_source_rgba(context, rgba)
        index = self.notebook.page_num(page)
        if active or hovered:
            left = 8 if index == 0 else 0
            right = width - 8 if index == self.notebook.get_n_pages() - 1 else width
            context.move_to(0, height)
            if left:
                context.curve_to(6, height, left, height - 4, left, height - 9)
            context.line_to(left, 10)
            context.curve_to(left, 4, left + 4, 0, left + 10, 0)
            context.line_to(right - 10, 0)
            context.curve_to(right - 4, 0, right, 4, right, 10)
            context.line_to(right, height - 9)
            context.curve_to(right, height - 4, min(right + 2, width), height, width, height)
            context.close_path()
        elif index + 1 != self.notebook.get_current_page():
            context.rectangle(width - 1, 8, 1, max(1, height - 16))
        context.fill()
        return False

    def drag_source(self, context):
        source = getattr(Gtk.drag_get_source_widget(context), "tab_page", None)
        return source if source is not None and self.notebook.page_num(source) >= 0 else None

    def drag_over(self, _, context, _x, _y, timestamp):
        if self.drag_source(context) is None:
            return False
        Gdk.drag_status(context, Gdk.DragAction.MOVE, timestamp)
        return True

    def drop_tab(self, tab, context, x, _y, timestamp, page):
        source = self.drag_source(context)
        if source is None:
            return False
        origin = self.notebook.page_num(source)
        target = self.notebook.page_num(page) + int(x >= tab.get_allocated_width() / 2)
        if origin < target:
            target -= 1
        if origin != target:
            self.notebook.reorder_child(source, target)
        Gtk.drag_finish(context, True, False, timestamp)
        return True
