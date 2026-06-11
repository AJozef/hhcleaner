"""Сохранение и переиспользование сессии hh.ru через постоянный профиль браузера.

Идея: логинимся вручную один раз (капчу и код из почты/SMS проходит человек),
а Playwright хранит весь профиль браузера (куки + localStorage + кэш) в папке на
диске. Все последующие запуски открывают тот же профиль — без повторного входа и
без хранения пароля. Полный профиль разлогинивается реже, чем снимок storage_state.

Вход всегда ручной: данные, капчу и код из почты/SMS вводит человек в окне
браузера. Пароль нигде не хранится и не вводится автоматически.

ВНИМАНИЕ: папка профиля фактически даёт доступ к аккаунту — не делитесь ей.
"""
from __future__ import annotations

import os
import shutil

from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from config import (
    BROWSER_CHANNELS, LOGIN_URL, USER_DATA_DIR, ensure_app_dir,
    log, log_ok, log_section,
)
from ui_selectors import LOGIN_APPLICANT_CARD

_LOGIN_TIMEOUT_MS = 5 * 60 * 1000  # 5 минут на ручной вход


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
    if os.path.isdir(USER_DATA_DIR):
        shutil.rmtree(USER_DATA_DIR, ignore_errors=True)


# Маркер «запасной встроенный Chromium» (launch без channel) в очереди попыток.
_BUNDLED = ""
# Кэш сработавшего варианта на процесс: первый запуск перебирает каналы, дальше
# сразу берём рабочий — чтобы не платить таймаутом за отсутствующий Edge каждый раз.
_WORKING_CHANNEL: str | None = None


def _persistent_context(playwright, headless: bool, channel: str) -> BrowserContext:
    """launch_persistent_context для конкретного варианта браузера.

    channel == _BUNDLED — без указания channel (встроенный Chromium Playwright);
    иначе — системный браузер (msedge/chrome).
    """
    kwargs = {} if channel == _BUNDLED else {"channel": channel}
    return playwright.chromium.launch_persistent_context(
        USER_DATA_DIR, headless=headless, **kwargs
    )


def launch_context(playwright, headless: bool) -> BrowserContext:
    """Открывает постоянный контекст в системном браузере (Edge/Chrome).

    Перебирает BROWSER_CHANNELS, затем встроенный Chromium как запас. Системный
    браузер не нужно скачивать — на Windows 10/11 Edge есть всегда. Сработавший
    вариант кэшируется на процесс.

    Возвращает BrowserContext (у persistent-контекста нет отдельного Browser —
    закрывать нужно сам контекст: context.close()).
    """
    global _WORKING_CHANNEL  # pylint: disable=global-statement
    ensure_app_dir()
    os.makedirs(USER_DATA_DIR, exist_ok=True)

    order = [_WORKING_CHANNEL] if _WORKING_CHANNEL is not None else [*BROWSER_CHANNELS, _BUNDLED]
    last_err: Exception | None = None
    for channel in order:
        try:
            ctx = _persistent_context(playwright, headless, channel)
            _WORKING_CHANNEL = channel
            return ctx
        except (PlaywrightError, PlaywrightTimeout) as e:
            last_err = e

    raise LoginError(
        "Не удалось запустить браузер. Установите Microsoft Edge или Google Chrome "
        "(обычно Edge уже есть в Windows 10/11) и попробуйте снова."
    ) from last_err


def detect_browser(playwright) -> str | None:
    """Какой браузер доступен для входа: 'msedge'/'chrome'/'' (встроенный) или None.

    Пробует кратко запустить НЕпостоянный браузер (без блокировки профиля) —
    для диагностики (hhcleaner doctor). Возвращает первый рабочий вариант или None.
    """
    for channel in [*BROWSER_CHANNELS, _BUNDLED]:
        kwargs = {} if channel == _BUNDLED else {"channel": channel}
        try:
            browser = playwright.chromium.launch(headless=True, **kwargs)
            browser.close()
            return channel
        except (PlaywrightError, PlaywrightTimeout):
            continue
    return None


def interactive_login(context: BrowserContext) -> BrowserContext:
    """Ручной вход в уже открытом (видимом) контексте.

    Открывает форму логина, ждёт редиректа после успешного входа.
    Возвращает тот же context.
    """
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(LOGIN_URL)

    try:
        card = page.query_selector(LOGIN_APPLICANT_CARD)
        if card:
            card.click()
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    log_section("Вход в hh.ru")
    log("Роль «Я ищу работу» уже выбрана.")
    log("Введите данные и пройдите капчу/код из почты — скрипт продолжит сам.")

    try:
        page.wait_for_url(_login_succeeded, timeout=_LOGIN_TIMEOUT_MS)
    except PlaywrightTimeout as exc:
        raise LoginError(
            "Вход не завершился за 5 минут. Если форма заполнена — нажмите вход "
            "и пройдите капчу/2FA; затем запустите hhcleaner login ещё раз."
        ) from exc
    except PlaywrightError as exc:
        raise LoginError("Окно браузера закрыто до завершения входа.") from exc

    log_ok("Вход выполнен, профиль сохранён.")
    return context
