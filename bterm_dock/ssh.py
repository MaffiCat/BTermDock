"""Проверка адресов и аргументы команд OpenSSH."""

import ipaddress
import os
import re
import shutil


def ssh_host(value):
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    if re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", value):
        return value
    try:
        ipaddress.IPv6Address(value)
    except ValueError:
        raise ValueError("Введите IP-адрес, имя хоста или алиас SSH.") from None
    _, separator, zone = value.partition("%")
    if separator and not re.fullmatch(r"[a-zA-Z0-9_.-]+", zone):
        raise ValueError("Некорректный идентификатор зоны IPv6.")
    return value


def ssh_address(profile):
    host = profile["host"]
    if ":" in host:
        host = f"[{host}]"
    user = f'{profile["user"]}@' if profile["user"] else ""
    port = f':{profile["port"]}' if profile["port"] else ""
    return f"{user}{host}{port}"


def validate_profile(profile):
    profile["host"] = ssh_host(profile["host"])
    port = profile["port"]
    if port and (not re.fullmatch(r"[0-9]{1,5}", port) or not 1 <= int(port) <= 65535):
        raise ValueError("Введите порт от 1 до 65535 или оставьте поле пустым.")
    if any(char.isspace() or not char.isprintable() for char in profile["user"]):
        raise ValueError("В логине не должно быть пробелов или управляющих символов.")
    profile["name"] = profile["name"] or profile["host"]


def hidden_profile_name(profile):
    name, host = profile["name"], profile["host"]
    if not name or name.casefold() in (host.casefold(), ssh_address(profile).casefold()):
        return "SSH-профиль"
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return name
    return re.sub(re.escape(host), "•••", name, flags=re.IGNORECASE)


def ssh_command(profile):
    executable = shutil.which("ssh")
    if not executable:
        raise ValueError("Для подключения установите openssh-client.")
    argv = [executable]
    for field, flag in (("user", "-l"), ("port", "-p"), ("key", "-i")):
        value = profile[field]
        if value:
            argv.extend((flag, os.path.expanduser(value) if field == "key" else value))
    return [*argv, "--", profile["host"]]
