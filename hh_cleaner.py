"""
hh_cleaner.py — точка входа.

Удаляет на hh.ru отклики и чаты со статусом «отказ», а также старые чаты.
Какие шаги выполнять — выбирается аргументами командной строки, без правки кода.

Авторизация — через сохранённую сессию (вход вручную один раз, см. auth.py).
Пароль нигде не хранится; капчу и код из почты проходит сам пользователь.
Если в .env заданы HH_EMAIL и HH_PASSWORD — форма входа заполняется автоматически.

Примеры:
    hhcleaner --setup                            # первый запуск: поставить браузер + войти
    hhcleaner --login-only                       # войти и сохранить сессию
    hhcleaner                                    # все шаги по умолчанию (по сессии)
    hhcleaner negotiations                       # только отклики-отказы
    hhcleaner old-chats --days 30                # только старые чаты, порог 30 дней
    hhcleaner old-chats --since 2025-01-01       # старые чаты начиная с даты
    hhcleaner negotiations old-chats             # выбранный набор шагов
    hhcleaner --status                           # статистика по чатам без удаления
    hhcleaner --status --output json             # та же статистика в JSON (для скриптов)
    hhcleaner --check                            # только проверить сессию (код 0/3)
    hhcleaner --check --output json              # проверка сессии в JSON
    hhcleaner --quiet --no-input --log           # безлюдный прогон с записью в лог
    hhcleaner --relogin                          # сменить аккаунт (новый вход) + шаги
    hhcleaner --max-delete 20                    # удалить не более 20 элементов за шаг
    hhcleaner --workers 5                        # параллельное удаление (5 потоков)
    hhcleaner --install-schedule                 # зарегистрировать еженедельный запуск
    hhcleaner --install-schedule --schedule-day FRI --schedule-time 10:00
    hhcleaner --uninstall-schedule               # снять задачу с планировщика
    hhcleaner --self-check                       # диагностика окружения
    hhcleaner --list-profiles                    # список сохранённых профилей
    hhcleaner --delete-profile work              # удалить профиль
    hhcleaner --show-log 100                     # последние 100 строк лога
    hhcleaner --clear-log                        # очистить лог
    hhcleaner config show                        # текущий конфиг и его источники
    hhcleaner config set days 30                 # сохранить дефолт в config.toml
    hhcleaner config set profile work
    hhcleaner config unset days                  # убрать дефолт
    hhcleaner config reset                       # сбросить весь config.toml

Коды выхода: 0 — успех, 2 — вход не удался, 3 — нужен ручной вход (--login-only),
4 — не установлен браузер Playwright (запустите --setup).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    import argcomplete as _argcomplete_mod
except ImportError:
    _argcomplete_mod = None  # type: ignore[assignment]

from rich.table import Table
from playwright.sync_api import sync_playwright

import app_config
import auth
import notify
from chats_api import (
    ChatAPIError,
    check_session,
    delete_chats_api_combined,
    gather_stats,
    mark_all_chats_read,
    open_session,
)
from chats_browser import (
    delete_archived_vacancy_chats_browser,
    delete_old_chats_browser,
    delete_rejected_chats,
)
from cli_cmds import (
    chromium_executable_exists,
    cmd_config,
    clear_log,
    load_snapshot,
    save_snapshot,
    self_check,
    show_log,
)
from config import (
    DEFAULT_LOG_FILE,
    OLD_CHATS_DAYS,
    console,
    file_console,
    log,
    log_err,
    log_ok,
    log_section,
    log_warn,
    set_log_file,
    set_quiet,
)
from negotiations import delete_rejected_negotiations
from scheduler import (
    SCHEDULE_DAY,
    SCHEDULE_TIME,
    install_schedule,
    uninstall_schedule,
)

# Версия — единый источник истины в pyproject.toml; читаем из метаданных пакета.
try:
    __version__ = version("hhcleaner")
except PackageNotFoundError:
    __version__ = "dev"

# Все доступные шаги; шаги по умолчанию запускаются, если ничего не указано.
ALL_STEPS = [
    "read-all",                # пометить прочитанными все непрочитанные чаты
    "negotiations",            # отклики со статусом «отказ»
    "chats-rejected",          # чаты с отказами через API (быстро)
    "archived-vacancy",        # чаты по архивным вакансиям, кроме собеседований
    "old-chats",               # чаты старше N дней
    "chats-rejected-browser",  # чаты с отказами через браузер (резервный метод)
]
DEFAULT_STEPS = ["read-all", "negotiations", "chats-rejected", "archived-vacancy", "old-chats"]

STEP_LABELS = {
    "read-all":               "Чатов прочитано",
    "negotiations":           "Откликов удалено",
    "chats-rejected":         "Чатов-отказов удалено",
    "archived-vacancy":       "Чатов по архивным вакансиям удалено",
    "old-chats":              "Старых чатов удалено",
    "chats-rejected-browser": "Чатов-отказов удалено (браузер)",
}

# Коды выхода.
EXIT_OK           = 0
EXIT_LOGIN_FAILED = 2
EXIT_NEED_LOGIN   = 3
EXIT_NO_BROWSER   = 4


# ──────────────────────────── arg parsing ─────────────────────────────────────


def parse_args() -> argparse.Namespace:
    """Разбирает аргументы командной строки. Дефолты берёт из config.toml."""
    parser = argparse.ArgumentParser(
        description="Очистка hh.ru: отклики-отказы, чаты-отказы и старые чаты.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Подкоманды (без --): hhcleaner config show|set|unset|reset\n"
            "Справка по конфигу:   hhcleaner config --help"
        ),
    )
    parser.add_argument("--version", action="version", version=f"hhcleaner {__version__}")
    parser.add_argument(
        "steps", nargs="*", choices=ALL_STEPS, metavar="STEP",
        help=(
            f"Шаги через пробел. Без аргументов — {', '.join(DEFAULT_STEPS)}. "
            f"Доступно: {', '.join(ALL_STEPS)}."
        ),
    )

    # ── Авторизация ──────────────────────────────────────────────────────────
    grp_auth = parser.add_argument_group("авторизация")
    grp_auth.add_argument(
        "--setup", action="store_true",
        help="Первичная настройка: установить браузер (если нужно) и выполнить вход.",
    )
    grp_auth.add_argument(
        "--login-only", action="store_true",
        help="Только выполнить вход и сохранить сессию, без шагов очистки.",
    )
    grp_auth.add_argument(
        "--relogin", action="store_true",
        help="Войти заново (сменить аккаунт), затем выполнить шаги.",
    )
    grp_auth.add_argument(
        "--check", action="store_true",
        help="Только проверить сессию и выйти (код 0 — рабочая, 3 — нужен вход).",
    )
    # ── Параметры очистки ────────────────────────────────────────────────────
    grp_clean = parser.add_argument_group("очистка")
    grp_clean.add_argument(
        "--days", type=int, default=OLD_CHATS_DAYS,
        help=f"Порог в днях для шага old-chats (по умолчанию {OLD_CHATS_DAYS}).",
    )
    grp_clean.add_argument(
        "--since", metavar="DATE",
        help=(
            "Удалять чаты старше этой даты (ISO-формат: YYYY-MM-DD). "
            "Альтернатива --days; при указании имеет приоритет над --days."
        ),
    )
    grp_clean.add_argument(
        "--dry-run", action="store_true",
        help="Показать, что будет удалено, без реального удаления.",
    )
    grp_clean.add_argument(
        "--max-delete", type=int, default=None, metavar="N",
        help="Страховочный лимит: удалить не более N элементов за шаг.",
    )
    grp_clean.add_argument(
        "--workers", type=int, default=1, metavar="N",
        help=(
            "Потоков для параллельного удаления чатов через API "
            "(1 = последовательно, >=2 = параллельно). По умолчанию 1."
        ),
    )

    # ── Информация / диагностика ─────────────────────────────────────────────
    grp_info = parser.add_argument_group("информация и диагностика")
    grp_info.add_argument(
        "--status", action="store_true",
        help="Показать статистику по чатам (сколько отказов, старых и т.д.) без удаления.",
    )
    grp_info.add_argument(
        "--self-check", action="store_true",
        help="Диагностика окружения: браузер, сессия, конфиг, зависимости.",
    )
    grp_info.add_argument(
        "--show-log", nargs="?", const=50, type=int, metavar="N",
        help="Показать последние N строк лога (по умолчанию 50).",
    )
    grp_info.add_argument(
        "--clear-log", action="store_true",
        help="Очистить лог-файл (с подтверждением).",
    )

    # ── Вывод ────────────────────────────────────────────────────────────────
    grp_out = parser.add_argument_group("вывод")
    grp_out.add_argument(
        "-q", "--quiet", action="store_true",
        help="Тихий режим: выводить только итоговую сводку.",
    )
    grp_out.add_argument(
        "--output", choices=["text", "json"], default="text",
        help="Формат вывода итогов: text (таблица) или json (для скриптов).",
    )
    grp_out.add_argument(
        "--log", nargs="?", const=DEFAULT_LOG_FILE, metavar="FILE",
        help=f"Дублировать вывод в файл (append). Без значения — {DEFAULT_LOG_FILE}.",
    )
    grp_out.add_argument(
        "-n", "--no-input", action="store_true",
        help=(
            "Безлюдный режим: при невалидной сессии не открывать окно входа, "
            "а сразу выйти с кодом 3."
        ),
    )
    grp_out.add_argument(
        "--headed", action="store_true",
        help="Показать окно браузера (по умолчанию скрыто, кроме входа).",
    )
    grp_out.add_argument(
        "--keep-open", action="store_true",
        help="Не закрывать браузер по завершении (ждать Enter).",
    )

    # ── Планировщик ──────────────────────────────────────────────────────────
    grp_sched = parser.add_argument_group("планировщик")
    grp_sched.add_argument(
        "--install-schedule", action="store_true",
        help=(
            "Зарегистрировать еженедельный запуск через планировщик ОС "
            "(Windows Task Scheduler / systemd user timer / launchd plist)."
        ),
    )
    grp_sched.add_argument(
        "--uninstall-schedule", action="store_true",
        help="Удалить ранее зарегистрированную задачу из планировщика.",
    )
    grp_sched.add_argument(
        "--schedule-day", default=SCHEDULE_DAY, metavar="DAY",
        help=f"День недели для --install-schedule (MON..SUN). По умолчанию {SCHEDULE_DAY}.",
    )
    grp_sched.add_argument(
        "--schedule-time", default=SCHEDULE_TIME, metavar="HH:MM",
        help=f"Время запуска для --install-schedule. По умолчанию {SCHEDULE_TIME}.",
    )

    # Применяем дефолты из config.toml ПЕРЕД парсингом, чтобы --help показывал их.
    cfg_defaults = app_config.as_argparse_defaults()
    if cfg_defaults:
        parser.set_defaults(**cfg_defaults)

    # workers может быть задан через HH_DELETE_WORKERS.
    env_workers = os.environ.get("HH_DELETE_WORKERS", "").strip()
    if env_workers and "workers" not in cfg_defaults:
        try:
            parser.set_defaults(workers=int(env_workers))
        except ValueError:
            pass

    if _argcomplete_mod is not None:
        _argcomplete_mod.autocomplete(parser)

    args = parser.parse_args()

    return args


# ──────────────────────────── core steps ─────────────────────────────────────


def _parse_since(since_str: str | None) -> datetime | None:
    """Парсит --since DATE в tz-aware datetime. None если не задано или неверный формат."""
    if not since_str:
        return None
    try:
        dt = datetime.strptime(since_str.strip(), "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        log_err(f"Неверный формат даты --since: «{since_str}». Ожидается YYYY-MM-DD.")
        return None


def run_steps(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    context,
    session,
    steps: list[str],
    days: int,
    dry_run: bool = False,
    limit: int | None = None,
    cutoff: datetime | None = None,
    workers: int = 1,
) -> dict[str, int]:
    """Выполняет выбранные шаги в фиксированном порядке, возвращает итоги.

    cutoff — абсолютная дата среза для old-chats (--since). Если None — вычисляется из days.
    workers — число потоков для параллельного API-удаления (1 = последовательно).
    """
    results: dict[str, int] = {}
    if "read-all" in steps:
        results["read-all"] = mark_all_chats_read(session)
    if "negotiations" in steps:
        neg_page = context.new_page()
        try:
            results["negotiations"] = delete_rejected_negotiations(
                neg_page, dry_run=dry_run, limit=limit
            )
        finally:
            neg_page.close()

    api_steps = [s for s in ("chats-rejected", "archived-vacancy", "old-chats") if s in steps]
    if api_steps:
        try:
            results.update(
                delete_chats_api_combined(
                    session, api_steps, days,
                    dry_run=dry_run, limit=limit, cutoff=cutoff, workers=workers,
                )
            )
        except ChatAPIError as e:
            log_warn(f"Chatik API недоступен ({e}) — использую браузерный резерв.")
            if "chats-rejected" in api_steps:
                results["chats-rejected"] = delete_rejected_chats(context, dry_run=dry_run)
            if "archived-vacancy" in api_steps:
                results["archived-vacancy"] = delete_archived_vacancy_chats_browser(
                    context, dry_run=dry_run
                )
            if "old-chats" in api_steps:
                results["old-chats"] = delete_old_chats_browser(
                    context, days=days, dry_run=dry_run, cutoff=cutoff
                )

    if "chats-rejected-browser" in steps:
        results["chats-rejected-browser"] = delete_rejected_chats(context, dry_run=dry_run)
    return results


# ──────────────────────────── context / auth ─────────────────────────────────


def _prepare_context(p, relogin: bool, headed: bool):
    """Готовит авторизованный постоянный контекст."""
    if relogin:
        log("Перелогин: вход в новый аккаунт.")
        auth.clear_session()
        return auth.interactive_login(auth.launch_context(p, headless=False))
    if auth.session_exists():
        log("Использую сохранённую сессию.")
        return auth.launch_context(p, headless=not headed)
    log("Сохранённой сессии нет — нужно войти один раз.")
    return auth.interactive_login(auth.launch_context(p, headless=False))


# ──────────────────────────── output ─────────────────────────────────────────


def _print_summary(results: dict[str, int], elapsed: float, dry_run: bool) -> None:
    """Выводит итоговую таблицу rich с количеством обработанных элементов."""
    table = Table(
        title="[bold]Результаты[/bold]" + (" [dim](dry-run)[/dim]" if dry_run else ""),
        show_header=True, header_style="bold cyan", box=None, padding=(0, 2),
    )
    table.add_column("Операция", style="default", no_wrap=True)
    table.add_column("Кол-во", justify="right", style="bold green")
    for step_id, count in results.items():
        table.add_row(STEP_LABELS.get(step_id, step_id), str(count))

    for out in (console, file_console()):
        if out is None:
            continue
        out.print()
        out.rule("[bold cyan]Готово[/bold cyan]")
        out.print(table)
        out.print(f"[dim]Время выполнения: {elapsed:.1f} с[/dim]")


def _print_stats(
    stats: dict[str, int],
    days: int,
    prev: dict[str, Any] | None = None,
) -> None:
    """Выводит таблицу статистики. prev — снапшот прошлого прогона для дельты."""
    prev_stats = prev.get("stats", {}) if prev else {}
    prev_ts    = prev.get("ts") if prev else None

    def _delta(key: str) -> str:
        if not prev_stats or key not in prev_stats:
            return ""
        d = stats[key] - prev_stats[key]
        if d > 0:
            return f"[red]+{d}[/red]"
        if d < 0:
            return f"[green]{d}[/green]"
        return "[dim]=0[/dim]"

    rows = [
        ("total",            "Всего чатов"),
        ("unread",           "Непрочитанных"),
        ("rejected",         "Чатов-отказов"),
        ("archived_vacancy", "По архивным вакансиям (кроме собеседований)"),
        ("old",              f"Старше {days} дней"),
    ]
    title = "[bold]Статистика чатов[/bold]"
    if prev_ts:
        try:
            dt = datetime.fromisoformat(prev_ts)
            title += f"\n[dim]Дельта с {dt.strftime('%Y-%m-%d %H:%M')} UTC[/dim]"
        except ValueError:
            pass

    table = Table(title=title, show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    table.add_column("Показатель", style="default", no_wrap=True)
    table.add_column("Кол-во", justify="right", style="bold green")
    if prev_stats:
        table.add_column("Изменение", justify="right")

    for key, label in rows:
        val = str(stats.get(key, 0))
        if prev_stats:
            table.add_row(label, val, _delta(key))
        else:
            table.add_row(label, val)

    for out in (console, file_console()):
        if out is None:
            continue
        out.print()
        out.print(table)


def _emit_json(payload: dict) -> None:
    """Печатает компактный JSON в stdout."""
    print(json.dumps(payload, ensure_ascii=False))


# ──────────────────────────── browser / setup ─────────────────────────────────


def _install_browser() -> int:
    """Скачивает Chromium для Playwright. Возвращает код возврата процесса."""
    log("Скачиваю браузер Chromium для Playwright (нужно один раз)...")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
    )
    if result.returncode == 0:
        log_ok("Браузер установлен.")
    else:
        log_err("Не удалось установить браузер. Выполните вручную: playwright install chromium")
    return result.returncode


def _setup(p) -> int:
    """Первичная настройка: ставит браузер (если нужно) и проводит вход."""
    log_section("Первичная настройка hhcleaner")
    if chromium_executable_exists(p):
        log_ok("Браузер Playwright уже установлен.")
    else:
        log_warn("Браузер Playwright (Chromium) не установлен.")
        rc = _install_browser()
        if rc != 0:
            return EXIT_LOGIN_FAILED

    auth.clear_session()
    context = auth.interactive_login(auth.launch_context(p, headless=False))
    status = check_session(open_session(context))
    log(f"Проверка: {status.message}")
    context.close()
    if status.ok:
        log_ok("Готово! Сессия сохранена. Теперь запускайте: hhcleaner")
        return EXIT_OK
    log_err("Вход не подтвердился — попробуйте ещё раз: hhcleaner --setup")
    return EXIT_LOGIN_FAILED


# ──────────────────────────── main entry ─────────────────────────────────────


def main() -> int:
    """Готовит авторизацию и выполняет выбранные шаги. Возвращает код выхода."""
    # Подкоманда config (до argparse, чтобы не конфликтовать с positional steps).
    if len(sys.argv) >= 2 and sys.argv[1] == "config":
        return cmd_config(sys.argv[2:])

    args = parse_args()

    set_quiet(args.quiet or args.output == "json")
    if args.log:
        set_log_file(args.log)

    # ── Команды без Playwright ────────────────────────────────────────────────
    if args.show_log is not None:
        return show_log(args.show_log, args.log if isinstance(args.log, str) else None)

    if args.clear_log:
        return clear_log(args.log if isinstance(args.log, str) else None)

    if args.install_schedule:
        return install_schedule(args.schedule_day, args.schedule_time)

    if args.uninstall_schedule:
        return uninstall_schedule()

    # ── Парсим --since ────────────────────────────────────────────────────────
    cutoff: datetime | None = _parse_since(args.since) if hasattr(args, "since") else None

    try:
        with sync_playwright() as p:
            if args.self_check:
                return self_check()
            if args.setup:
                return _setup(p)
            return _run(p, args, cutoff=cutoff)
    except auth.LoginError as e:
        log_err(str(e))
        return EXIT_LOGIN_FAILED


def _run(p, args: argparse.Namespace, cutoff: datetime | None = None) -> int:
    """Тело работы внутри Playwright: авторизация и выполнение шагов."""
    # Детект первого запуска: нет браузера + нет сессии -> предлагаем --setup.
    if not chromium_executable_exists(p):
        log_err("Браузер Playwright (Chromium) не установлен.")
        if not args.no_input and not auth.session_exists():
            log_warn("Похоже, это первый запуск. Запустить автоматическую настройку?")
            try:
                answer = input("  hhcleaner --setup [Y/n]: ").strip().lower()
            except EOFError:
                answer = "n"
            if answer in ("", "y", "yes", "д", "да"):
                return _setup(p)
        log_err("Установите браузер: hhcleaner --setup")
        log_err("Либо вручную:       playwright install chromium")
        return EXIT_NO_BROWSER

    # ── Режим «только вход» ───────────────────────────────────────────────────
    if args.login_only:
        auth.clear_session()
        context = auth.interactive_login(auth.launch_context(p, headless=False))
        status = check_session(open_session(context))
        log(f"Проверка: {status.message}")
        if not status.ok:
            log_err("Вход не подтвердился — попробуй --login-only ещё раз.")
        context.close()
        log_ok("Готово. Теперь запускай нужные шаги обычной командой.")
        return EXIT_OK if status.ok else EXIT_LOGIN_FAILED

    # ── Режим проверки сессии ─────────────────────────────────────────────────
    if args.check:
        if not auth.session_exists():
            result = {"ok": False, "reason": "no_session", "chats": None}
            if args.output == "json":
                _emit_json(result)
            else:
                log_err("Сохранённой сессии нет — выполните вход: hhcleaner --login-only")
            return EXIT_NEED_LOGIN
        context = auth.launch_context(p, headless=not args.headed)
        status = check_session(open_session(context))
        log(f"Проверка: {status.message}")
        context.close()
        if args.output == "json":
            _emit_json({
                "ok": status.ok,
                "reason": None if status.ok else "session_invalid",
                "chats": status.chats,
                "message": status.message,
            })
        elif status.ok:
            log_ok("Сессия рабочая.")
        else:
            log_err("Сессия не работает — выполните вход: hhcleaner --login-only")
        return EXIT_OK if status.ok else EXIT_NEED_LOGIN

    # ── Режим статистики ──────────────────────────────────────────────────────
    if args.status:
        if not auth.session_exists():
            log_err("Сохранённой сессии нет — выполните вход: hhcleaner --login-only")
            return EXIT_NEED_LOGIN
        context = auth.launch_context(p, headless=not args.headed)
        session = open_session(context)
        status = check_session(session)
        log(f"Проверка: {status.message}")
        if not status.ok or session is None:
            log_err("Сессия не работает — выполните вход: hhcleaner --login-only")
            context.close()
            return EXIT_NEED_LOGIN
        stats = gather_stats(session, days=args.days)
        prev = load_snapshot()
        if args.output == "json":
            _emit_json({
                "days": args.days,
                "stats": stats,
                "prev": prev,
            })
        else:
            _print_stats(stats, args.days, prev=prev)
        save_snapshot(stats)
        context.close()
        return EXIT_OK

    # ── Основной прогон ───────────────────────────────────────────────────────
    context = _prepare_context(p, args.relogin, args.headed)
    session = open_session(context)
    status = check_session(session)
    log(f"Проверка: {status.message}")

    if not status.ok:
        if args.no_input:
            log_err("Сессия не работает, а --no-input запрещает открывать окно входа.")
            log_err("Выполните вход вручную: hhcleaner --login-only")
            notify.session_expired()
            context.close()
            return EXIT_NEED_LOGIN
        log_warn("Сессия не работает — открываю окно входа.")
        context.close()
        context = auth.interactive_login(auth.launch_context(p, headless=False))
        session = open_session(context)
        status = check_session(session)
        log(f"Проверка: {status.message}")
        if not status.ok:
            log_err("Вход не удался.")
            context.close()
            return EXIT_LOGIN_FAILED

    steps = args.steps or DEFAULT_STEPS
    log(f"Шаги: {', '.join(steps)}")
    if args.dry_run:
        log_warn("[dry-run] Режим просмотра — ничего удалено не будет.")
    if args.workers > 1:
        log(f"Параллельное удаление: {args.workers} потока(ов).")

    start = time.monotonic()
    results = run_steps(
        context, session, steps, args.days,
        dry_run=args.dry_run,
        limit=args.max_delete,
        cutoff=cutoff,
        workers=args.workers,
    )
    elapsed = time.monotonic() - start

    if args.output == "json":
        _emit_json({
            "dry_run": args.dry_run,
            "elapsed_sec": round(elapsed, 1),
            "results": results,
        })
    else:
        _print_summary(results, elapsed, args.dry_run)

    if args.no_input and not args.dry_run:
        notify.done(results)

    if args.keep_open:
        input("Нажмите Enter для закрытия браузера...")
    context.close()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
