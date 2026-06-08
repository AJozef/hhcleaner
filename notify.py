"""Системные уведомления (Windows toast) для безлюдного режима (--no-input).

Вызывается, когда запланированный прогон обнаруживает протухшую сессию
и не может открыть окно входа, либо когда прогон успешно завершился.
Без уведомления пользователь узнаёт об этом только если сам заглянет в лог.

Реализация: fire-and-forget subprocess; любой сбой — тихо игнорируем
(лог уже написан, окно не нужно). На не-Windows функция тихо ничего не делает.
"""
from __future__ import annotations

import platform
import subprocess

from steps import NOTIFY_LABELS

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
    parts = [
        f"{count} {NOTIFY_LABELS.get(key, key)}"
        for key, count in results.items()
        if count > 0
    ]
    body = ("Удалено: " + ", ".join(parts)) if parts else "Нечего удалять — всё чисто."
    _notify(_APP, body)


def _notify(title: str, body: str) -> None:
    """Показывает Windows-toast. На не-Windows — no-op."""
    if platform.system() != "Windows":
        return

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


def _esc(s: str) -> str:
    """Экранирует одинарную кавычку для вставки в PowerShell-строку в одинарных кавычках."""
    return s.replace("'", "''")
