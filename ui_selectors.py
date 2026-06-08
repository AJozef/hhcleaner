"""Все CSS / data-qa селекторы и текстовые маркеры веб-UI hh.ru и chatik.

hh.ru периодически меняет вёрстку, и тогда какой-нибудь шаг перестаёт находить
элементы. Чинить нужно здесь — это единственное место, где живут селекторы;
по остальному коду их искать не придётся.

Соглашение об именах:
    *_INPUT / *_BUTTON / одиночные строки  — готовый аргумент для query_selector
        (может содержать запятые = «любой из перечисленных»).
    *_CANDIDATES / *_MARKERS / кортежи      — код перебирает варианты по одному
        (any() / цикл с индивидуальной обработкой каждого).
    *_TEXTS                                 — текстовые подстроки, не селекторы.

Назван ui_selectors (а не selectors), чтобы не затенять stdlib-модуль selectors,
который тянет asyncio под Playwright.
"""
from __future__ import annotations

# ─── Форма входа (auth.py) ─────────────────────────────────────────────────
LOGIN_USERNAME_INPUT = "[data-qa='login-input-username'], input[name='login'], input[type='email']"
LOGIN_PASSWORD_INPUT = "[data-qa='login-input-password'], input[type='password']"
LOGIN_SUBMIT_BUTTON  = "[data-qa='account-login-submit'], button[type='submit']"
LOGIN_APPLICANT_CARD = "[data-qa*='account-type-card-APPLICANT']"

# ─── Список чатов chatik (chats_browser.py) ────────────────────────────────
CHATIK_LAYOUT       = "[data-qa='chatik-layout']"
CHAT_LIST_CONTAINER = '[class*="chats--"]'   # скролл-контейнер списка (внутри JS evaluate)
CHAT_LINK           = "a[data-qa^='chatik-open-chat-']"
CHAT_REJECTED_MARK  = "[class*='last-message-color_red']"
CHAT_TITLE          = "[class*='title--']"
CHAT_COMPANY        = "[class*='subtitle--']"
CHAT_REJECTED_TEXT  = "Отказ"  # текст внутри красного маркера последнего сообщения

# Дата последнего сообщения: сначала надёжный <time datetime>, потом запасные.
CHAT_TIME_PRIMARY   = "time[datetime]"
CHAT_TIME_FALLBACKS = (
    "[data-qa*='date']", "[data-qa*='time']",
    "[class*='date--']", "[class*='time--']",
)

# ─── Меню чата и выход из чата (chats_browser.py) ──────────────────────────
CHAT_MENU  = "[data-qa='chatik-chat-menu']"
CHAT_LEAVE = "[data-qa='chatik-chat-leave-chat']"

# ─── Признаки архивной вакансии в открытом чате (chats_browser.py) ─────────
# Прямые маркеры: достаточно найти любой из них.
ARCHIVED_VACANCY_MARKERS = (
    "[data-qa*='vacancy-archived']", "[data-qa*='vacancy-deleted']",
    "[class*='vacancy-archived']",   "[class*='vacancy-deleted']",
    "[class*='archived-vacancy']",
)
# Контейнеры, в тексте которых ищем фразы ARCHIVED_VACANCY_TEXTS.
ARCHIVED_VACANCY_TEXT_HOSTS = (
    "[data-qa*='vacancy']", "[class*='vacancy-info']",
    "[data-qa*='chat-header']", "[class*='chat-header']",
    "[class*='vacancy']",
)
ARCHIVED_VACANCY_TEXTS = (
    "Вакансия закрыта", "вакансия закрыта",
    "Вакансия удалена", "вакансия удалена",
    "Вакансия не существует",
    "Вакансия снята",
)
# Признаки собеседования — такие чаты по архивным вакансиям НЕ трогаем.
INTERVIEW_MARKERS = (
    "[data-qa*='interview']", "[class*='interview']",
    "[data-qa*='INTERVIEW']", "[class*='applicant-state_interview']",
)

# ─── Раздел откликов / negotiations (negotiations.py) ──────────────────────
NEGOTIATIONS_LIST         = "[data-qa='negotiations-list']"
NEGOTIATIONS_ITEM         = "[data-qa='negotiations-item']"
NEGOTIATIONS_ITEM_LINK    = "[data-qa='negotiations-item'] a[href]"
NEGOTIATIONS_DISCARD      = "[data-qa*='negotiations-item-discard']"
NEGOTIATIONS_CHECKBOX     = "input[data-qa='negotiations-item-checkbox']"
NEGOTIATIONS_BATCH_REMOVE = "[data-qa='negotiations-batch-remove']"

# Окно подтверждения удаления откликов.
MODAL_CONTAINER = "[role='dialog'], [data-qa*='modal'], [class*='magritte-modal'], .bloko-modal"
# Кнопки подтверждения по data-qa (приоритетнее, чем поиск по тексту кнопки).
DELETE_CONFIRM_BUTTONS = [
    "[data-qa='magritte_modal_buttons_delete']",
    "[data-qa='abandon-negotiation-submit']",
    "[data-qa='delete-confirmation-submit']",
    "[data-qa='negotiations-batch-remove-confirm']",
    "[data-qa='negotiations-delete-submit']",
]
# Любые видимые кнопки окна — для отладочного дампа при смене вёрстки.
MODAL_ANY_BUTTON = "button, [role='button'], a[data-qa]"
