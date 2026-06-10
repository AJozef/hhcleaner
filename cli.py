"""Командный интерфейс hhcleaner: подкоманды, их разбор и обработчики.

Команды (вместо прежних mode-флагов вроде --login-only):

    hhcleaner [clean] [STEP ...]   очистка — команда по умолчанию
    hhcleaner login                войти на hh.ru и сохранить сессию
    hhcleaner check                рабочая ли сессия (код 0/3)
    hhcleaner status               статистика по чатам без удаления
    hhcleaner doctor               диагностика окружения
    hhcleaner schedule install     еженедельный запуск через Task Scheduler
    hhcleaner schedule uninstall   снять задачу с планировщика
    hhcleaner log show [N]         показать последние N строк лога
    hhcleaner log clear            очистить лог

«clean» — неявная команда по умолчанию: bare `hhcleaner`, `hhcleaner negotiations`
и `hhcleaner --quiet --no-input --log` работают как раньше (важно для уже
зарегистрированных задач планировщика).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

import auth
import notify
from chats_api import gather_stats
from cli_cmds import clear_log, self_check, show_log
from config import (
    DEFAULT_LOG_FILE,
    EXIT_LOGIN_FAILED,
    EXIT_NEED_LOGIN,
    EXIT_OK,
    OLD_CHATS_DAYS,
    first_run_pending,
    log,
    log_err,
    log_ok,
    log_warn,
    mark_first_run_done,
    package_version,
    set_log_file,
    set_quiet,
)
from report import print_stats, print_summary
from runner import acquire_context, open_and_check, require_saved_session, run_steps
from scheduler import (
    SCHEDULE_DAY,
    SCHEDULE_TIME,
    install_schedule,
    uninstall_schedule,
)
from steps import ALL_STEPS, DEFAULT_STEPS

# Имена подкоманд. Нужны, чтобы отличить `hhcleaner negotiations` (шаг неявного
# clean) от `hhcleaner login` (подкоманда) при подстановке команды по умолчанию.
COMMANDS = ("clean", "login", "check", "status", "doctor", "schedule", "log")

_EXAMPLES = """\
Примеры:
  hhcleaner                            все шаги по умолчанию (по сохранённой сессии)
  hhcleaner negotiations               только отклики-отказы
  hhcleaner old-chats --days 30        только старые чаты, порог 30 дней
  hhcleaner old-chats --since 2025-01-01   старые чаты начиная с даты
  hhcleaner --dry-run                  показать, что будет удалено, ничего не трогая
  hhcleaner --force-browser            не ходить в API, чистить через браузер
  hhcleaner --quiet --no-input --log   безлюдный прогон с записью в лог
  hhcleaner login                      войти и сохранить сессию
  hhcleaner status                     статистика по чатам без удаления
  hhcleaner doctor                     диагностика окружения
  hhcleaner schedule install --day FRI --time 10:00
  hhcleaner log show 100               последние 100 строк лога

Коды выхода: 0 — успех, 2 — вход не удался, 3 — нужен ручной вход (hhcleaner login).
"""

_VALID_SCHEDULE_DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


# ──────────────────────────── argparse types ─────────────────────────────────


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


# ──────────────────────────── parser ─────────────────────────────────────────


def _add_clean_arguments(parser: argparse.ArgumentParser) -> None:
    """Аргументы команды clean (она же — поведение по умолчанию)."""
    parser.add_argument(
        "steps", nargs="*", choices=ALL_STEPS, metavar="STEP",
        help=(
            f"Шаги через пробел. Без аргументов — {', '.join(DEFAULT_STEPS)}. "
            f"Доступно: {', '.join(ALL_STEPS)}."
        ),
    )
    parser.add_argument(
        "--days", type=_positive_int, default=OLD_CHATS_DAYS,
        help=f"Порог в днях для шага old-chats (по умолчанию {OLD_CHATS_DAYS}).",
    )
    parser.add_argument(
        "--since", type=_iso_date, default=None, metavar="DATE",
        help=(
            "Удалять чаты старше этой даты (ISO-формат: YYYY-MM-DD). "
            "Альтернатива --days; при указании имеет приоритет над --days."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Показать, что будет удалено, без реального удаления.",
    )
    parser.add_argument(
        "--max-delete", type=_positive_int, default=None, metavar="N",
        help="Страховочный лимит: удалить не более N элементов за шаг.",
    )
    parser.add_argument(
        "--force-browser", action="store_true",
        help="Не ходить в chatik API, сразу чистить через браузер (резервный путь).",
    )
    parser.add_argument(
        "--relogin", action="store_true",
        help="Войти заново (сменить аккаунт), затем выполнить шаги.",
    )
    parser.add_argument(
        "-n", "--no-input", action="store_true",
        help="Безлюдный режим: при невалидной сессии не открывать окно входа, а выйти с кодом 3.",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Отвечать «да» на подтверждения и не делать предпросмотр на первом запуске.",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Показать окно браузера (по умолчанию скрыто, кроме входа).",
    )
    parser.add_argument(
        "--keep-open", action="store_true",
        help="Не закрывать браузер по завершении (ждать Enter).",
    )


def build_parser() -> argparse.ArgumentParser:
    """Собирает парсер с подкомандами."""
    parser = argparse.ArgumentParser(
        prog="hhcleaner",
        description="Очистка hh.ru: отклики-отказы, чаты-отказы и старые чаты.",
        epilog=_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"hhcleaner {package_version()}"
    )

    # Общие опции вывода — у всех подкоманд (через parents).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-q", "--quiet", action="store_true",
        help="Тихий режим: выводить только итоговую сводку.",
    )
    common.add_argument(
        "--log", nargs="?", const=DEFAULT_LOG_FILE, metavar="FILE",
        help=f"Дублировать вывод в файл (append). Без значения — {DEFAULT_LOG_FILE}.",
    )

    sub = parser.add_subparsers(dest="command", metavar="КОМАНДА")

    p_clean = sub.add_parser(
        "clean", parents=[common], help="Очистка (команда по умолчанию).",
        description="Очистка hh.ru. Запускается и без явного слова clean.",
    )
    _add_clean_arguments(p_clean)

    sub.add_parser(
        "login", parents=[common], help="Войти на hh.ru и сохранить сессию.",
    )

    p_check = sub.add_parser(
        "check", parents=[common], help="Проверить, рабочая ли сессия (код 0/3).",
    )
    p_check.add_argument(
        "--headed", action="store_true", help="Показать окно браузера при проверке.",
    )

    p_status = sub.add_parser(
        "status", parents=[common], help="Статистика по чатам без удаления.",
    )
    p_status.add_argument(
        "--days", type=_positive_int, default=OLD_CHATS_DAYS,
        help=f"Порог «старых» чатов в днях (по умолчанию {OLD_CHATS_DAYS}).",
    )
    p_status.add_argument(
        "--headed", action="store_true", help="Показать окно браузера.",
    )

    sub.add_parser(
        "doctor", parents=[common],
        help="Диагностика окружения: браузер, сессия, конфиг, зависимости.",
    )

    p_sched = sub.add_parser(
        "schedule", parents=[common], help="Управление еженедельным запуском.",
    )
    p_sched.add_argument(
        "action", choices=["install", "uninstall"],
        help="install — зарегистрировать задачу, uninstall — снять.",
    )
    p_sched.add_argument(
        "--day", type=_schedule_day, default=SCHEDULE_DAY, metavar="DAY",
        help=f"День недели для install (MON..SUN). По умолчанию {SCHEDULE_DAY}.",
    )
    p_sched.add_argument(
        "--time", type=_schedule_time, default=SCHEDULE_TIME, metavar="HH:MM",
        help=f"Время запуска для install. По умолчанию {SCHEDULE_TIME}.",
    )

    p_log = sub.add_parser(
        "log", parents=[common], help="Просмотр и очистка лога.",
    )
    p_log.add_argument(
        "action", choices=["show", "clear"],
        help="show — показать последние строки, clear — очистить файл.",
    )
    p_log.add_argument(
        "lines", nargs="?", type=int, default=50, metavar="N",
        help="Для show: сколько последних строк показать (по умолчанию 50).",
    )
    p_log.add_argument(
        "-y", "--yes", action="store_true",
        help="Для clear: не спрашивать подтверждение.",
    )

    return parser


def _with_default_command(argv: list[str]) -> list[str]:
    """Подставляет команду clean, если первый аргумент — не команда и не -h/--version.

    Так `hhcleaner`, `hhcleaner negotiations`, `hhcleaner --dry-run` и плановый
    `hhcleaner --quiet --no-input --log ...` остаются командой clean.
    """
    if argv and (argv[0] in COMMANDS or argv[0] in ("-h", "--help", "--version")):
        return argv
    return ["clean", *argv]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Разбирает аргументы (по умолчанию из sys.argv)."""
    raw = list(sys.argv[1:] if argv is None else argv)
    return build_parser().parse_args(_with_default_command(raw))


def apply_output(args: argparse.Namespace) -> None:
    """Применяет общие опции вывода (--quiet/--log) до выполнения команды."""
    set_quiet(getattr(args, "quiet", False))
    log_path = getattr(args, "log", None)
    if log_path:
        set_log_file(log_path)


# ──────────────────────────── command handlers ───────────────────────────────


def _cmd_login(p) -> int:
    """login: чистый вход и сохранение сессии (бывший --setup / --login-only)."""
    auth.clear_session()
    context = auth.interactive_login(auth.launch_context(p, headless=False))
    _session, status = open_and_check(context)
    log(f"Проверка: {status.message}")
    context.close()
    if status.ok:
        log_ok("Готово. Сессия сохранена — запускайте: hhcleaner")
        return EXIT_OK
    log_err("Вход не подтвердился — попробуйте ещё раз: hhcleaner login")
    return EXIT_LOGIN_FAILED


def _cmd_check(p, args) -> int:
    """check: только проверить сессию."""
    if not require_saved_session():
        return EXIT_NEED_LOGIN
    context = auth.launch_context(p, headless=not args.headed)
    _session, status = open_and_check(context)
    log(f"Проверка: {status.message}")
    context.close()
    if status.ok:
        log_ok("Сессия рабочая.")
        return EXIT_OK
    log_err("Сессия не работает — выполните вход: hhcleaner login")
    return EXIT_NEED_LOGIN


def _cmd_status(p, args) -> int:
    """status: статистика по чатам без удаления."""
    if not require_saved_session():
        return EXIT_NEED_LOGIN
    context = auth.launch_context(p, headless=not args.headed)
    session, status = open_and_check(context)
    log(f"Проверка: {status.message}")
    if not status.ok or session is None:
        log_err("Сессия не работает — выполните вход: hhcleaner login")
        context.close()
        return EXIT_NEED_LOGIN
    stats = gather_stats(session, days=args.days)
    print_stats(stats, args.days)
    context.close()
    return EXIT_OK


def _cmd_clean(p, args) -> int:
    """clean: основной режим — выполнить выбранные шаги очистки."""
    result = acquire_context(p, args)
    if isinstance(result, int):
        return result
    context, session = result

    steps = args.steps or DEFAULT_STEPS
    log(f"Шаги: {', '.join(steps)}")

    # Страховка первого запуска: первый РЕАЛЬНЫЙ интерактивный прогон гоним как
    # предпросмотр (dry-run), показываем числа и просим повторить. Безлюдный
    # (--no-input) и явные --dry-run/--yes не трогаем: там либо плановый прогон,
    # который обязан удалять, либо пользователь подтвердил, что знает, что делает.
    dry_run = args.dry_run
    preview_only = (
        not dry_run and not args.no_input and not args.yes and first_run_pending()
    )
    if preview_only:
        dry_run = True
        log_warn("Первый запуск — показываю предпросмотр без удаления.")
    if dry_run:
        log_warn("[dry-run] Режим просмотра — ничего удалено не будет.")

    start = time.monotonic()
    results = run_steps(
        context, session, steps, args.days,
        dry_run=dry_run,
        limit=args.max_delete,
        cutoff=args.since,
        force_browser=args.force_browser,
    )
    print_summary(results, time.monotonic() - start, dry_run)

    if preview_only:
        mark_first_run_done()
        log_warn(
            "Это был предпросмотр первого запуска. Чтобы действительно удалить — "
            "запустите ту же команду ещё раз."
        )
    elif not dry_run:
        mark_first_run_done()

    if args.no_input and not dry_run:
        notify.done(results)
    if args.keep_open:
        try:
            input("Нажмите Enter для закрытия браузера...")
        except EOFError:
            pass
    context.close()
    return EXIT_OK


# ──────────────────────────── dispatch ───────────────────────────────────────


def dispatch(args: argparse.Namespace) -> int:
    """Маршрутизирует разобранную команду в обработчик. Возвращает код выхода."""
    cmd = args.command

    # Команды без браузера.
    if cmd == "log":
        log_path = args.log if isinstance(args.log, str) else None
        if args.action == "show":
            return show_log(args.lines, log_path)
        return clear_log(log_path, assume_yes=args.yes)
    if cmd == "schedule":
        if args.action == "install":
            return install_schedule(args.day, args.time)
        return uninstall_schedule()
    if cmd == "doctor":
        # self_check сам открывает sync_playwright внутри — держим его вне
        # внешнего контекста, иначе Chromium-движок поднимался бы дважды.
        return self_check()

    # Команды, которым нужен браузер.
    try:
        with sync_playwright() as p:
            if cmd == "login":
                return _cmd_login(p)
            if cmd == "check":
                return _cmd_check(p, args)
            if cmd == "status":
                return _cmd_status(p, args)
            return _cmd_clean(p, args)
    except auth.LoginError as e:
        log_err(str(e))
        return EXIT_LOGIN_FAILED
