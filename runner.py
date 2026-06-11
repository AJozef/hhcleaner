"""Оркестрация очистки: выполнение шагов и получение авторизованного контекста.

Здесь живёт «что и в каком порядке делать» (run_steps) и «как раздобыть рабочую
сессию» (acquire_context). Печать итогов — в report.py, разбор аргументов и
команды — в cli.py.
"""
from __future__ import annotations

import auth
import notify
from chats_api import (
    ChatAPIError,
    check_session,
    delete_chats_api_combined,
    mark_all_chats_read,
    open_session,
)
from chats_browser import (
    delete_archived_vacancy_chats_browser,
    delete_old_chats_browser,
    delete_rejected_chats,
)
from config import (
    EXIT_LOGIN_FAILED,
    EXIT_NEED_LOGIN,
    log,
    log_err,
    log_warn,
)
from negotiations import delete_rejected_negotiations
from steps import API_STEPS, CleanOptions


# ──────────────────────────── core steps ─────────────────────────────────────


def _run_browser_steps(
    context,
    api_steps: list[str],
    opts: CleanOptions,
) -> dict[str, int]:
    """Браузерный путь для chats-rejected / archived-vacancy / old-chats.

    Общий код для двух случаев: API вернул 401/403 (фолбэк) и явный --force-browser.
    """
    out: dict[str, int] = {}
    if "chats-rejected" in api_steps:
        out["chats-rejected"] = delete_rejected_chats(
            context, dry_run=opts.dry_run, limit=opts.limit
        )
    if "archived-vacancy" in api_steps:
        out["archived-vacancy"] = delete_archived_vacancy_chats_browser(
            context, dry_run=opts.dry_run, limit=opts.limit
        )
    if "old-chats" in api_steps:
        out["old-chats"] = delete_old_chats_browser(
            context, days=opts.days, dry_run=opts.dry_run, cutoff=opts.cutoff, limit=opts.limit
        )
    return out


def run_steps(
    context,
    session,
    steps: list[str],
    opts: CleanOptions,
) -> dict[str, int]:
    """Выполняет выбранные шаги в фиксированном порядке, возвращает итоги.

    opts.cutoff — абсолютная дата среза для old-chats (--since). Если None — вычисляется из days.
    opts.force_browser — не ходить в chatik API, сразу использовать браузерный путь
    (рычаг для проверки резерва и аварийный режим, если API сломался без 401/403).
    """
    results: dict[str, int] = {}
    if "read-all" in steps:
        if opts.force_browser:
            # У read-all нет браузерного аналога — он ходит только через API.
            log_warn("read-all доступен только через API — в режиме --force-browser пропущен.")
            results["read-all"] = 0
        else:
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
                neg_page, dry_run=opts.dry_run, limit=opts.limit
            )
        finally:
            neg_page.close()

    api_steps = [s for s in API_STEPS if s in steps]
    if api_steps:
        if opts.force_browser:
            log_warn("Принудительно использую браузерный метод (--force-browser).")
            results.update(_run_browser_steps(context, api_steps, opts))
        else:
            try:
                results.update(
                    delete_chats_api_combined(session, api_steps, opts)
                )
            except ChatAPIError as e:
                log_warn(f"Chatik API недоступен ({e}) — использую браузерный резерв.")
                results.update(_run_browser_steps(context, api_steps, opts))

    return results


# ──────────────────────────── context / auth ─────────────────────────────────


def open_and_check(context):
    """open_session + check_session. Возвращает (session, status); лог на стороне вызывающего."""
    session = open_session(context)
    return session, check_session(session)


def require_saved_session() -> bool:
    """True, если есть сохранённая сессия; иначе печатает подсказку и False."""
    if auth.session_exists():
        return True
    log_err("Сохранённой сессии нет — выполните вход: hhcleaner login")
    return False


def prepare_context(p, *, relogin: bool, headed: bool):
    """Готовит авторизованный постоянный контекст для интерактивного режима."""
    if relogin:
        log("Перелогин: вход в новый аккаунт.")
        auth.clear_session()
        return auth.interactive_login(auth.launch_context(p, headless=False))
    if auth.session_exists():
        log("Использую сохранённую сессию.")
        return auth.launch_context(p, headless=not headed)
    log("Сохранённой сессии нет — нужно войти один раз.")
    return auth.interactive_login(auth.launch_context(p, headless=False))


def _try_headless_relogin(context):
    """Пробует тихий перелогин из .env на уже открытом контексте. Возвращает (session, status)."""
    log_warn("Сессия не работает — пробую тихий перелогин из HH_EMAIL/HH_PASSWORD.")
    if auth.headless_login(context):
        session, status = open_and_check(context)
        log(f"После тихого перелогина: {status.message}")
        return session, status
    log_warn("Тихий перелогин не удался (возможно, капча или 2FA).")
    return open_and_check(context)


def _acquire_noninteractive(p, args):
    """Безлюдный режим (--no-input): НИКОГДА не открываем видимое окно входа.

    Даже при --relogin: смену аккаунта без окна можно сделать только тихим входом
    из .env. Нет сессии / нет creds / перелогин не удался → уведомление и код 3.
    """
    if args.relogin:
        if not auth.has_login_credentials():
            log_err("--no-input --relogin без HH_EMAIL/HH_PASSWORD невозможен: "
                    "смена аккаунта требует входа, а окно открывать нельзя.")
            notify.session_expired()
            return EXIT_NEED_LOGIN
        log("Перелогин в безлюдном режиме: тихий вход из .env.")
        auth.clear_session()
    elif not auth.session_exists():
        log_err("Сохранённой сессии нет, а --no-input запрещает открывать окно входа.")
        log_err("Выполните вход вручную: hhcleaner login")
        notify.session_expired()
        return EXIT_NEED_LOGIN

    context = auth.launch_context(p, headless=True)
    session, status = open_and_check(context)
    log(f"Проверка: {status.message}")

    if not status.ok and auth.has_login_credentials():
        session, status = _try_headless_relogin(context)

    if not status.ok:
        log_err("Сессия не работает, а --no-input запрещает открывать окно входа.")
        if auth.has_login_credentials():
            log_err("Тихий перелогин споткнулся — вероятно, появилась капча или 2FA.")
        log_err("Выполните вход вручную: hhcleaner login")
        notify.session_expired()
        context.close()
        return EXIT_NEED_LOGIN

    return context, session


def _acquire_interactive(p, args):
    """Интерактивный режим: при протухшей сессии открываем видимое окно входа."""
    context = prepare_context(p, relogin=args.relogin, headed=args.headed)
    session, status = open_and_check(context)
    log(f"Проверка: {status.message}")

    if not status.ok and auth.has_login_credentials():
        session, status = _try_headless_relogin(context)

    if not status.ok:
        log_warn("Сессия не работает — открываю окно входа.")
        context.close()
        context = auth.interactive_login(auth.launch_context(p, headless=False))
        session, status = open_and_check(context)
        log(f"Проверка: {status.message}")
        if not status.ok:
            log_err("Вход не удался.")
            context.close()
            return EXIT_LOGIN_FAILED

    return context, session


def acquire_context(p, args):
    """Возвращает (context, session) с валидной сессией или int (код выхода).

    Содержит всю логику перелогина: тихий (через .env) и интерактивный. В
    --no-input видимое окно входа не открывается ни при каких условиях (fix #3).
    """
    if args.no_input:
        return _acquire_noninteractive(p, args)
    return _acquire_interactive(p, args)
