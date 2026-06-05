"""Сохранение и переиспользование сессии hh.ru через постоянный профиль браузера.

Идея: логинимся вручную один раз (капчу и код из почты/SMS проходит человек),
а Playwright хранит весь профиль браузера (куки + localStorage + кэш) в папке на
диске. Все последующие запуски открывают тот же профиль — без повторного входа и
без хранения пароля. Полный профиль разлогинивается реже, чем снимок storage_state.

Если в .env заданы HH_EMAIL и HH_PASSWORD — форма заполняется автоматически;
капчу и 2FA всё равно нужно пройти вручную.

ВНИМАНИЕ: папка профиля фактически даёт доступ к аккаунту — не делитесь ей.
"""
from __future__ import annotations

import os

from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from config import (
    LOGIN_URL, USER_DATA_DIR, ensure_app_dir,
    log, log_ok, log_section, log_warn,
)

_LOGIN_TIMEOUT_MS = 5 * 60 * 1000  # 5 минут на ручной вход
_HEADLESS_LOGIN_TIMEOUT_MS = 15 * 1000  # 15 секунд на тихий перелогин


class LoginError(Exception):
    """Вход не удалось завершить (таймаут или окно закрыли до конца входа)."""


def _login_succeeded(url: str) -> bool:
    """Сигнал успешного входа: ушли из раздела /account/ на страницу hh.ru."""
    return "hh.ru/" in url and "/account/" not in url


def session_exists() -> bool:
    """True, если профиль браузера существует и непустой."""
    return os.path.isdir(USER_DATA_DIR) and bool(os.listdir(USER_DATA_DIR))


def clear_session() -> None:
    """Удаляет профиль браузера (для перелогина / смены аккаунта).

    Вызывать только когда контекст закрыт — иначе файлы профиля заняты.
    """
    import shutil  # pylint: disable=import-outside-toplevel
    if os.path.isdir(USER_DATA_DIR):
        shutil.rmtree(USER_DATA_DIR, ignore_errors=True)


def launch_context(playwright, headless: bool) -> BrowserContext:
    """Открывает постоянный контекст браузера из сохранённого профиля.

    Возвращает BrowserContext (у persistent-контекста нет отдельного Browser —
    закрывать нужно сам контекст: context.close()).
    """
    ensure_app_dir()
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    return playwright.chromium.launch_persistent_context(USER_DATA_DIR, headless=headless)


def _autofill_login(page, email: str, password: str) -> None:
    """Пытается автоматически заполнить форму логина из переменных окружения.

    Работает по принципу best-effort: если селекторы не совпали — тихо
    пропускает.
    """
    if not email:
        return

    try:
        inp = page.wait_for_selector(
            "[data-qa='login-input-username'], input[name='login'], input[type='email']",
            timeout=5000,
            state="visible",
        )
        if inp:
            inp.fill(email)
            btn = page.query_selector(
                "[data-qa='account-login-submit'], button[type='submit']"
            )
            if btn:
                btn.click()
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    if not password:
        return

    try:
        inp = page.wait_for_selector(
            "[data-qa='login-input-password'], input[type='password']",
            timeout=5000,
            state="visible",
        )
        if inp:
            inp.fill(password)
            btn = page.query_selector(
                "[data-qa='account-login-submit'], button[type='submit']"
            )
            if btn:
                btn.click()
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def has_login_credentials() -> bool:
    """True, если HH_EMAIL и HH_PASSWORD заданы в env (оба непустые после strip)."""
    return bool(
        os.environ.get("HH_EMAIL", "").strip()
        and os.environ.get("HH_PASSWORD", "").strip()
    )


def headless_login(context: BrowserContext) -> bool:
    """Тихий вход через HH_EMAIL/HH_PASSWORD — без участия человека.

    Используется в --no-input при протухшей сессии: пытаемся за ~15 секунд
    автоматически залогиниться через автозаполнение формы. Капча и 2FA
    автоматически не проходятся — тогда возвращаем False, и вызывающий код
    решает что делать (notify + exit 3).
    """
    if not has_login_credentials():
        return False

    email    = os.environ.get("HH_EMAIL", "").strip()
    password = os.environ.get("HH_PASSWORD", "").strip()

    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=15000)
    except (PlaywrightError, PlaywrightTimeout):
        return False

    try:
        card = page.query_selector("[data-qa*='account-type-card-APPLICANT']")
        if card:
            card.click()
    except Exception:  # pylint: disable=broad-exception-caught
        pass  # карточку выбора роли могут не показать — не критично

    _autofill_login(page, email, password)

    try:
        page.wait_for_url(_login_succeeded, timeout=_HEADLESS_LOGIN_TIMEOUT_MS)
        return True
    except (PlaywrightError, PlaywrightTimeout):
        return False


def interactive_login(context: BrowserContext) -> BrowserContext:
    """Ручной вход в уже открытом (видимом) контексте.

    Открывает форму логина, ждёт редиректа после успешного входа.
    Возвращает тот же context.
    """
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(LOGIN_URL)

    try:
        card = page.query_selector("[data-qa*='account-type-card-APPLICANT']")
        if card:
            card.click()
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    log_section("Вход в hh.ru")

    email    = os.environ.get("HH_EMAIL", "").strip()
    password = os.environ.get("HH_PASSWORD", "").strip()

    if email and password:
        log_ok("Данные из .env найдены — заполняю форму автоматически.")
        log_warn("Если появится капча или 2FA — пройдите их вручную в окне браузера.")
        _autofill_login(page, email, password)
    else:
        log("Роль «Я ищу работу» уже выбрана.")
        log("Введите данные и пройдите капчу/код из почты — скрипт продолжит сам.")
        log_warn("Подсказка: задайте HH_EMAIL и HH_PASSWORD в .env для автозаполнения.")

    try:
        page.wait_for_url(_login_succeeded, timeout=_LOGIN_TIMEOUT_MS)
    except PlaywrightTimeout as exc:
        raise LoginError(
            "Вход не завершился за 5 минут. Если форма заполнена — нажмите вход "
            "и пройдите капчу/2FA; затем запустите --login-only ещё раз."
        ) from exc
    except PlaywrightError as exc:
        raise LoginError("Окно браузера закрыто до завершения входа.") from exc

    log_ok("Вход выполнен, профиль сохранён.")
    return context
