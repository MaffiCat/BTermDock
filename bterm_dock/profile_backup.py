"""Перенос SSH-профилей между установками BTermDock."""

import base64
import hashlib
import json
from pathlib import Path
import struct

from . import settings
from ._gtk import GdkPixbuf, Gio, GLib, Gtk
from .images import PROFILE_ICON_SIZE, load_profile_icon
from .ssh import validate_profile
from .widgets import label

BACKUP_FORMAT = "btermdock-ssh-profiles"
BACKUP_VERSION = 1
MAX_BACKUP_BYTES = 32 * 1024 * 1024
MAX_ICON_BYTES = 32 * 1024
PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"


def write_backup(path, profiles):
    if path.resolve() == settings.CONFIG.resolve():
        raise ValueError("Выберите отдельный файл для резервной копии.")
    records = []
    for profile in profiles:
        record = {field: profile[field] for field in settings.PROFILE_FIELDS}
        if icon := profile.get("icon"):
            try:
                saved, image = load_profile_icon(icon).save_to_bufferv("png", [], [])
            except GLib.Error:
                pass
            else:
                if saved:
                    record["icon_png"] = base64.b64encode(bytes(image)).decode("ascii")
        records.append(record)
    settings.write_json(path, {"format": BACKUP_FORMAT, "version": BACKUP_VERSION,
                               "profiles": records})


def read_icon(encoded):
    if not isinstance(encoded, str) or len(encoded) > (MAX_ICON_BYTES + 2) // 3 * 4:
        raise ValueError("Некорректный значок профиля.")
    try:
        image = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise ValueError("Некорректный значок профиля.") from None
    if len(image) < 24 or len(image) > MAX_ICON_BYTES or not image.startswith(PNG_HEADER):
        raise ValueError("Некорректный значок профиля.")
    width, height = struct.unpack_from(">II", image, 16)
    if not all(1 <= size <= 2 * PROFILE_ICON_SIZE for size in (width, height)):
        raise ValueError("Некорректный размер значка профиля.")
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(image))
    try:
        GdkPixbuf.Pixbuf.new_from_stream(stream, None)
    except GLib.Error:
        raise ValueError("Не удалось прочитать значок профиля.") from None
    return image


def read_backup(path):
    if not path.is_file():
        raise ValueError("Выберите файл резервной копии JSON.")
    with path.open("rb") as source:
        content = source.read(MAX_BACKUP_BYTES + 1)
    if len(content) > MAX_BACKUP_BYTES:
        raise ValueError("Файл резервной копии превышает 32 МБ.")
    try:
        backup = json.loads(content)
    except (ValueError, RecursionError):
        raise ValueError("Не удалось прочитать файл JSON.") from None
    if (not isinstance(backup, dict) or backup.get("format") != BACKUP_FORMAT
            or type(backup.get("version")) is not int or backup["version"] != BACKUP_VERSION
            or not isinstance(backup.get("profiles"), list)):
        raise ValueError("Выберите резервную копию SSH-профилей BTermDock версии 1.")
    profiles = []
    for index, record in enumerate(backup["profiles"], 1):
        try:
            if not isinstance(record, dict) or any(
                not isinstance(record.get(field), str) or "\x00" in record[field]
                for field in settings.PROFILE_FIELDS
            ):
                raise ValueError("Некорректные поля профиля.")
            profile = {field: record[field] for field in settings.PROFILE_FIELDS}
            for value in profile.values():
                value.encode("utf-8")
            validate_profile(profile)
            icon = read_icon(record["icon_png"]) if "icon_png" in record else None
        except UnicodeError:
            raise ValueError(f"Профиль №{index} содержит повреждённый текст.") from None
        except ValueError as error:
            raise ValueError(f"Профиль №{index}. {error}") from None
        profiles.append((profile, icon))
    return profiles


def add_profiles(current, imported):
    def identity(profile):
        return tuple(profile[field] for field in settings.PROFILE_FIELDS)

    profiles = current.copy()
    seen = {identity(profile) for profile in current}
    for profile, icon in imported:
        key = identity(profile)
        if key not in seen:
            profiles.append(store_icon(profile, icon))
            seen.add(key)
    return profiles


def store_icon(profile, image):
    if image is None:
        return profile
    folder = settings.CONFIG.parent / "ssh-icons"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / (hashlib.sha256(image).hexdigest() + ".png")
    if not path.exists():
        GLib.file_set_contents_full(str(path), image, GLib.FileSetContentsFlags.CONSISTENT, 0o600)
    return dict(profile, icon=str(path))


def choose_backup(window, action):
    saving = action == Gtk.FileChooserAction.SAVE
    title = "Экспорт SSH-профилей" if saving else "Импорт SSH-профилей"
    chooser = Gtk.FileChooserDialog(title=title, action=action, transient_for=window,
                                    modal=True, destroy_with_parent=True)
    chooser.add_buttons("Отмена", Gtk.ResponseType.CANCEL,
                        "Сохранить" if saving else "Открыть", Gtk.ResponseType.ACCEPT)
    chooser.set_default_response(Gtk.ResponseType.ACCEPT)
    formats = Gtk.FileFilter()
    formats.set_name("Файлы JSON")
    formats.add_pattern("*.[jJ][sS][oO][nN]")
    chooser.add_filter(formats)
    if saving:
        chooser.set_current_name("btermdock-ssh-profiles.json")
        chooser.set_do_overwrite_confirmation(True)
        detail = label("Резервная копия содержит адреса и параметры подключений.", wrap=True)
        detail.show()
        chooser.set_extra_widget(detail)
    else:
        all_files = Gtk.FileFilter()
        all_files.set_name("Все файлы")
        all_files.add_pattern("*")
        chooser.add_filter(all_files)
    try:
        if chooser.run() == Gtk.ResponseType.ACCEPT:
            if filename := chooser.get_filename():
                return Path(filename)
    finally:
        chooser.destroy()
    return None


def export_dialog(window):
    path = choose_backup(window, Gtk.FileChooserAction.SAVE)
    if path is None:
        return
    try:
        write_backup(path, window.settings["profiles"])
    except (OSError, ValueError) as error:
        window.show_error("Экспорт не удался", str(error))


def import_dialog(window):
    path = choose_backup(window, Gtk.FileChooserAction.OPEN)
    if path is None:
        return False
    try:
        imported = read_backup(path)
        current = window.settings["profiles"]
        response = Gtk.ResponseType.APPLY
        if current:
            dialog = Gtk.MessageDialog(transient_for=window, modal=True,
                                       message_type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.CANCEL,
                                       text=f"Профилей в резервной копии — {len(imported)}")
            dialog.format_secondary_text("При добавлении совпадающие профили пропускаются. "
                                         "Замена восстанавливает список целиком.")
            dialog.add_buttons("Заменить список", Gtk.ResponseType.APPLY,
                               "Добавить", Gtk.ResponseType.OK)
            dialog.set_default_response(Gtk.ResponseType.OK)
            response = dialog.run()
            dialog.destroy()
        if response == Gtk.ResponseType.OK:
            profiles = add_profiles(current, imported)
        elif response == Gtk.ResponseType.APPLY:
            profiles = [store_icon(profile, image) for profile, image in imported]
        else:
            return False
        return profiles != current and window.save(profiles=profiles)
    except (OSError, ValueError, GLib.Error) as error:
        window.show_error("Импорт не удался", str(error))
        return False
