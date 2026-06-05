"""Команды hhcleaner, не требующие запущенного браузера Playwright.

Содержит:
    log     — show / clear
    doctor  — --self-check (диагностика окружения)
"""
from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version

from playwright.sync_api import sync_playwright

import auth
from config import (
    APP_DIR, DEFAULT_LOG_FILE, USER_DATA_DIR, console,
    log, log_err, log_ok, log_section, log_warn,
)

# Код выхода «успех» — дублируем локально, чтобы не создавать цикл с hh_cleaner.
EXIT_OK = 0

try:
    _PKG_VERSION: str = version("hhcleaner")
except PackageNotFoundError:
    _PKG_VERSION = "dev"

try:
    import argcomplete as _argcomplete_mod
except ImportError:
    _argcomplete_mod = None  # type: ignore[assignment]


# ──────────────────────────── browser helper ─────────────────────────────────


def chromium_executable_exists(p) -> bool:
    """True, если бинарь Chromium для Playwright уже скачан на диск."""
    try:
        path = p.chromium.executable_path
        return bool(path) and os.path.exists(path)
    except Exception:  # pylint: disable=broad-exception-caught
        return False


# ──────────────────────────── log management ──────────────────────────────────


def show_log(n: int, log_path: str | None = None) -> int:
    """Выводит последние N строк лога."""
    path = log_path or DEFAULT_LOG_FILE
    if not os.path.isfile(path):
        log_err(f"Лог-файл не найден: {path}")
        return 1
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    tail = lines[-n:] if len(lines) > n else lines
    for line in tail:
        console.print(line.rstrip(), markup=False, highlight=False)
    console.print(f"\n[dim]{path} — {len(lines)} строк всего, показано {len(tail)}[/dim]")
    return EXIT_OK


def clear_log(log_path: str | None = None) -> int:
    """Очищает лог-файл (после подтверждения)."""
    path = log_path or DEFAULT_LOG_FILE
    if not os.path.isfile(path):
        log(f"Лог-файл не существует: {path}")
        return EXIT_OK
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
    log_section("Диагностика hhcleaner (--self-check)")

    ok_all = True

    def _chk(label: str, ok: bool, detail: str = "") -> None:
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

    # argcomplete
    _ac_ok = _argcomplete_mod is not None
    _chk("argcomplete (tab-completion)", _ac_ok,
         "pip install argcomplete" if not _ac_ok else "")

    # Playwright + Chromium
    try:
        with sync_playwright() as p:
            browser_ok = chromium_executable_exists(p)
        _chk("Playwright установлен", True)
        _chk("Chromium (браузер) скачан", browser_ok,
             "hhcleaner --setup" if not browser_ok else "")
    except Exception as e:  # pylint: disable=broad-exception-caught
        _chk("Playwright", False, str(e))

    # Сессия
    session_ok = auth.session_exists()
    _chk(
        "Сессия",
        session_ok,
        "hhcleaner --login-only" if not session_ok else USER_DATA_DIR,
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

    # Credentials
    has_email = bool(os.environ.get("HH_EMAIL", "").strip())
    has_pwd   = bool(os.environ.get("HH_PASSWORD", "").strip())
    _chk("HH_EMAIL в .env", has_email, "(опционально) задайте для автозаполнения формы")
    _chk("HH_PASSWORD в .env", has_pwd, "(опционально) задайте для автозаполнения формы")

    if ok_all:
        log_ok("Всё в порядке — можно работать.")
    else:
        log_warn("Есть проблемы — устраните отмеченные пункты.")
    return EXIT_OK if ok_all else 1
