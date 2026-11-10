"""Окно настроек оформления, терминала и фона."""

from ._gtk import Gtk
from .appearance import PALETTES, sidebar_color, theme_palette
from .background import edit_background
from .dialogs import DockColorButton, DockDialog, DockFontButton
from .settings import MAX_SCROLLBACK, MIN_SCROLLBACK
from .terminal import default_font
from .widgets import button, color, label


def settings_page(title, description):
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
    page.pack_start(label(title, "dialog-heading"), False, False, 0)
    page.pack_start(label(description, "muted", wrap=True), False, False, 0)
    return page


def setting_row(title, description, control):
    row = Gtk.Box(spacing=16, margin=14)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    text.pack_start(label(title, "setting-title"), False, False, 0)
    text.pack_start(label(description, "muted", wrap=True), False, False, 0)
    row.pack_start(text, True, True, 0)
    control.set_valign(Gtk.Align.CENTER)
    row.pack_end(control, False, False, 0)
    return row


def settings_card(*rows):
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    card.get_style_context().add_class("settings-card")
    for index, row in enumerate(rows):
        if index:
            card.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0)
        card.pack_start(row, False, False, 0)
    return card


def theme_button(theme, group, title, icon):
    control = Gtk.RadioButton.new_from_widget(group)
    control.set_mode(False)
    control.get_style_context().add_class("theme-choice")
    control.get_accessible().set_name(title)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
    sample = Gtk.Box(spacing=8, halign=Gtk.Align.FILL)
    sample.get_style_context().add_class("theme-sample")
    sample.get_style_context().add_class(f"theme-sample-{theme}")
    sample.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON), False, False, 0)
    sample.pack_start(label(">_", "mono-label"), False, False, 0)
    content.pack_start(sample, False, False, 0)
    content.pack_start(label(title, "setting-title"), False, False, 0)
    control.add(content)
    return control


def edit_preferences(window):
    dialog = DockDialog(window, "Настройки BTermDock")
    dialog.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Применить", Gtk.ResponseType.OK)
    dialog.get_widget_for_response(Gtk.ResponseType.OK).get_style_context().add_class("suggested-action")
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.set_default_size(720, 500)

    stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=140)
    navigation = Gtk.StackSidebar(stack=stack, width_request=160)
    navigation.get_style_context().add_class("settings-nav")
    layout = Gtk.Box()
    layout.pack_start(navigation, False, False, 0)
    layout.pack_start(stack, True, True, 0)
    dialog.get_content_area().pack_start(layout, True, True, 0)

    current = window.settings
    draft = {"theme": current["theme"], "sidebar_colors": current["sidebar_colors"].copy(),
             "custom_theme": current["custom_theme"].copy()}
    application = window.get_application()
    appearance = settings_page("Оформление", "Тема меняет окна, меню и панель вкладок.")
    choices = Gtk.Grid(column_spacing=10, row_spacing=10, column_homogeneous=True)
    light = theme_button("light", None, "Светлая", "weather-clear-symbolic")
    dark = theme_button("dark", light, "Тёмная", "weather-clear-night-symbolic")
    oled = theme_button("oled", light, "OLED", "video-display-symbolic")
    own = theme_button("custom", light, "Своя", "applications-graphics-symbolic")
    theme_buttons = {"light": light, "dark": dark, "oled": oled, "custom": own}
    for index, choice in enumerate(theme_buttons.values()):
        choice.set_hexpand(True)
        choices.attach(choice, index % 2, index // 2, 1, 1)
    theme_buttons[draft["theme"]].set_active(True)
    appearance.pack_start(choices, False, False, 0)
    custom = Gtk.CheckButton(label="Свой цвет")
    picker = DockColorButton()
    picker.set_title("Цвет SSH-панели")
    picker.get_accessible().set_name("Цвет SSH-панели")
    sidebar_controls = Gtk.Box(spacing=8)
    sidebar_controls.pack_start(custom, False, False, 0)
    sidebar_controls.pack_start(picker, False, False, 0)
    appearance.pack_start(settings_card(setting_row(
        "SSH-панель", "Отдельный цвет для выбранной темы.", sidebar_controls)), False, False, 0)
    stack.add_titled(appearance, "appearance", "Оформление")

    def sync_sidebar_controls():
        theme = draft["theme"]
        active = bool(draft["sidebar_colors"][theme])
        custom.handler_block(custom_handler)
        custom.set_active(active)
        custom.handler_unblock(custom_handler)
        picker.set_rgba(sidebar_color(draft, theme))
        picker.set_sensitive(active)

    def choose_theme(control, theme):
        if control.get_active():
            draft["theme"] = theme
            sync_sidebar_controls()
            application.update_appearance(draft)

    def update_sidebar(*_):
        theme = draft["theme"]
        active = custom.get_active()
        draft["sidebar_colors"][theme] = picker.get_rgba().to_string() if active else ""
        sync_sidebar_controls()
        application.update_appearance(draft)

    custom_handler = custom.connect("toggled", update_sidebar)
    picker.connect("color-set", update_sidebar)
    for theme, choice in theme_buttons.items():
        choice.connect("toggled", choose_theme, theme)
    sync_sidebar_controls()

    designer = settings_page("Своя тема", "Задайте основные цвета. Оттенки панелей и границ подберутся автоматически.")
    custom_colors = {}

    def activate_custom():
        if own.get_active():
            sync_sidebar_controls()
            application.update_appearance(draft)
        else:
            own.set_active(True)

    def update_custom(control, key):
        draft["custom_theme"][key] = control.get_rgba().to_string()
        activate_custom()

    custom_rows = []
    palette = theme_palette(draft, "custom")
    for key, title, description in (
        ("base", "Фон интерфейса", "Окна и поверхности приложения."),
        ("text", "Текст", "Подписи, заголовки и значки."),
        ("accent", "Акцент", "Выбранные элементы и кнопки действий."),
    ):
        control = DockColorButton(rgba=color(palette[key]))
        control.set_title(title)
        control.get_accessible().set_name(title)
        control.connect("color-set", update_custom, key)
        custom_colors[key] = control
        custom_rows.append(setting_row(title, description, control))
    designer.pack_start(settings_card(*custom_rows), False, False, 0)

    def reset_custom(_):
        draft["custom_theme"].clear()
        for key, control in custom_colors.items():
            control.set_rgba(color(PALETTES["dark"][key]))
        activate_custom()

    reset = button("Сбросить цвета", reset_custom)
    reset.set_halign(Gtk.Align.START)
    designer.pack_start(reset, False, False, 0)
    stack.add_titled(designer, "custom", "Своя тема")

    terminal = settings_page("Терминал", "Настройки текста и истории применяются к открытым вкладкам.")
    inherited_font = default_font(window.workspace.terminal_profile)
    use_default_font = Gtk.CheckButton(label="По умолчанию", active=not current["terminal_font"])
    font = DockFontButton(
        title="Шрифт терминала", font=current["terminal_font"] or inherited_font,
        use_font=True, use_size=False,
    )
    font.get_accessible().set_name("Шрифт терминала")
    font.set_sensitive(not use_default_font.get_active())
    use_default_font.connect("toggled", lambda control: font.set_sensitive(not control.get_active()))
    font_controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
    font_controls.pack_start(font, False, False, 0)
    font_controls.pack_start(use_default_font, False, False, 0)
    history = Gtk.SpinButton.new_with_range(MIN_SCROLLBACK, MAX_SCROLLBACK, 1000)
    history.set_value(current["scrollback_lines"])
    history.set_numeric(True)
    history.get_accessible().set_name("Строк в истории")
    cursor = Gtk.ComboBoxText()
    for key, title in (("system", "По умолчанию"), ("block", "Блок"),
                       ("ibeam", "Вертикальный"), ("underline", "Подчёркивание")):
        cursor.append(key, title)
    cursor.set_active_id(current["cursor_shape"])
    cursor.get_accessible().set_name("Форма курсора")
    terminal.pack_start(settings_card(
        setting_row("Шрифт", "Гарнитура и размер текста в терминале.", font_controls),
        setting_row("Строк в истории", "Глубина прокрутки каждой вкладки.", history),
        setting_row("Курсор", "Форма курсора ввода.", cursor),
    ), False, False, 0)
    stack.add_titled(terminal, "terminal", "Терминал")

    background = settings_page("Фон", "Изображение и цвет под текстом терминала.")
    editor = button("Открыть редактор", lambda _: edit_background(window, parent=dialog), "image-x-generic-symbolic")
    background.pack_start(settings_card(setting_row(
        "Фон терминала", "Выбор файла, положение и приглушение фото с живым превью.", editor)), False, False, 0)
    stack.add_titled(background, "background", "Фон")

    while dialog.run() == Gtk.ResponseType.OK:
        history.update()
        terminal_options = {"terminal_font": "" if use_default_font.get_active() else font.get_font(),
                            "scrollback_lines": history.get_value_as_int(),
                            "cursor_shape": cursor.get_active_id()}
        changed = any(current[key] != value for key, value in terminal_options.items())
        if window.save(**draft, **terminal_options):
            if changed:
                for app_window in application.get_windows():
                    for page in app_window.workspace.tabs.get_children():
                        app_window.workspace.configure_terminal(page.terminal)
            break
    dialog.destroy()
    application.update_appearance(current)
