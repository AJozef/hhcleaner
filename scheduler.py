"""Регистрация и снятие еженедельного запуска hhcleaner через планировщик ОС.

Поддерживаемые бэкенды:
  Windows  — Task Scheduler (schtasks)
  Linux    — systemd user timer (с fallback на crontab)
  macOS    — launchd plist (~/ Library/LaunchAgents/)
  Прочее   — печатает crontab-строку и выходит

Функции install_schedule / uninstall_schedule — публичные точки входа;
остальные — приватные вспомогательные.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from config import DEFAULT_LOG_FILE, console, log, log_err, log_ok, log_warn

# Имя задачи в планировщике (Windows) и таймера (systemd).
TASK_NAME     = "HHCleaner"
SCHEDULE_DAY  = "MON"
SCHEDULE_TIME = "09:00"

# Таблицы дней для разных планировщиков.
_DAY_TO_CRON: dict[str, str] = {
    "MON": "1", "TUE": "2", "WED": "3", "THU": "4",
    "FRI": "5", "SAT": "6", "SUN": "0",
}
_DAY_TO_SYSTEMD: dict[str, str] = {
    "MON": "Mon", "TUE": "Tue", "WED": "Wed", "THU": "Thu",
    "FRI": "Fri", "SAT": "Sat", "SUN": "Sun",
}
_DAY_TO_LAUNCHD: dict[str, int] = {
    "SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6,
}


# ──────────────────────────── command builder ─────────────────────────────────


def _scheduled_run_command(profile: str = "default") -> str:
    """Строит команду для планировщика.

    Предпочитает установленный exe-файл; если не найден — python hh_cleaner.py.
    """
    profile_arg = f' --profile "{profile}"' if profile != "default" else ""
    run_args = f'--quiet --no-input --log "{DEFAULT_LOG_FILE}"{profile_arg}'

    scripts_dir = os.path.dirname(sys.executable)
    exe_name = "hhcleaner.exe" if os.name == "nt" else "hhcleaner"
    exe_path = os.path.join(scripts_dir, exe_name)
    if os.path.isfile(exe_path):
        return f'"{exe_path}" {run_args}'

    # Fallback: ищем hh_cleaner.py через importlib (работает и в editable-install).
    spec = importlib.util.find_spec("hh_cleaner")
    script = spec.origin if (spec and spec.origin) else "hh_cleaner.py"
    return f'"{sys.executable}" "{os.path.abspath(script)}" {run_args}'


# ──────────────────────────── платформо-зависимые установщики ────────────────


def _install_schedule_windows(run_cmd: str, day: str, time_str: str) -> int:
    """Регистрирует задачу в Windows Task Scheduler."""
    cmd = [
        "schtasks", "/create", "/tn", TASK_NAME,
        "/tr", run_cmd,
        "/sc", "WEEKLY", "/d", day, "/st", time_str, "/f",
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="cp866", errors="replace", check=False,
    )
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


def _install_schedule_systemd(run_cmd: str, day: str, time_str: str) -> int:
    """Создаёт systemd user service + timer и активирует их."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)

    sys_day = _DAY_TO_SYSTEMD.get(day.upper(), "Mon")
    h, m = (time_str.split(":") + ["0"])[:2]
    on_calendar = f"{sys_day} {int(h):02d}:{int(m):02d}:00"

    service_path = unit_dir / "hhcleaner.service"
    service_path.write_text(
        "[Unit]\n"
        "Description=HHCleaner — очистка hh.ru\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={run_cmd}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )

    timer_path = unit_dir / "hhcleaner.timer"
    timer_path.write_text(
        "[Unit]\n"
        "Description=HHCleaner weekly timer\n"
        "\n"
        "[Timer]\n"
        f"OnCalendar={on_calendar}\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n",
        encoding="utf-8",
    )

    log(f"Создан: {service_path}")
    log(f"Создан: {timer_path}")

    subprocess.run(
        ["systemctl", "--user", "daemon-reload"], check=False
    )

    rc = subprocess.run(
        ["systemctl", "--user", "enable", "--now", "hhcleaner.timer"], check=False
    ).returncode
    if rc == 0:
        log_ok(f"Таймер активирован: каждый {day} в {time_str}.")
        log("Статус:  systemctl --user status hhcleaner.timer")
        log("Удалить: hhcleaner --uninstall-schedule")
    else:
        log_warn("Не удалось активировать таймер. Активируйте вручную:")
        log("  systemctl --user enable --now hhcleaner.timer")
    return rc


def _uninstall_schedule_systemd() -> int:
    """Останавливает и удаляет systemd user timer и service."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", "hhcleaner.timer"],
        check=False, capture_output=True,
    )
    removed = []
    for name in ("hhcleaner.timer", "hhcleaner.service"):
        f = unit_dir / name
        if f.exists():
            f.unlink()
            removed.append(str(f))
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    if removed:
        log_ok(f"Удалено: {', '.join(removed)}")
    else:
        log_warn("Файлы юнитов не найдены — возможно, задача уже удалена.")
    return 0


def _install_schedule_launchd(run_cmd: str, day: str, time_str: str) -> int:
    """Создаёт LaunchAgent plist (macOS) и регистрирует его."""
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    weekday = _DAY_TO_LAUNCHD.get(day.upper(), 1)
    h, m = (time_str.split(":") + ["0"])[:2]
    plist_path = agents_dir / "com.hhcleaner.plist"

    plist_content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '    <key>Label</key>\n'
        '    <string>com.hhcleaner</string>\n'
        '    <key>ProgramArguments</key>\n'
        '    <array>\n'
        + "".join(f"        <string>{part}</string>\n"
                  for part in run_cmd.strip('"').split('" "'))
        + '    </array>\n'
        '    <key>StartCalendarInterval</key>\n'
        '    <dict>\n'
        f'        <key>Weekday</key><integer>{weekday}</integer>\n'
        f'        <key>Hour</key><integer>{int(h)}</integer>\n'
        f'        <key>Minute</key><integer>{int(m)}</integer>\n'
        '    </dict>\n'
        '    <key>RunAtLoad</key><false/>\n'
        '    <key>StandardOutPath</key>\n'
        f'    <string>{DEFAULT_LOG_FILE}</string>\n'
        '    <key>StandardErrorPath</key>\n'
        f'    <string>{DEFAULT_LOG_FILE}.err</string>\n'
        '</dict>\n'
        '</plist>\n'
    )
    plist_path.write_text(plist_content, encoding="utf-8")
    log(f"Создан: {plist_path}")

    rc = subprocess.run(
        ["launchctl", "load", str(plist_path)], check=False,
        capture_output=True, text=True,
    ).returncode
    if rc == 0:
        log_ok(f"LaunchAgent зарегистрирован: каждый {day} в {time_str}.")
        log("Удалить: hhcleaner --uninstall-schedule")
    else:
        log_warn("Не удалось загрузить plist. Выполните вручную:")
        log(f"  launchctl load {plist_path}")
    return rc


def _uninstall_schedule_launchd() -> int:
    """Выгружает и удаляет LaunchAgent."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.hhcleaner.plist"
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        check=False, capture_output=True,
    )
    if plist_path.exists():
        plist_path.unlink()
        log_ok(f"Удалено: {plist_path}")
    else:
        log_warn("plist не найден — задача уже была удалена.")
    return 0


# ──────────────────────────── публичные функции ───────────────────────────────


def install_schedule(
    day: str = SCHEDULE_DAY,
    time_str: str = SCHEDULE_TIME,
    profile: str = "default",
) -> int:
    """Регистрирует задачу в планировщике ОС (Windows/Linux/macOS)."""
    run_cmd = _scheduled_run_command(profile)
    plat = sys.platform

    if plat == "win32":
        try:
            return _install_schedule_windows(run_cmd, day, time_str)
        except FileNotFoundError:
            pass  # schtasks недоступен — падаем на crontab ниже

    if plat == "linux":
        if shutil.which("systemctl"):
            return _install_schedule_systemd(run_cmd, day, time_str)
        # Без systemd — crontab.
        cron_day = _DAY_TO_CRON.get(day.upper(), "1")
        h, m = (time_str.split(":") + ["0"])[:2]
        cron_line = f"{int(m)} {int(h)} * * {cron_day} {run_cmd}"
        log_warn("systemctl не найден. Добавьте в crontab (crontab -e):")
        console.print(f"  [bold]{cron_line}[/bold]")
        return 0

    if plat == "darwin":
        return _install_schedule_launchd(run_cmd, day, time_str)

    # Неизвестная ОС — печатаем crontab-строку.
    cron_day = _DAY_TO_CRON.get(day.upper(), "1")
    h, m = (time_str.split(":") + ["0"])[:2]
    cron_line = f"{int(m)} {int(h)} * * {cron_day} {run_cmd}"
    log_warn("Платформа не распознана. Добавьте в crontab (crontab -e):")
    console.print(f"  [bold]{cron_line}[/bold]")
    return 0


def uninstall_schedule() -> int:
    """Удаляет задачу из планировщика (обратная к install_schedule)."""
    plat = sys.platform

    if plat == "win32":
        cmd = ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="cp866", errors="replace", check=False,
            )
        except FileNotFoundError:
            log_warn("schtasks не найден. Удалите строку из crontab: crontab -e")
            return 0
        if result.returncode == 0:
            log_ok(f"Задача «{TASK_NAME}» удалена из планировщика.")
        else:
            log_err("Не удалось удалить задачу (возможно, её и не было):")
            log_err(result.stderr or result.stdout)
        return result.returncode

    if plat == "linux":
        return _uninstall_schedule_systemd()

    if plat == "darwin":
        return _uninstall_schedule_launchd()

    log_warn("Платформа не распознана. Удалите задачу из планировщика вручную.")
    return 0
