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
    hhcleaner --check                            # только проверить сессию (код 0/3)
    hhcleaner --quiet --no-input --log           # безлюдный прогон с записью в лог
    hhcleaner --relogin                          # сменить аккаунт (новый вход) + шаги
    hhcleaner --max-delete 20                    # удалить не более 20 элементов за шаг
    hhcleaner --install-schedule                 # зарегистрировать еженедельный запуск
    hhcleaner --install-schedule --schedule-day FRI --schedule-time 10:00
    hhcleaner --uninstall-schedule               # снять задачу с планировщика
    hhcleaner --self-check                       # диагностика окружения
    hhcleaner --show-log 100                     # последние 100 строк лога
    hhcleaner --clear-log                        # очистить лог

Коды выхода: 0 — успех, 2 — вход не удался, 3 — нужен ручной вход (--login-only),
4 — не установлен браузер Playwright (запустите --setup).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone

from rich.table import Table
from playwright.sync_api import sync_playwright

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
    clear_log,
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
    package_version,
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
__version__ = package_version()

# Все доступные шаги; шаги по умолчанию запускаются, если ничего не указано.
ALL_STEPS = [
    "read-all",                # пометить прочитанными все непрочитанные чаты
    "negotiations",            # отклики со статусом «отказ»
    "chats-rejected",          # чаты с отказами: API, при падении — браузер (фолбэк)
    "archived-vacancy",        # чаты по архивным вакансиям, кроме собеседований
    "old-chats",               # чаты старше N дней
]
DEFAULT_STEPS = ["read-all", "negotiations", "chats-rejected", "archived-vacancy", "old-chats"]

STEP_LABELS = {
    "read-all":               "Чатов прочитано",
    "negotiations":           "Откликов удалено",
    "chats-rejected":         "Чатов-отказов удалено",
    "archived-vacancy":       "Чатов по архивным вакансиям удалено",
    "old-chats":              "Старых чатов удалено",
}

# Коды выхода.
EXIT_OK           = 0
EXIT_LOGIN_FAILED = 2
EXIT_NEED_LOGIN   = 3
EXIT_NO_BROWSER   = 4


# ──────────────────────────── arg parsing ─────────────────────────────────────


def _positive_int(value: str) -> int:
    """argparse-тип: целое >= 1. Понятная ошибка вместо мусора в логике ниже."""
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"ожидается целое число, получено «{value}»") from exc
    if n < 1:
        raise argparse.ArgumentTypeError(f"должно быть >= 1, получено {n}")
    return n


def _iso_date(value: str) -> datetime:
    """argparse-тип: дата YYYY-MM-DD → tz-aware datetime (UTC).

    Валидируем на этапе разбора аргументов: битая дата у удаляющего инструмента
    должна остановить запуск с понятной ошибкой, а не молча превратиться в
    дефолтное окно --days.
    """
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ожидается дата в формате YYYY-MM-DD, получено «{value}»"
        ) from exc


def parse_args() -> argparse.Namespace:
    """Разбирает аргументы командной строки."""
    parser = argparse.ArgumentParser(
        description="Очистка hh.ru: отклики-отказы, чаты-отказы и старые чаты.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        "--days", type=_positive_int, default=OLD_CHATS_DAYS,
        help=f"Порог в днях для шага old-chats (по умолчанию {OLD_CHATS_DAYS}).",
    )
    grp_clean.add_argument(
        "--since", type=_iso_date, default=None, metavar="DATE",
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
        "--max-delete", type=_positive_int, default=None, metavar="N",
        help="Страховочный лимит: удалить не более N элементов за шаг.",
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
        help="Зарегистрировать еженедельный запуск через Windows Task Scheduler.",
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

    args = parser.parse_args()

    return args


# ──────────────────────────── core steps ─────────────────────────────────────


def run_steps(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    context,
    session,
    steps: list[str],
    days: int,
    dry_run: bool = False,
    limit: int | None = None,
    cutoff: datetime | None = None,
) -> dict[str, int]:
    """Выполняет выбранные шаги в фиксированном порядке, возвращает итоги.

    cutoff — абсолютная дата среза для old-chats (--since). Если None — вычисляется из days.
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
                    dry_run=dry_run, limit=limit, cutoff=cutoff,
                )
            )
        except ChatAPIError as e:
            log_warn(f"Chatik API недоступен ({e}) — использую браузерный резерв.")
            if "chats-rejected" in api_steps:
                results["chats-rejected"] = delete_rejected_chats(
                    context, dry_run=dry_run, limit=limit
                )
            if "archived-vacancy" in api_steps:
                results["archived-vacancy"] = delete_archived_vacancy_chats_browser(
                    context, dry_run=dry_run, limit=limit
                )
            if "old-chats" in api_steps:
                results["old-chats"] = delete_old_chats_browser(
                    context, days=days, dry_run=dry_run, cutoff=cutoff, limit=limit
                )

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


def _print_stats(stats: dict[str, int], days: int) -> None:
    """Выводит таблицу статистики чатов."""
    rows = [
        ("total",            "Всего чатов"),
        ("unread",           "Непрочитанных"),
        ("rejected",         "Чатов-отказов"),
        ("archived_vacancy", "По архивным вакансиям (кроме собеседований)"),
        ("old",              f"Старше {days} дней"),
    ]
    table = Table(
        title="[bold]Статистика чатов[/bold]",
        show_header=True, header_style="bold cyan", box=None, padding=(0, 2),
    )
    table.add_column("Показатель", style="default", no_wrap=True)
    table.add_column("Кол-во", justify="right", style="bold green")
    for key, label in rows:
        table.add_row(label, str(stats.get(key, 0)))

    for out in (console, file_console()):
        if out is None:
            continue
        out.print()
        out.print(table)


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
    args = parse_args()

    set_quiet(args.quiet)
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

    cutoff: datetime | None = args.since

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
            log_err("Сохранённой сессии нет — выполните вход: hhcleaner --login-only")
            return EXIT_NEED_LOGIN
        context = auth.launch_context(p, headless=not args.headed)
        status = check_session(open_session(context))
        log(f"Проверка: {status.message}")
        context.close()
        if status.ok:
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
        _print_stats(stats, args.days)
        context.close()
        return EXIT_OK

    # ── Основной прогон ───────────────────────────────────────────────────────
    # Если сессии вовсе нет и --no-input — выходим до открытия окна:
    # _prepare_context в этом случае позвал бы interactive_login.
    if args.no_input and not args.relogin and not auth.session_exists():
        log_err("Сохранённой сессии нет, а --no-input запрещает открывать окно входа.")
        log_err("Выполните вход вручную: hhcleaner --login-only")
        notify.session_expired()
        return EXIT_NEED_LOGIN

    context = _prepare_context(p, args.relogin, args.headed)
    session = open_session(context)
    status = check_session(session)
    log(f"Проверка: {status.message}")

    if not status.ok and auth.has_login_credentials():
        log_warn("Сессия не работает — пробую тихий перелогин из HH_EMAIL/HH_PASSWORD.")
        if auth.headless_login(context):
            session = open_session(context)
            status = check_session(session)
            log(f"После тихого перелогина: {status.message}")
        else:
            log_warn("Тихий перелогин не удался (возможно, капча или 2FA).")

    if not status.ok:
        if args.no_input:
            log_err("Сессия не работает, а --no-input запрещает открывать окно входа.")
            if auth.has_login_credentials():
                log_err("Тихий перелогин споткнулся — вероятно, появилась капча или 2FA.")
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

    start = time.monotonic()
    results = run_steps(
        context, session, steps, args.days,
        dry_run=args.dry_run,
        limit=args.max_delete,
        cutoff=cutoff,
    )
    elapsed = time.monotonic() - start

    _print_summary(results, elapsed, args.dry_run)

    if args.no_input and not args.dry_run:
        notify.done(results)

    if args.keep_open:
        input("Нажмите Enter для закрытия браузера...")
    context.close()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
