#!/bin/sh
set -eu

case "${1-}" in
    -h|--help)
        printf '%s\n' 'sh ./install.sh [--skip-deps]' \
            'Устанавливает BTermDock для текущего пользователя.' \
            '--skip-deps  Использовать уже установленные системные зависимости.'
        exit 0
        ;;
    ''|--skip-deps) ;;
    *) printf '%s\n' 'Использование: sh ./install.sh [--skip-deps]' >&2; exit 2 ;;
esac

if [ "$#" -gt 1 ]; then
    printf '%s\n' 'Использование: sh ./install.sh [--skip-deps]' >&2
    exit 2
fi

if [ "$(uname -s)" != Linux ] || [ "$(id -u)" -eq 0 ]; then
    printf '%s\n' 'Запустите установщик от обычного пользователя Linux, без sudo.' >&2
    exit 1
fi

cd -- "$(dirname -- "$0")"

system_python() {
    if [ -n "${BTERM_PYTHON:-}" ]; then
        command -v "$BTERM_PYTHON"
    elif [ -x /usr/bin/python3 ]; then
        printf '%s\n' /usr/bin/python3
    else
        command -v python3
    fi
}

check_dependencies() {
    python=$(system_python) || return 1
    command -v ssh >/dev/null || return 1
    "$python" -B -c '
import ctypes
import sys

if sys.version_info < (3, 9):
    sys.exit("Нужен Python 3.9 или новее.")

from bterm_dock._gtk import Vte

try:
    ctypes.CDLL("libpam.so.0")
except OSError:
    sys.exit("Не найдена системная библиотека Linux-PAM.")
if not hasattr(Vte.Terminal, "set_clear_background"):
    sys.exit("Установите более новую версию VTE для GTK 3.")
'
}

as_admin() {
    if command -v sudo >/dev/null; then
        sudo "$@"
    elif command -v doas >/dev/null; then
        doas "$@"
    else
        printf '%s\n' 'Установите зависимости с правами администратора и запустите установщик с --skip-deps.' >&2
        exit 1
    fi
}

install_dependencies() {
    if [ -r /etc/os-release ]; then
        . /etc/os-release
    elif [ -r /usr/lib/os-release ]; then
        . /usr/lib/os-release
    fi
    if [ -e /run/ostree-booted ]; then
        printf '%s\n' 'Установите зависимости средствами вашего дистрибутива и повторите запуск с --skip-deps.' >&2
        exit 1
    fi

    # ID предшествует родственным дистрибутивам из ID_LIKE.
    for distro in ${ID:-} ${ID_LIKE:-}; do
        case "$distro" in
            arch|archlinux|manjaro|endeavouros|garuda|cachyos|artix)
                as_admin pacman -S --needed python python-gobject python-cairo gtk3 vte3 pam pambase openssh adwaita-icon-theme
                return
                ;;
            debian|ubuntu|linuxmint|pop|neon)
                as_admin apt-get update
                as_admin apt-get install python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-vte-2.91 libpam0g openssh-client adwaita-icon-theme
                return
                ;;
            fedora|rhel|centos|rocky|almalinux)
                as_admin dnf install python3 python3-gobject python3-cairo gtk3 vte291 pam openssh-clients adwaita-icon-theme
                return
                ;;
            opensuse*|suse|sles)
                as_admin zypper install python3 python3-gobject python3-gobject-Gdk python3-gobject-cairo python3-cairo typelib-1_0-Gtk-3_0 typelib-1_0-Vte-2_91 pam openssh adwaita-icon-theme
                return
                ;;
            alpine)
                as_admin apk add python3 py3-gobject3 py3-cairo gtk+3.0 vte3 linux-pam openssh-client-default adwaita-icon-theme
                return
                ;;
            void)
                as_admin xbps-install -S python3 python3-gobject python3-cairo gtk+3 vte3 pam openssh adwaita-icon-theme
                return
                ;;
        esac
    done
    printf '%s\n' 'Установите Python 3, PyGObject, Pycairo, GTK 3, VTE 2.91, Linux-PAM и клиент OpenSSH.' \
        'Затем повторите запуск с --skip-deps.' >&2
    exit 1
}

if ! check_dependencies >/dev/null 2>&1; then
    if [ "${1-}" != --skip-deps ]; then
        install_dependencies
    fi
    if ! check_dependencies; then
        printf '%s\n' 'Системные зависимости BTermDock недоступны. См. раздел «Установка» в README.md.' >&2
        exit 1
    fi
fi

"$python" -B - <<'PY'
from pathlib import Path
import os
import re
import shutil
import sys
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile

from gi.repository import GLib

interpreter = os.fsencode(sys.executable)
if not interpreter.startswith(b"/") or any(char in interpreter for char in b" \t\r\n"):
    sys.exit("Укажите системный Python по абсолютному пути без пробелов в BTERM_PYTHON.")

home = Path.home()
data_home = Path(os.environ.get("XDG_DATA_HOME", ""))
if not data_home.is_absolute():
    data_home = home / ".local" / "share"
launcher = home / ".local" / "bin" / "bterm-dock"
desktop_file = data_home / "applications" / "local.BTermDock.desktop"
icon = data_home / "icons" / "hicolor" / "256x256" / "apps" / "local.BTermDock.png"
for path in (launcher, desktop_file, icon):
    path.parent.mkdir(parents=True, exist_ok=True)

with NamedTemporaryFile(dir=launcher.parent, prefix=".bterm-dock-", delete=False) as temporary:
    staging = Path(temporary.name)
    try:
        temporary.write(b"#!" + interpreter + b"\n")
        temporary.flush()
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as app:
            app.write("bterm-dock.py", "__main__.py")
            for path in sorted(Path("bterm_dock").glob("*.py")):
                app.write(path)
            app.write("bterm_dock/style.css")
        staging.chmod(0o755)
        staging.replace(launcher)
    finally:
        staging.unlink(missing_ok=True)

shutil.copyfile("BTermDock-icon.png", icon)
desktop = GLib.KeyFile()
exec_path = re.sub(r'(["`$\\])', r'\\\1', str(launcher)).replace("%", "%%")
for key, value in {
    "Type": "Application",
    "Name": "BTermDock",
    "Comment": "Терминал с вкладками и SSH-профилями",
    "Exec": f'"{exec_path}"',
    "Icon": "local.BTermDock",
    "Terminal": "false",
    "Categories": "System;TerminalEmulator;",
    "StartupNotify": "true",
    "StartupWMClass": "bterm-dock",
}.items():
    desktop.set_string("Desktop Entry", key, value)
desktop.save_to_file(str(desktop_file))
print(f"Готово. Откройте BTermDock в меню приложений или запустите {launcher}")
PY
