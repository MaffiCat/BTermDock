"""Подтверждение пароля текущей учётной записи через Linux-PAM."""

import ctypes as c
import os
from pathlib import Path
import pwd
from threading import Thread

from ._gtk import GLib, Gtk, Pango
from .dialogs import DockDialog
from .widgets import label

PAM_SUCCESS = 0
PAM_BUF_ERR = 5
PAM_CONV_ERR = 19
PAM_USER = 2
PAM_PROMPT_ECHO_OFF = 1
PAM_ERROR_MSG = 3
PAM_TEXT_INFO = 4
PAM_DISALLOW_NULL_AUTHTOK = 1
PAM_MAX_NUM_MSG = 32


class PamMessage(c.Structure):
    _fields_ = [("msg_style", c.c_int), ("msg", c.c_char_p)]


class PamResponse(c.Structure):
    _fields_ = [("resp", c.c_void_p), ("resp_retcode", c.c_int)]


Conversation = c.CFUNCTYPE(c.c_int, c.c_int, c.POINTER(c.POINTER(PamMessage)),
                           c.POINTER(c.POINTER(PamResponse)), c.c_void_p)


class PamConversation(c.Structure):
    _fields_ = [("conv", Conversation), ("appdata_ptr", c.c_void_p)]


def password_service():
    for name in ("common-auth", "system-auth", "base-auth", "login"):
        if any((Path(directory) / name).is_file()
               for directory in ("/etc/pam.d", "/usr/lib/pam.d", "/usr/share/pam/pam.d")):
            return name.encode("ascii")
    raise OSError("Системный профиль проверки пароля PAM не найден.")


def verify_local_password(password):
    if not password or 0 in password:
        return False
    username = os.fsencode(pwd.getpwuid(os.getuid()).pw_name)
    pam = c.CDLL("libpam.so.0")
    libc = c.CDLL(None)
    libc.calloc.argtypes = (c.c_size_t, c.c_size_t)
    libc.calloc.restype = c.c_void_p
    libc.free.argtypes = (c.c_void_p,)
    libc.free.restype = None
    pam.pam_start.argtypes = (c.c_char_p, c.c_char_p, c.POINTER(PamConversation),
                              c.POINTER(c.c_void_p))
    pam.pam_authenticate.argtypes = (c.c_void_p, c.c_int)
    pam.pam_get_item.argtypes = (c.c_void_p, c.c_int, c.POINTER(c.c_char_p))
    pam.pam_end.argtypes = (c.c_void_p, c.c_int)
    supplied_password = False

    @Conversation
    def respond(count, messages, response, _):
        nonlocal supplied_password
        if not 1 <= count <= PAM_MAX_NUM_MSG:
            return PAM_CONV_ERR
        password_index = None
        for index in range(count):
            style = messages[index].contents.msg_style
            if style in (PAM_ERROR_MSG, PAM_TEXT_INFO):
                continue
            if style != PAM_PROMPT_ECHO_OFF or supplied_password or password_index is not None:
                return PAM_CONV_ERR
            password_index = index
        buffer = libc.calloc(count, c.sizeof(PamResponse))
        if not buffer:
            return PAM_BUF_ERR
        replies = c.cast(buffer, c.POINTER(PamResponse))
        if password_index is not None:
            secret = libc.calloc(len(password) + 1, 1)
            if not secret:
                libc.free(buffer)
                return PAM_BUF_ERR
            c.memmove(secret, (c.c_char * len(password)).from_buffer(password), len(password))
            replies[password_index].resp = secret
            supplied_password = True
        response[0] = replies  # PAM освобождает переданные ответы.
        return PAM_SUCCESS

    handle = c.c_void_p()
    conversation = PamConversation(respond, None)
    status = pam.pam_start(password_service(), username, c.byref(conversation), c.byref(handle))
    if status != PAM_SUCCESS:
        raise OSError("Не удалось начать проверку PAM.")
    try:
        status = pam.pam_authenticate(handle, PAM_DISALLOW_NULL_AUTHTOK)
        if status != PAM_SUCCESS or not supplied_password:
            return False
        account = c.c_char_p()
        status = pam.pam_get_item(handle, PAM_USER, c.byref(account))
        return status == PAM_SUCCESS and account.value == username
    finally:
        pam.pam_end(handle, status)


def confirm_account_password(window, *, title="Доступ к SSH-адресам", action="Разблокировать",
                             subject=None, detail=None, destructive=False):
    dialog = DockDialog(window, title)
    dialog.set_default_size(420, -1)
    dialog.add_buttons("Отмена", Gtk.ResponseType.CANCEL, action, Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    confirm = dialog.get_widget_for_response(Gtk.ResponseType.OK)
    confirm.get_style_context().add_class("destructive-action" if destructive else "suggested-action")
    box = dialog.get_content_area()
    box.set_border_width(20)
    box.set_spacing(10)
    if subject is not None:
        summary = Gtk.Box(spacing=12, margin_bottom=5)
        icon = "user-trash-symbolic" if destructive else "network-server-symbolic"
        summary.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR), False, False, 0)
        name = label(subject, "dialog-heading")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_tooltip_text(subject)
        summary.pack_start(name, True, True, 0)
        box.add(summary)
    if detail is not None:
        box.add(label(detail, "muted", wrap=True))
    box.add(label("Введите пароль текущей учётной записи Linux.", wrap=True))
    entry = Gtk.Entry(visibility=False, activates_default=True,
                      input_purpose=Gtk.InputPurpose.PASSWORD, placeholder_text="Пароль Linux")
    entry.get_accessible().set_name("Пароль Linux")
    box.add(entry)
    feedback = label("", wrap=True)
    box.add(feedback)
    active = True

    def show_result(verified, message):
        if active:
            feedback.set_text(message)
            dialog.response(Gtk.ResponseType.APPLY if verified else Gtk.ResponseType.REJECT)
        return GLib.SOURCE_REMOVE

    def verify(password):
        try:
            verified = verify_local_password(password)
            message = "" if verified else "Пароль не принят. Попробуйте ещё раз."
        except (OSError, KeyError, AttributeError):
            verified = False
            message = "Системная проверка пароля недоступна."
        finally:
            password[:] = bytes(len(password))
        GLib.idle_add(show_result, verified, message)

    dialog.initial_focus = entry
    try:
        while dialog.run() == Gtk.ResponseType.OK:
            password = bytearray(entry.get_text(), "utf-8")
            entry.set_text("")
            if not password:
                feedback.set_text("Введите пароль Linux.")
                continue
            entry.set_sensitive(False)
            dialog.set_response_sensitive(Gtk.ResponseType.OK, False)
            feedback.set_text("Проверяю пароль…")
            Thread(target=verify, args=(password,), daemon=True).start()
            response = dialog.run()
            if response != Gtk.ResponseType.REJECT:
                return response == Gtk.ResponseType.APPLY
            dialog.set_response_sensitive(Gtk.ResponseType.OK, True)
            entry.set_sensitive(True)
            entry.grab_focus()
        return False
    finally:
        active = False
        dialog.destroy()
