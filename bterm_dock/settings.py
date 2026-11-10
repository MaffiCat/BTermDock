"""Чтение и атомарная запись пользовательских настроек."""

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from ._gtk import GLib
from .widgets import color

CONFIG = Path(GLib.get_user_config_dir()) / "zsh-dock" / "config.json"
PROFILE_FIELDS = ("name", "host", "user", "port", "key")
DEFAULT_THEME = "dark"
THEMES = ("dark", "light", "oled", "custom")
CUSTOM_THEME_FIELDS = ("base", "text", "accent")
CURSOR_SHAPES = ("system", "block", "ibeam", "underline")
MIN_SCROLLBACK = 1000
MAX_SCROLLBACK = 100000
MIN_BACKGROUND_ZOOM = 0.10
MAX_BACKGROUND_ZOOM = 3.0


def default_settings():
    return {
        "profiles": [],
        "background": "",
        "background_zoom": 1.0,
        "background_color": "",
        "background_x": 0.0,
        "background_y": 0.0,
        "dim": 0.72,
        "theme": DEFAULT_THEME,
        "sidebar_colors": {theme: "" for theme in THEMES},
        "custom_theme": {},
        "terminal_font": "",
        "scrollback_lines": 10000,
        "cursor_shape": "system",
    }


def read_settings():
    defaults = default_settings()
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return defaults
    if not isinstance(data, dict):
        raise ValueError("Ожидается JSON-объект с настройками.")
    settings = defaults | data
    profiles = settings["profiles"]
    if not isinstance(profiles, list) or any(
        not isinstance(profile, dict) or not isinstance(profile.get("icon", ""), str) or any(
            not isinstance(profile.get(field), str) for field in PROFILE_FIELDS
        ) for profile in profiles
    ):
        raise ValueError("Некорректный список SSH-профилей.")
    if (not isinstance(settings["background"], str)
            or not isinstance(settings["dim"], (float, int))
            or not 0.15 <= settings["dim"] <= 0.95):
        raise ValueError("Некорректные настройки фона.")
    zoom = settings["background_zoom"]
    if not isinstance(zoom, (int, float)) or not MIN_BACKGROUND_ZOOM <= zoom <= MAX_BACKGROUND_ZOOM:
        settings["background_zoom"] = 1.0
    fill = settings["background_color"]
    if not isinstance(fill, str) or (fill and color(fill) is None):
        settings["background_color"] = ""
    for key in ("background_x", "background_y"):
        offset = settings[key]
        if not isinstance(offset, (int, float)) or not -1 <= offset <= 1:
            settings[key] = 0.0
    if settings["theme"] not in THEMES:
        settings["theme"] = DEFAULT_THEME
    sidebar_colors = settings["sidebar_colors"]
    settings["sidebar_colors"] = defaults["sidebar_colors"].copy()
    if isinstance(sidebar_colors, dict):
        for theme in THEMES:
            value = sidebar_colors.get(theme, "")
            if isinstance(value, str) and value and color(value) is not None:
                settings["sidebar_colors"][theme] = value
    legacy_color = settings.pop("sidebar_color", "")
    if "sidebar_colors" not in data and isinstance(legacy_color, str) and legacy_color:
        rgba = color(legacy_color)
        if rgba is not None and not rgba.equal(color("#242424")):
            settings["sidebar_colors"]["dark"] = legacy_color
    custom = settings["custom_theme"]
    settings["custom_theme"] = {
        key: value for key in CUSTOM_THEME_FIELDS
        if isinstance(custom, dict) and isinstance(value := custom.get(key), str)
        and color(value) is not None
    }
    font = settings["terminal_font"]
    if not isinstance(font, str) or len(font) > 256 or not all(c.isprintable() for c in font):
        settings["terminal_font"] = ""
    lines = settings["scrollback_lines"]
    if type(lines) is not int or not MIN_SCROLLBACK <= lines <= MAX_SCROLLBACK:
        settings["scrollback_lines"] = defaults["scrollback_lines"]
    if settings["cursor_shape"] not in CURSOR_SHAPES:
        settings["cursor_shape"] = defaults["cursor_shape"]
    return settings


def write_settings(settings):
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json(CONFIG, settings)


def write_json(path, data):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                            prefix=".bterm-", delete=False) as temporary:
        temp_path = Path(temporary.name)
        try:
            json.dump(data, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.close()
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)
