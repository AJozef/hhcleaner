"""Дружелюбный визард для двойного клика по собранному .exe.

Срабатывает, когда onefile-бинарь запущен без аргументов (обыватель кликнул по
файлу). Плановый/безлюдный прогон всегда идёт с флагами, то есть с аргументами —
и визард не трогает.
"""
from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright

import auth
from config import (
    EXIT_LOGIN_FAILED,
    EXIT_OK,
    console,
    log,
    log_err,
    log_warn,
    mark_first_run_done,
)
from report import print_summary
from runner import open_and_check, run_steps
from scheduler import SCHEDULE_DAY, SCHEDULE_TIME, install_schedule, schedule_exists
from steps import CleanOptions, DEFAULT_STEPS

_DISCLAIMER = (
    "Программа удаляет на hh.ru отклики-отказы и ненужные чаты (отказы, архивные\n"
    "вакансии, старые переписки).\n"
    "\n"
    "Вход вы выполняете сами на сайте hh.ru в окне браузера — программа не видит\n"
    "ваш пароль и нигде его не хранит. На компьютере остаётся только cookie сессии,\n"
    "как при обычном входе в браузере.\n"
    "\n"
    "Неофициальный инструмент, с hh.ru не связан."
)


def should_run() -> bool:
    """True при двойном клике по .exe (frozen и без аргументов).

    Намеренно НЕ пытаемся отличить двойной клик от запуска в терминале по числу
    процессов консоли: в onefile-сборке бутлоадер добавляет лишний процесс, и
    такая эвристика хрупкая. Запуск из исходников (frozen=False) — всегда CLI.
    """
    return getattr(sys, "frozen", False) and len(sys.argv) == 1


def _pause() -> None:
    """Держит окно открытым, пока человек не нажмёт Enter."""
    try:
        input("\nНажмите Enter, чтобы закрыть окно…")
    except EOFError:
        pass


def _offer_schedule() -> None:
    """Предлагает настроить еженедельную автоочистку, если она ещё не настроена.

    Спрашиваем явное «да» (по умолчанию НЕТ): случайное нажатие не должно молча
    зарегистрировать задачу в планировщике.
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


def _login(p):
    """Открывает видимое окно браузера и ждёт ручного входа на hh.ru."""
    console.print("Сейчас откроется окно браузера — войдите на hh.ru как обычно.")
    console.print("[dim]Капчу и код из почты/SMS вводите там же, вручную.[/dim]\n")
    return auth.interactive_login(auth.launch_context(p, headless=False))


def _wizard(p) -> int:
    """Сценарий на двойной клик: дисклеймер → вход → очистка → итоги → пауза."""
    console.rule("[bold cyan]hhcleaner[/bold cyan]")
    console.print(_DISCLAIMER)
    try:
        input("\nНажмите Enter, чтобы начать (или закройте окно, чтобы отменить)… ")
    except EOFError:
        return EXIT_OK
    console.print()

    # Вход: рабочую сессию переиспользуем; если её нет/протухла — открываем браузер.
    if auth.session_exists():
        console.print("Проверяю сохранённый вход…")
        context = auth.launch_context(p, headless=True)
        session, status = open_and_check(context)
        log(f"Проверка: {status.message}")
        if not status.ok:
            context.close()
            log_warn("Сохранённый вход устарел — нужно войти заново.")
            context = _login(p)
            session, status = open_and_check(context)
            log(f"Проверка: {status.message}")
    else:
        context = _login(p)
        session, status = open_and_check(context)
        log(f"Проверка: {status.message}")

    if not status.ok or session is None:
        log_err("Войти не удалось. Закройте окно и попробуйте ещё раз.")
        context.close()
        _pause()
        return EXIT_LOGIN_FAILED

    console.print("\nНачинаю очистку — это может занять несколько минут…\n")
    start = time.monotonic()
    results = run_steps(context, session, DEFAULT_STEPS, CleanOptions())
    print_summary(results, time.monotonic() - start, dry_run=False)
    context.close()

    # Визард показал дисклеймер и реально почистил — снимаем страховку-предпросмотр
    # для будущих запусков из CLI, чтобы человеку не предлагали предпросмотр снова.
    mark_first_run_done()

    console.print("\n[green]Готово![/green]")
    _offer_schedule()
    _pause()
    return EXIT_OK


def run() -> int:
    """Точка входа визарда: поднимает Playwright и ведёт сценарий двойного клика."""
    try:
        with sync_playwright() as p:
            return _wizard(p)
    except auth.LoginError as e:
        log_err(str(e))
        _pause()
        return EXIT_LOGIN_FAILED
