"""Команды hhcleaner, не требующие запущенного браузера Playwright.

Содержит:
    config   — show / set / unset / reset
    log      — show / clear
    status   — снапшоты для delta-сравнения
    doctor   — --self-check (диагностика окружения)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright
from rich.table import Table

import app_config
import auth
from config import (
    APP_DIR, DEFAULT_LOG_FILE, OLD_CHATS_DAYS, USER_DATA_DIR, console,
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


# ──────────────────────────── config subcommand ───────────────────────────────


def _build_config_parser() -> argparse.ArgumentParser:
    """Парсер подкоманды config со своими show/set/unset/reset (и --help)."""
    known = sorted(app_config.KNOWN_KEYS)
    keys_help = ", ".join(
        f"{k} ({desc})" for k, (_t, desc) in sorted(app_config.KNOWN_KEYS.items())
    )

    parser = argparse.ArgumentParser(
        prog="hhcleaner config",
        description="Персистентный конфиг (~/.hhcleaner/config.toml). "
                    "Приоритет: CLI-флаг > config.toml > HH_*-env > хардкод.",
        epilog=f"Ключи: {keys_help}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="action", metavar="show|set|unset|reset")

    sub.add_parser("show", help="Показать текущие настройки и их источники.")

    p_set = sub.add_parser("set", help="Установить значение: config set KEY VALUE")
    p_set.add_argument("key", metavar="KEY", choices=known, help=f"Один из: {', '.join(known)}")
    p_set.add_argument("value", metavar="VALUE", help="Новое значение.")

    p_unset = sub.add_parser("unset", help="Убрать значение из конфига: config unset KEY")
    p_unset.add_argument("key", metavar="KEY", choices=known, help=f"Один из: {', '.join(known)}")

    sub.add_parser("reset", help="Удалить config.toml (сброс всех настроек к дефолтам).")
    return parser


def cmd_config(argv: list[str]) -> int:
    """Обрабатывает `hhcleaner config show|set|unset|reset` через argparse."""
    parser = _build_config_parser()
    args = parser.parse_args(argv)
    action = args.action or "show"  # без подкоманды — показываем конфиг

    if action == "show":
        _config_show()
        return EXIT_OK

    if action == "set":
        ok, msg = app_config.set_key(args.key, args.value)
        if ok:
            log_ok(f"Сохранено: {msg}")
            return EXIT_OK
        log_err(msg)
        return 1

    if action == "unset":
        ok, msg = app_config.unset_key(args.key)
        if ok:
            log_ok(msg)
            return EXIT_OK
        log_err(msg)
        return 1

    # action == "reset"
    cfg_path = app_config.config_path()
    if cfg_path.exists():
        log_warn(f"Удалить {cfg_path} ?")
        try:
            answer = input("[y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes", "д", "да"):
            log("Отменено.")
            return EXIT_OK
    if app_config.reset():
        log_ok("Конфиг удалён. Все настройки возвращены к дефолтам.")
    else:
        log("Файл конфига не существовал — ничего не удалено.")
    return EXIT_OK


def _config_show() -> None:
    """Выводит текущие настройки и их источник."""
    cfg = app_config.load()
    cfg_path = app_config.config_path()

    table = Table(
        title="[bold]Конфиг hhcleaner[/bold]",
        show_header=True, header_style="bold cyan", box=None, padding=(0, 2),
    )
    table.add_column("Ключ", style="bold", no_wrap=True)
    table.add_column("Значение", style="green")
    table.add_column("Источник", style="dim")

    def _row(key: str, env_var: str | None, hardcode: str) -> None:
        env_val = os.environ.get(env_var, "").strip() if env_var else ""
        cfg_val = str(cfg.get(key, "")).strip()
        if env_val:
            table.add_row(key, env_val, f"env {env_var}")
        elif cfg_val:
            table.add_row(key, cfg_val, "config.toml")
        else:
            table.add_row(key, hardcode, "хардкод")

    _row("days",       "HH_OLD_DAYS",      str(OLD_CHATS_DAYS))
    _row("log",        None,               f"(нет) -> {DEFAULT_LOG_FILE}")
    _row("quiet",      None,                "false")
    _row("headed",     None,                "false")
    _row("max_delete", None,                "(нет ограничения)")
    _row("workers",    "HH_DELETE_WORKERS", "1")

    console.print()
    console.print(table)
    exists_str = "[green]существует[/green]" if cfg_path.exists() else "[dim]не создан[/dim]"
    console.print(f"\n[dim]Файл конфига: {cfg_path} ({exists_str})[/dim]")
    console.print(f"[dim]Каталог данных: {APP_DIR}[/dim]")
    console.print()
    console.print("[dim]Изменить: hhcleaner config set KEY VALUE[/dim]")
    console.print("[dim]Сбросить: hhcleaner config reset[/dim]")


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


# ──────────────────────────── status delta ────────────────────────────────────


def snapshot_path() -> Path:
    """Путь к файлу снапшота статистики."""
    return Path(APP_DIR) / "last_status.json"


def load_snapshot() -> dict[str, Any] | None:
    """Загружает последний снапшот статистики. None если не существует или повреждён."""
    p = snapshot_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def save_snapshot(stats: dict[str, int]) -> None:
    """Сохраняет текущую статистику как снапшот (best-effort)."""
    p = snapshot_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"ts": datetime.now(timezone.utc).isoformat(), "stats": stats}
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:  # pylint: disable=broad-exception-caught
        pass


# ──────────────────────────── self-check ─────────────────────────────────────


def self_check() -> int:
    """Диагностика окружения: версии, браузер, сессия, конфиг."""
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

    # Конфиг
    cfg_path = app_config.config_path()
    _chk("config.toml", cfg_path.exists(),
         str(cfg_path) if not cfg_path.exists() else f"{cfg_path} (OK)")

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
