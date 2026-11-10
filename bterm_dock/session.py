"""Сохранение расположения вкладок между запусками."""

import hashlib
import json
import re

from . import settings


def ssh_profile_key(profile):
    fields = [profile[key] for key in ("host", "user", "port", "key")]
    return hashlib.sha256(json.dumps(fields).encode("utf-8")).hexdigest()


def read_session():
    path = settings.CONFIG.with_name("session.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("windows"), list):
        return []

    windows = []
    for window in data["windows"]:
        if not isinstance(window, dict) or not isinstance(window.get("tabs"), list):
            continue
        tabs = []
        selected = 0
        for index, tab in enumerate(window["tabs"]):
            if not isinstance(tab, dict):
                continue
            kind = tab.get("kind")
            if kind == "local":
                cwd = tab.get("cwd")
                saved = {"kind": kind, "cwd": cwd if isinstance(cwd, str) else ""}
            elif (kind == "ssh" and isinstance(tab.get("profile"), str)
                  and re.fullmatch(r"[a-f0-9]{64}", tab["profile"])):
                title = tab.get("title")
                saved = {"kind": kind, "profile": tab["profile"],
                         "title": title if isinstance(title, str) and title.isprintable() else "SSH-профиль"}
            else:
                continue
            if index == window.get("selected"):
                selected = len(tabs)
            tabs.append(saved)
        if tabs:
            windows.append({"tabs": tabs, "selected": selected})
    return windows


def write_session(windows):
    path = settings.CONFIG.with_name("session.json")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings.write_json(path, {"windows": windows})
