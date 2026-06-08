"""Регистрация и снятие еженедельного запуска hhcleaner через Windows Task Scheduler.

Функции install_schedule / uninstall_schedule — публичные точки входа.
На не-Windows возвращают ошибку: проект ориентирован на Windows-машину пользователя.
"""
from __future__ import annotations

import importlib.util
import os
import platform
import subprocess
import sys

from config import DEFAULT_LOG_FILE, log, log_err, log_ok, log_warn

# Имя задачи в Windows Task Scheduler.
TASK_NAME     = "HHCleaner"
SCHEDULE_DAY  = "MON"
SCHEDULE_TIME = "09:00"


# ──────────────────────────── command builder ─────────────────────────────────


def _scheduled_run_command() -> str:
    """Строит команду для планировщика.

    Предпочитает установленный exe-файл; если не найден — python hh_cleaner.py.
    """
    run_args = f'--quiet --no-input --log "{DEFAULT_LOG_FILE}"'

    scripts_dir = os.path.dirname(sys.executable)
    exe_path = os.path.join(scripts_dir, "hhcleaner.exe")
    if os.path.isfile(exe_path):
        return f'"{exe_path}" {run_args}'

    # Fallback: ищем hh_cleaner.py через importlib (работает и в editable-install).
    spec = importlib.util.find_spec("hh_cleaner")
    script = spec.origin if (spec and spec.origin) else "hh_cleaner.py"
    return f'"{sys.executable}" "{os.path.abspath(script)}" {run_args}'


# ──────────────────────────── публичные функции ───────────────────────────────


def _ensure_windows() -> bool:
    """True на Windows; иначе печатает ошибку и возвращает False.

    Используем platform.system() (а не sys.platform), чтобы избежать
    type-narrowing у Pyright: sys.platform типизируется литералом для
    текущей ОС, из-за чего ветка с ошибкой считалась бы недостижимой.
    """
    if platform.system() == "Windows":
        return True
    log_err("Планировщик поддерживается только на Windows.")
    return False


def install_schedule(
    day: str = SCHEDULE_DAY,
    time_str: str = SCHEDULE_TIME,
) -> int:
    """Регистрирует задачу в Windows Task Scheduler."""
    if not _ensure_windows():
        return 1

    run_cmd = _scheduled_run_command()
    cmd = [
        "schtasks", "/create", "/tn", TASK_NAME,
        "/tr", run_cmd,
        "/sc", "WEEKLY", "/d", day, "/st", time_str, "/f",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="cp866", errors="replace", check=False,
        )
    except FileNotFoundError:
        log_err("schtasks не найден в PATH.")
        return 1

    if result.returncode == 0:
        log_ok(f"Задача «{TASK_NAME}» создана: каждый {day} в {time_str}.")
        log(f"Управление: Планировщик задач -> {TASK_NAME}")
        log("Удалить:    hhcleaner --uninstall-schedule")
    else:
        log_err("Не удалось создать задачу:")
        log_err(result.stderr or result.stdout)
        if result.returncode == 1:
            log_warn("Возможно, нужны права администратора — "
                    "запустите терминал «от имени администратора».")
    return result.returncode


def uninstall_schedule() -> int:
    """Удаляет задачу из Windows Task Scheduler."""
    if not _ensure_windows():
        return 1

    cmd = ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="cp866", errors="replace", check=False,
        )
    except FileNotFoundError:
        log_err("schtasks не найден в PATH.")
        return 1

    if result.returncode == 0:
        log_ok(f"Задача «{TASK_NAME}» удалена из планировщика.")
    else:
        log_err("Не удалось удалить задачу (возможно, её и не было):")
        log_err(result.stderr or result.stdout)
    return result.returncode
