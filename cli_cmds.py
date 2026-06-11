"""Команды hhcleaner, не требующие запущенного браузера Playwright.

Содержит:
    show_log / clear_log  — подкоманда `log show` / `log clear`
    self_check            — подкоманда `doctor` (диагностика окружения)
"""
from __future__ import annotations

import os
import sys
from collections import deque

from playwright.sync_api import sync_playwright

import auth
from config import (
    APP_DIR, DEFAULT_LOG_FILE, EXIT_OK, USER_DATA_DIR, console,
    log, log_err, log_ok, log_section, log_warn, package_version,
)

_PKG_VERSION: str = package_version()

# Человекочитаемые имена браузеров для doctor.
_BROWSER_LABELS = {"msedge": "Microsoft Edge", "chrome": "Google Chrome", "": "встроенный Chromium"}


# ──────────────────────────── log management ──────────────────────────────────


def show_log(n: int, log_path: str | None = None) -> int:
    """Выводит последние N строк лога."""
    path = log_path or DEFAULT_LOG_FILE
    if not os.path.isfile(path):
        log_err(f"Лог-файл не найден: {path}")
        return 1
    with open(path, encoding="utf-8", errors="replace") as fh:
        tail = list(deque(fh, maxlen=n))
    for line in tail:
        console.print(line.rstrip(), markup=False, highlight=False)
    console.print(f"\n[dim]{path} — показано {len(tail)} строк[/dim]")
    return EXIT_OK


def clear_log(log_path: str | None = None, assume_yes: bool = False) -> int:
    """Очищает лог-файл (после подтверждения).

    assume_yes — пропустить интерактивный вопрос (флаг --yes для безлюдного
    режима).
    """
    path = log_path or DEFAULT_LOG_FILE
    if not os.path.isfile(path):
        log(f"Лог-файл не существует: {path}")
        return EXIT_OK
    if assume_yes:
        answer = "y"
    else:
        size_kb = os.path.getsize(path) // 1024
        log_warn(f"Очистить лог {path} ({size_kb} КБ)?")
        try:
            answer = input("[y/N]: ").strip().lower()
        except EOFError:
            answer = ""
    if answer not in ("y", "yes", "д", "да"):
        log("Отменено.")
        return EXIT_OK
    with open(path, "w", encoding="utf-8"):
        pass  # открытие в режиме "w" обрезает файл до нуля
    log_ok("Лог очищен.")
    return EXIT_OK


# ──────────────────────────── self-check ─────────────────────────────────────


def self_check() -> int:
    """Диагностика окружения: версии, браузер, сессия, лог."""
    log_section("Диагностика hhcleaner (doctor)")

    ok_all = True

    def _chk(label: str, ok: bool, detail: str = "") -> None:
        """Печатает строку диагностики; любая неудача заваливает общий итог (ok_all)."""
        nonlocal ok_all
        mark = "[green]v[/green]" if ok else "[red]x[/red]"
        suffix = f"  [dim]{detail}[/dim]" if detail else ""
        console.print(f"  {mark}  {label}{suffix}")
        if not ok:
            ok_all = False

    # Python
    pv = sys.version_info
    _chk(
        f"Python {pv.major}.{pv.minor}.{pv.micro}",
        pv >= (3, 9),
        "" if pv >= (3, 9) else "требуется >=3.9",
    )

    # Пакет
    _chk(f"hhcleaner {_PKG_VERSION}", _PKG_VERSION != "dev",
         "запуск из исходников (pip install -e . не выполнен)" if _PKG_VERSION == "dev" else "")

    # Playwright + системный браузер для входа
    try:
        with sync_playwright() as p:
            browser = auth.detect_browser(p)
        _chk("Playwright установлен", True)
        if browser is None:
            _chk("Браузер для входа (Edge/Chrome)", False,
                 "установите Microsoft Edge или Google Chrome")
        else:
            _chk("Браузер для входа", True, _BROWSER_LABELS.get(browser, browser))
    except Exception as e:  # pylint: disable=broad-exception-caught
        _chk("Playwright", False, str(e))

    # Сессия
    session_ok = auth.session_exists()
    _chk(
        "Сессия",
        session_ok,
        "hhcleaner login" if not session_ok else USER_DATA_DIR,
    )

    # Каталог данных + лог
    _chk(
        f"Каталог данных ({APP_DIR})",
        os.path.isdir(APP_DIR),
        "создастся автоматически при первом запуске" if not os.path.isdir(APP_DIR) else "",
    )
    log_exists = os.path.isfile(DEFAULT_LOG_FILE)
    if log_exists:
        size_kb = os.path.getsize(DEFAULT_LOG_FILE) // 1024
        _chk("Лог-файл", True, f"{DEFAULT_LOG_FILE} ({size_kb} КБ)")
    else:
        _chk("Лог-файл", True, f"ещё не создан -> {DEFAULT_LOG_FILE}")

    if ok_all:
        log_ok("Всё в порядке — можно работать.")
    else:
        log_warn("Есть проблемы — устраните отмеченные пункты.")
    return EXIT_OK if ok_all else 1
