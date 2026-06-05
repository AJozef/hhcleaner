"""Системные уведомления для безлюдного режима (--no-input).

Вызывается, когда запланированный прогон обнаруживает протухшую сессию
и не может открыть окно входа. Без уведомления пользователь узнаёт об
этом только если сам заглянет в лог.

Реализация: fire-and-forget subprocess; любой сбой — тихо игнорируем
(лог уже написан, окно не нужно).
"""
from __future__ import annotations

import subprocess
import sys

_APP = "HHCleaner"


def session_expired() -> None:
    """Показывает системное уведомление: сессия истекла, нужен ручной вход."""
    _notify(
        title=_APP,
        body="Сессия hh.ru истекла — выполните: hhcleaner --login-only",
    )


def done(results: dict) -> None:
    """Показывает системное уведомление с итогами успешного прогона.

    Вызывается в --no-input режиме (запуск по расписанию): пользователь не
    смотрит в терминал, поэтому toast — единственный способ узнать результат.
    """
    _labels = {
        "read-all":               "прочитано чатов",
        "negotiations":           "откликов удалено",
        "chats-rejected":         "чатов-отказов удалено",
        "archived-vacancy":       "архивных чатов удалено",
        "old-chats":              "старых чатов удалено",
        "chats-rejected-browser": "чатов-отказов удалено",
    }
    parts = [
        f"{count} {_labels.get(key, key)}"
        for key, count in results.items()
        if count > 0
    ]
    body = ("Удалено: " + ", ".join(parts)) if parts else "Нечего удалять — всё чисто."
    _notify(_APP, body)


def _notify(title: str, body: str) -> None:
    if sys.platform == "win32":
        _win_toast(title, body)
    elif sys.platform == "darwin":
        _mac_notify(title, body)
    else:
        _linux_notify(title, body)


def _win_toast(title: str, body: str) -> None:
    """Toast через WinRT (Windows 10/11). CREATE_NO_WINDOW — консоль не всплывает."""
    # PowerShell 5.1 грузит WinRT-типы через ContentType=WindowsRuntime.
    # InnerText безопаснее AppendChild для строк с кавычками.
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "[Windows.UI.Notifications.ToastNotificationManager,"
        "Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$n=$t.GetElementsByTagName('text');"
        f"$n.Item(0).InnerText='{_esc(title)}';"
        f"$n.Item(1).InnerText='{_esc(body)}';"
        "[Windows.UI.Notifications.ToastNotificationManager]::"
        f"CreateToastNotifier('{_esc(_APP)}').Show("
        "[Windows.UI.Notifications.ToastNotification]::new($t))"
    )
    try:
        subprocess.Popen(  # pylint: disable=consider-using-with
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _mac_notify(title: str, body: str) -> None:
    try:
        subprocess.Popen(  # pylint: disable=consider-using-with
            ["osascript", "-e",
             f'display notification "{_esc_dq(body)}" with title "{_esc_dq(title)}"'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _linux_notify(title: str, body: str) -> None:
    try:
        subprocess.Popen(  # pylint: disable=consider-using-with
            ["notify-send", "--urgency=critical", title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _esc(s: str) -> str:
    """Экранирует одинарную кавычку для вставки в PowerShell-строку в одинарных кавычках."""
    return s.replace("'", "''")


def _esc_dq(s: str) -> str:
    """Экранирует двойную кавычку для AppleScript/bash строк в двойных кавычках."""
    return s.replace('"', '\\"')
