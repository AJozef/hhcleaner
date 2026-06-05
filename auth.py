"""Сохранение и переиспользование сессии hh.ru через постоянный профиль браузера.

Идея: логинимся вручную один раз (капчу и код из почты/SMS проходит человек),
а Playwright хранит весь профиль браузера (куки + localStorage + кэш) в папке на
диске. Все последующие запуски открывают тот же профиль — без повторного входа и
без хранения пароля. Полный профиль разлогинивается реже, чем снимок storage_state.

Если в .env заданы HH_EMAIL и HH_PASSWORD — форма заполняется автоматически;
капчу и 2FA всё равно нужно пройти вручную.

Профили позволяют вести несколько аккаунтов hh.ru. Аргумент profile передаётся
во все публичные функции; «default» — обратно совместимое поведение.
ВНИМАНИЕ: папка профиля фактически даёт доступ к аккаунту — не делитесь ей.
"""
from __future__ import annotations

import os
import shutil

from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from config import (
    LOGIN_URL, ensure_app_dir, get_user_data_dir,
    log, log_ok, log_section, log_warn,
)

# Старое расположение профиля (рядом со скриптом) — переносим один раз.
_LEGACY_USER_DATA_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".userdata"
)

_LOGIN_TIMEOUT_MS = 5 * 60 * 1000  # 5 минут на ручной вход


def _migrate_legacy_session(user_data_dir: str) -> None:
    """Переносит профиль из старого <проект>/.userdata в APP_DIR/userdata.

    Срабатывает только для профиля «default» (если старая папка есть, а новой
    ещё нет). После переноса условие больше не выполняется.
    """
    if os.path.isdir(_LEGACY_USER_DATA_DIR) and not os.path.isdir(user_data_dir):
        ensure_app_dir()
        try:
            shutil.move(_LEGACY_USER_DATA_DIR, user_data_dir)
            log(f"Профиль сессии перенесён в {user_data_dir}")
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # best-effort; не вышло — просто попросим войти заново


class LoginError(Exception):
    """Вход не удалось завершить (таймаут или окно закрыли до конца входа)."""


def _login_succeeded(url: str) -> bool:
    """Сигнал успешного входа: ушли из раздела /account/ на страницу hh.ru."""
    return "hh.ru/" in url and "/account/" not in url


def session_exists(profile: str = "default") -> bool:
    """True, если профиль браузера существует и непустой."""
    udd = get_user_data_dir(profile)
    if profile == "default":
        _migrate_legacy_session(udd)
    return os.path.isdir(udd) and bool(os.listdir(udd))


def clear_session(profile: str = "default") -> None:
    """Удаляет профиль браузера (для перелогина / смены аккаунта).

    Вызывать только когда контекст закрыт — иначе файлы профиля заняты.
    """
    udd = get_user_data_dir(profile)
    if os.path.isdir(udd):
        shutil.rmtree(udd, ignore_errors=True)


def launch_context(playwright, headless: bool, profile: str = "default") -> BrowserContext:
    """Открывает постоянный контекст браузера из профиля нужного аккаунта.

    Возвращает BrowserContext (у persistent-контекста нет отдельного Browser —
    закрывать нужно сам контекст: context.close()).
    """
    udd = get_user_data_dir(profile)
    if profile == "default":
        _migrate_legacy_session(udd)
    ensure_app_dir()
    os.makedirs(udd, exist_ok=True)
    return playwright.chromium.launch_persistent_context(udd, headless=headless)


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
