"""Палитры оформления BTermDock и общий CSS GTK."""

from importlib.resources import files

from .widgets import color

PALETTES = {
    "dark": {
        "base": "#111214", "surface": "#17181b", "panel": "#242529",
        "border": "#35373d", "text": "#f0f0f3", "muted": "#abadb8",
        "accent": "#9cbcff", "accent_fill": "#292f40", "accent_text": "#e8eeff",
        "sidebar": "#151619",
    },
    "light": {
        "base": "#edf2f9", "surface": "#f7f9fd", "panel": "#ffffff",
        "border": "#cdd8ea", "text": "#1c283b", "muted": "#526681",
        "accent": "#275bad", "accent_fill": "#dce9ff", "accent_text": "#183f79",
        "sidebar": "#f0f4fb",
    },
    "oled": {
        "base": "#000000", "surface": "#000000", "panel": "#131313",
        "border": "#282828", "text": "#f1f1f3", "muted": "#a4a5ae",
        "accent": "#b8a8ff", "accent_fill": "#252039", "accent_text": "#f1eaff",
        "sidebar": "#000000",
    },
}

STYLE = files(__package__).joinpath("style.css").read_bytes()


def is_light(rgba):
    luminance = (0.2126 * rgba.red ** 2.2 + 0.7152 * rgba.green ** 2.2
                 + 0.0722 * rgba.blue ** 2.2)
    return luminance > 0.2


def sidebar_color(preferences, theme):
    rgba = color(preferences["sidebar_colors"][theme] or theme_palette(preferences, theme)["sidebar"])
    rgba.alpha = 1
    return rgba


def theme_palette(preferences, theme):
    if theme != "custom":
        return PALETTES[theme]
    base, text, accent = (color(preferences["custom_theme"].get(key, PALETTES["dark"][key]))
                          for key in ("base", "text", "accent"))

    def mix(first, second, weight):
        channels = (getattr(first, key) * (1 - weight) + getattr(second, key) * weight
                    for key in ("red", "green", "blue"))
        return "#{:02x}{:02x}{:02x}".format(*(round(channel * 255) for channel in channels))

    return {
        "base": mix(base, base, 0), "text": mix(text, text, 0), "accent": mix(accent, accent, 0),
        "surface": mix(base, text, .035), "panel": mix(base, text, .085),
        "border": mix(base, text, .19), "muted": mix(text, base, .28),
        "accent_fill": mix(base, accent, .20), "accent_text": mix(text, accent, .22),
        "sidebar": mix(base, text, .025),
    }


def stylesheet(preferences, palette):
    theme = preferences["theme"]
    sidebar = sidebar_color(preferences, theme)
    tokens = palette.copy()
    tokens.update(sidebar_bg=sidebar.to_string(),
                  sidebar_fg=("#182339" if is_light(sidebar) else "#f4f7fe")
                  if preferences["sidebar_colors"][theme] else palette["text"])
    definitions = "".join(f"@define-color dock_{name} {value};\n" for name, value in tokens.items())
    return definitions.encode("ascii") + STYLE
