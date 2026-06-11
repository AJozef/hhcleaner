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
# Вход всегда ручной — селекторы полей формы не нужны; цепляемся только за
# карточку выбора роли «соискатель», чтобы её предвыбрать.
LOGIN_APPLICANT_CARD = "[data-qa*='account-type-card-APPLICANT']"

# ─── Список чатов chatik (chats_browser.py) ────────────────────────────────
CHATIK_LAYOUT       = "[data-qa='chatik-layout']"
CHAT_LIST_CONTAINER = '[class*="chats--"]'   # скролл-контейнер списка (внутри JS evaluate)
CHAT_LINK           = "a[data-qa^='chatik-open-chat-']"
CHAT_REJECTED_MARK  = "[class*='last-message-color_red']"
CHAT_TITLE          = "[class*='title--']"
CHAT_COMPANY        = "[class*='subtitle--']"
CHAT_REJECTED_TEXT  = "Отказ"  # текст внутри красного маркера последнего сообщения

# Дата последнего сообщения в карточке чата. Это НЕ machine-readable <time>:
# текст вида «01.06», «вчера», «пн», «31.12.25» — разбирается в
# chats_browser._parse_chat_date_text.
CHAT_TIME = "[data-qa='chat-cell-creation-time'], [class*='time--']"

# ─── Меню чата и выход из чата (chats_browser.py) ──────────────────────────
CHAT_MENU  = "[data-qa='chatik-chat-menu']"
CHAT_LEAVE = "[data-qa='chatik-chat-leave-chat']"

# ─── Признаки в ОТКРЫТОМ чате (chats_browser.py) ──────────────────────────
# Статус вакансии показывается «интенцией» — бейджем в шапке чата:
#   <span class="intention--HASH">Вакансия в&nbsp;архиве</span>
# Класс обфусцирован, поэтому цепляемся за подстроку intention-- (это именно
# внутренний бейдж, а не intention-wrapper--) и сверяем его текст.
VACANCY_INTENTION = "[class*='intention--']"
# Подстроки (нижний регистр; &nbsp нормализуется в пробел в коде), означающие,
# что вакансия больше не активна. Реальная формулировка hh — «Вакансия в архиве».
# «удалена» (а не «удалён/удален») — чтобы матчить «Вакансия удалена», но не
# зацепить «удалённая работа» / «удаленная работа» (формат, а не статус).
ARCHIVED_VACANCY_TEXTS = (
    "архив", "закрыт", "удалена", "снят", "не существ", "неактивн",
)
# Собеседование показывается отдельным «пузырём»-событием в переписке:
#   <div data-qa="chat-bubble-title">Собеседование</div>
# Такие чаты по архивным вакансиям НЕ трогаем (равнозначно applicantState=INTERVIEW).
INTERVIEW_BUBBLE = "[data-qa='chat-bubble-title']"
INTERVIEW_TEXTS = ("собеседование", "интервью")

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
