"""
hh_cleaner.py — точка входа.

Удаляет на hh.ru отклики и чаты со статусом «отказ», а также старые чаты.
Какие шаги выполнять — выбирается аргументами командной строки, без правки кода.

Авторизация — через сохранённую сессию (вход вручную один раз, см. auth.py).
Пароль нигде не хранится; капчу и код из почты проходит сам пользователь.
Если в .env заданы HH_EMAIL и HH_PASSWORD — форма входа заполняется автоматически.

Вход открывается в системном браузере (Edge/Chrome) — отдельно ничего скачивать
не нужно. На Windows 10/11 Edge есть всегда.

Примеры:
    hhcleaner --setup                            # первый запуск: войти и сохранить сессию
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

Коды выхода: 0 — успех, 2 — вход не удался, 3 — нужен ручной вход (--login-only).
"""
from __future__ import annotations

import argparse
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
    clear_log,
    self_check,
    show_log,
)
from config import (
    DEFAULT_LOG_FILE,
    EXIT_LOGIN_FAILED,
    EXIT_NEED_LOGIN,
    EXIT_OK,
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
    schedule_exists,
    uninstall_schedule,
)
from steps import ALL_STEPS, API_STEPS, DEFAULT_STEPS, STEP_LABELS

# Версия — единый источник истины в pyproject.toml; читаем из метаданных пакета.
__version__ = package_version()


# ──────────────────────────── arg parsing ─────────────────────────────────────


_VALID_SCHEDULE_DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def _schedule_day(value: str) -> str:
    """argparse-тип: день недели MON..SUN."""
    v = value.strip().upper()
    if v not in _VALID_SCHEDULE_DAYS:
        raise argparse.ArgumentTypeError(
            f"ожидается день недели ({', '.join(_VALID_SCHEDULE_DAYS)}), получено «{value}»"
        )
    return v


def _schedule_time(value: str) -> str:
    """argparse-тип: время в формате HH:MM."""
    import re  # pylint: disable=import-outside-toplevel
    if not re.fullmatch(r"\d{2}:\d{2}", value.strip()):
        raise argparse.ArgumentTypeError(
            f"ожидается время в формате HH:MM, получено «{value}»"
        )
    return value.strip()


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
        dt = datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ожидается дата в формате YYYY-MM-DD, получено «{value}»"
        ) from exc
    if dt > datetime.now(timezone.utc):
        raise argparse.ArgumentTypeError(
            f"дата --since в будущем ({value}). Укажите прошедшую дату."
        )
    return dt


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
        "-y", "--yes", action="store_true",
        help="Отвечать «да» на подтверждения (для безлюдного режима, напр. --clear-log).",
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
        "--schedule-day", default=SCHEDULE_DAY, metavar="DAY", type=_schedule_day,
        help=f"День недели для --install-schedule (MON..SUN). По умолчанию {SCHEDULE_DAY}.",
    )
    grp_sched.add_argument(
        "--schedule-time", default=SCHEDULE_TIME, metavar="HH:MM", type=_schedule_time,
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
        try:
            results["read-all"] = mark_all_chats_read(session)
        except ChatAPIError as e:
            # read-all ходит только через API; браузерного аналога нет —
            # не валим прогон, но и не глотаем сбой молча.
            log_warn(f"Не удалось пометить чаты прочитанными (chatik API: {e}).")
            results["read-all"] = 0
    if "negotiations" in steps:
        neg_page = context.new_page()
        try:
            results["negotiations"] = delete_rejected_negotiations(
                neg_page, dry_run=dry_run, limit=limit
            )
        finally:
            neg_page.close()

    api_steps = [s for s in API_STEPS if s in steps]
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


def _open_and_check(context):
    """open_session + check_session. Возвращает (session, status); лог на стороне вызывающего."""
    session = open_session(context)
    return session, check_session(session)


def _require_saved_session() -> bool:
    """True, если есть сохранённая сессия; иначе печатает подсказку и False."""
    if auth.session_exists():
        return True
    log_err("Сохранённой сессии нет — выполните вход: hhcleaner --login-only")
    return False


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


def _setup(p) -> int:
    """Первичная настройка: проводит вход. Браузер берётся системный (Edge/Chrome)."""
    log_section("Первичная настройка hhcleaner")
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


# ──────────────────────────── wizard (двойной клик) ──────────────────────────

_WIZARD_DISCLAIMER = (
    "Программа удаляет на hh.ru отклики-отказы и ненужные чаты (отказы, архивные\n"
    "вакансии, старые переписки).\n"
    "\n"
    "Вход вы выполняете сами на сайте hh.ru в окне браузера — программа не видит\n"
    "ваш пароль и нигде его не хранит. На компьютере остаётся только cookie сессии,\n"
    "как при обычном входе в браузере.\n"
    "\n"
    "Неофициальный инструмент, с hh.ru не связан."
)


def _should_run_wizard() -> bool:
    """Дружелюбный визард вместо чистого CLI для собранного .exe без аргументов.

    Срабатывает при двойном клике по hhcleaner.exe (или когда человек просто
    набрал имя exe без флагов). Плановый/безлюдный прогон всегда идёт с флагами
    (--no-input --quiet --log ...), то есть с аргументами — и визард не трогает.
    Намеренно НЕ пытаемся отличить двойной клик от запуска в терминале по числу
    процессов консоли: в onefile-сборке бутлоадер добавляет лишний процесс, и
    такая эвристика хрупкая. Запуск из исходников (frozen=False) — всегда CLI.
    """
    return getattr(sys, "frozen", False) and len(sys.argv) == 1


def _wizard_pause() -> None:
    """Держит окно открытым, пока человек не нажмёт Enter (иначе оно мгновенно закроется)."""
    try:
        input("\nНажмите Enter, чтобы закрыть окно…")
    except EOFError:
        pass


def _wizard_offer_schedule() -> None:
    """Предлагает настроить еженедельную автоочистку, если она ещё не настроена.

    Спрашиваем явное «да» (по умолчанию НЕТ): отдельный Enter-на-закрытие рядом, и
    случайное нажатие не должно молча зарегистрировать задачу в планировщике.
    """
    if schedule_exists():
        console.print("[dim]Автоматическая еженедельная очистка уже настроена.[/dim]")
        return
    console.print(
        "\nМожно настроить автоматическую очистку раз в неделю — "
        "тогда запускать вручную не придётся."
    )
    try:
        answer = input("Настроить автоочистку? [y/N]: ").strip().lower()
    except EOFError:
        return
    if answer in ("y", "yes", "д", "да"):
        install_schedule(SCHEDULE_DAY, SCHEDULE_TIME)


def _wizard_login(p):
    """Открывает видимое окно браузера и ждёт ручного входа на hh.ru."""
    console.print("Сейчас откроется окно браузера — войдите на hh.ru как обычно.")
    console.print("[dim]Капчу и код из почты/SMS вводите там же, вручную.[/dim]\n")
    return auth.interactive_login(auth.launch_context(p, headless=False))


def _wizard(p) -> int:
    """Сценарий на двойной клик: дисклеймер → вход → очистка → итоги → пауза."""
    console.rule("[bold cyan]hhcleaner[/bold cyan]")
    console.print(_WIZARD_DISCLAIMER)
    try:
        input("\nНажмите Enter, чтобы начать (или закройте окно, чтобы отменить)… ")
    except EOFError:
        return EXIT_OK
    console.print()

    # Вход: рабочую сессию переиспользуем; если её нет/протухла — открываем браузер.
    if auth.session_exists():
        console.print("Проверяю сохранённый вход…")
        context = auth.launch_context(p, headless=True)
        session, status = _open_and_check(context)
        log(f"Проверка: {status.message}")
        if not status.ok:
            context.close()
            log_warn("Сохранённый вход устарел — нужно войти заново.")
            context = _wizard_login(p)
            session, status = _open_and_check(context)
            log(f"Проверка: {status.message}")
    else:
        context = _wizard_login(p)
        session, status = _open_and_check(context)
        log(f"Проверка: {status.message}")

    if not status.ok or session is None:
        log_err("Войти не удалось. Закройте окно и попробуйте ещё раз.")
        context.close()
        _wizard_pause()
        return EXIT_LOGIN_FAILED

    console.print("\nНачинаю очистку — это может занять несколько минут…\n")
    start = time.monotonic()
    results = run_steps(context, session, DEFAULT_STEPS, OLD_CHATS_DAYS)
    _print_summary(results, time.monotonic() - start, dry_run=False)
    context.close()

    console.print("\n[green]Готово![/green]")
    _wizard_offer_schedule()
    _wizard_pause()
    return EXIT_OK


# ──────────────────────────── main entry ─────────────────────────────────────


def main() -> int:
    """Готовит авторизацию и выполняет выбранные шаги. Возвращает код выхода."""
    # Двойной клик по .exe (без аргументов) — ведём обывателя за руку, без флагов.
    if _should_run_wizard():
        try:
            with sync_playwright() as p:
                return _wizard(p)
        except auth.LoginError as e:
            log_err(str(e))
            _wizard_pause()
            return EXIT_LOGIN_FAILED

    args = parse_args()

    set_quiet(args.quiet)
    if args.log:
        set_log_file(args.log)

    # ── Команды без Playwright ────────────────────────────────────────────────
    if args.show_log is not None:
        return show_log(args.show_log, args.log if isinstance(args.log, str) else None)

    if args.clear_log:
        log_path = args.log if isinstance(args.log, str) else None
        if args.no_input and not args.yes:
            log_err("Очистка лога требует подтверждения: добавьте --yes для безлюдного режима.")
            return EXIT_OK
        return clear_log(log_path, assume_yes=args.yes)

    if args.install_schedule:
        return install_schedule(args.schedule_day, args.schedule_time)

    if args.uninstall_schedule:
        return uninstall_schedule()

    # self_check сам открывает sync_playwright внутри — держим его здесь, до
    # внешнего контекста, иначе Chromium-движок поднимался бы дважды (вложенно).
    if args.self_check:
        return self_check()

    cutoff: datetime | None = args.since

    try:
        with sync_playwright() as p:
            if args.setup:
                return _setup(p)
            return _run(p, args, cutoff=cutoff)
    except auth.LoginError as e:
        log_err(str(e))
        return EXIT_LOGIN_FAILED


def _do_login_only(p) -> int:
    auth.clear_session()
    context = auth.interactive_login(auth.launch_context(p, headless=False))
    _session, status = _open_and_check(context)
    log(f"Проверка: {status.message}")
    context.close()
    if status.ok:
        log_ok("Готово. Теперь запускай нужные шаги обычной командой.")
        return EXIT_OK
    log_err("Вход не подтвердился — попробуй --login-only ещё раз.")
    return EXIT_LOGIN_FAILED


def _do_check(p, args) -> int:
    if not _require_saved_session():
        return EXIT_NEED_LOGIN
    context = auth.launch_context(p, headless=not args.headed)
    _session, status = _open_and_check(context)
    log(f"Проверка: {status.message}")
    context.close()
    if status.ok:
        log_ok("Сессия рабочая.")
    else:
        log_err("Сессия не работает — выполните вход: hhcleaner --login-only")
    return EXIT_OK if status.ok else EXIT_NEED_LOGIN


def _do_status(p, args) -> int:
    if not _require_saved_session():
        return EXIT_NEED_LOGIN
    context = auth.launch_context(p, headless=not args.headed)
    session, status = _open_and_check(context)
    log(f"Проверка: {status.message}")
    if not status.ok or session is None:
        log_err("Сессия не работает — выполните вход: hhcleaner --login-only")
        context.close()
        return EXIT_NEED_LOGIN
    stats = gather_stats(session, days=args.days)
    _print_stats(stats, args.days)
    context.close()
    return EXIT_OK


def _acquire_context(p, args):
    """Возвращает (context, session) с валидной сессией или int (код выхода).

    Содержит всю логику перелогина: тихий (через .env) и интерактивный.
    """
    if args.no_input and not args.relogin and not auth.session_exists():
        log_err("Сохранённой сессии нет, а --no-input запрещает открывать окно входа.")
        log_err("Выполните вход вручную: hhcleaner --login-only")
        notify.session_expired()
        return EXIT_NEED_LOGIN

    context = _prepare_context(p, args.relogin, args.headed)
    session, status = _open_and_check(context)
    log(f"Проверка: {status.message}")

    if not status.ok and auth.has_login_credentials():
        log_warn("Сессия не работает — пробую тихий перелогин из HH_EMAIL/HH_PASSWORD.")
        if auth.headless_login(context):
            session, status = _open_and_check(context)
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
        session, status = _open_and_check(context)
        log(f"Проверка: {status.message}")
        if not status.ok:
            log_err("Вход не удался.")
            context.close()
            return EXIT_LOGIN_FAILED

    return context, session


def _do_main(p, args, cutoff: datetime | None) -> int:
    result = _acquire_context(p, args)
    if isinstance(result, int):
        return result
    context, session = result

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
    _print_summary(results, time.monotonic() - start, args.dry_run)

    if args.no_input and not args.dry_run:
        notify.done(results)
    if args.keep_open:
        input("Нажмите Enter для закрытия браузера...")
    context.close()
    return EXIT_OK


def _run(p, args: argparse.Namespace, cutoff: datetime | None = None) -> int:
    """Диспетчер режимов внутри Playwright-контекста."""
    if args.login_only:
        return _do_login_only(p)
    if args.check:
        return _do_check(p, args)
    if args.status:
        return _do_status(p, args)
    return _do_main(p, args, cutoff)


if __name__ == "__main__":
    sys.exit(main())
