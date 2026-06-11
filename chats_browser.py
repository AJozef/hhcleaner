"""Резервный браузерный метод удаления чатов (через клики в UI chatik).

Используется автоматически как фолбэк, если chatik.hh.ru/chatik/api вернул
401/403. Работает через Playwright с полными куками браузера.

Медленнее API (навигация + клики вместо HTTP-запросов), но не зависит от
стабильности внутреннего API: работает до тех пор, пока жив веб-UI chatik.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from config import (
    BROWSER_CLICK_PAUSE,
    BROWSER_INIT_PAUSE,
    BROWSER_LEAVE_PAUSE,
    BROWSER_NAV_PAUSE,
    BROWSER_SCROLL_PAUSE,
    BROWSER_VACANCY_PAUSE,
    CHATIK_BASE,
    CHATIK_URL,
    OLD_CHATS_DAYS,
    log,
    log_ok,
    log_section,
    log_warn,
)
from ui_selectors import (
    ARCHIVED_VACANCY_TEXTS,
    CHAT_COMPANY,
    CHAT_LEAVE,
    CHAT_LINK,
    CHAT_LIST_CONTAINER,
    CHAT_MENU,
    CHAT_REJECTED_MARK,
    CHAT_REJECTED_TEXT,
    CHAT_TIME,
    CHAT_TITLE,
    CHATIK_LAYOUT,
    INTERVIEW_BUBBLE,
    INTERVIEW_TEXTS,
    VACANCY_INTENTION,
)

# Максимум проходов для delete_rejected_chats.
_MAX_ROUNDS = 6
# Ограничение скролла: защита на случай очень большого аккаунта.
_MAX_SCROLL_ITEMS = 2000


# ──────────────────────────── приватные хелперы ──────────────────────────────


def _open_chatik(page) -> bool:
    """Переходит на chatik и ждёт layout. True при успехе."""
    try:
        page.goto(CHATIK_URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_selector(CHATIK_LAYOUT, timeout=15000)
        return True
    except Exception as e:  # pylint: disable=broad-exception-caught
        log(f"  ! chatik не открылся: {e}")
        return False


def _full_url(href: str) -> str:
    return f"{CHATIK_BASE}{href}" if href.startswith("/") else href


def _scroll_collect_all(page) -> dict[str, dict]:
    """Прокручивает весь список чатов и возвращает {href: info_dict}.

    info_dict: href, company, title, is_rejected, date_text (str | None) —
    «сырой» текст подписи даты («01.06», «вчера», «пн», «31.12.25»).
    """
    page.evaluate(
        "(sel) => { const s = document.querySelector(sel); if (s) s.scrollTop = 0; }",
        CHAT_LIST_CONTAINER,
    )
    time.sleep(BROWSER_INIT_PAUSE)

    collected: dict[str, dict] = {}
    last_scroll = -1
    no_growth = 0

    while len(collected) < _MAX_SCROLL_ITEMS:
        for chat in page.query_selector_all(CHAT_LINK):
            href = chat.get_attribute("href")
            if not href or href in collected:
                continue
            try:
                red_el   = chat.query_selector(CHAT_REJECTED_MARK)
                title_el = chat.query_selector(CHAT_TITLE)
                cmp_el   = chat.query_selector(CHAT_COMPANY)
                is_rejected = bool(red_el) and CHAT_REJECTED_TEXT in (red_el.inner_text() or "")
                collected[href] = {
                    "href":        href,
                    "company":     cmp_el.inner_text().strip() if cmp_el else "",
                    "title":       title_el.inner_text().strip() if title_el else "",
                    "is_rejected": is_rejected,
                    "date_text":   _extract_date_text(chat),
                }
            except Exception:  # pylint: disable=broad-exception-caught
                continue

        result = page.evaluate("""(sel) => {
            const s = document.querySelector(sel);
            if (!s) return null;
            s.scrollTop += s.clientHeight * 0.5;
            return {
                scrollTop: s.scrollTop,
                scrollHeight: s.scrollHeight,
                clientHeight: s.clientHeight
            };
        }""", CHAT_LIST_CONTAINER)
        if not result:
            break
        at_bottom = (
            result["scrollTop"] + result["clientHeight"] >= result["scrollHeight"] - 5
        )
        if result["scrollTop"] == last_scroll or at_bottom:
            no_growth += 1
            if no_growth >= 3:
                break
        else:
            no_growth = 0
        last_scroll = result["scrollTop"]
        time.sleep(BROWSER_SCROLL_PAUSE)

    return collected


def _extract_date_text(chat_el) -> str | None:
    """Возвращает «сырой» текст подписи даты из карточки чата (или None).

    В текущей вёрстке chatik машиночитаемого <time datetime> нет — дата живёт
    текстом в [data-qa='chat-cell-creation-time']: «01.06», «вчера», «пн»,
    «31.12.25». Разбор — в _parse_chat_date_text.
    """
    try:
        el = chat_el.query_selector(CHAT_TIME)
        if el:
            return (el.inner_text() or "").strip()
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    return None


# Сокращённые названия дней недели в подписи даты chatik → номер дня (Пн=0..Вс=6).
_WEEKDAYS = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}


def _parse_chat_date_text(text: str | None, now: datetime | None = None) -> datetime | None:
    """Разбирает подпись даты chatik в дату (tz-aware UTC) или None.

    Форматы hh.ru (наблюдения на 2026-06):
      • «сегодня» или время «ЧЧ:ММ»          → сегодня;
      • «вчера»                              → вчера;
      • «пн»…«вс» (сокр. день недели)        → ближайший прошедший такой день
                                               (в окне 2–6 дней назад);
      • «ДД.ММ» (без года)                   → текущий год (8+ дней назад);
      • «ДД.ММ.ГГ» / «ДД.ММ.ГГГГ»            → явный год (прошлые годы).

    Дата возвращается с точностью до суток (полночь UTC) — old-chats всё равно
    сравнивает по дню. Нераспознанное → None (чат пропускается, не удаляется).
    """
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    t = text.strip().lower().rstrip(".")

    if t == "сегодня" or re.fullmatch(r"\d{1,2}:\d{2}", t):
        return today
    if t == "вчера":
        return today - timedelta(days=1)
    if t in _WEEKDAYS:
        delta = (today.weekday() - _WEEKDAYS[t]) % 7
        return today - timedelta(days=delta or 7)

    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?", t)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        if year is None:
            y = today.year
        else:
            y = int(year) if len(year) == 4 else 2000 + int(year)
        try:
            return datetime(y, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _leave_from_current_page(page, company: str, title: str) -> bool:
    """Покидает чат, предполагая, что его страница уже открыта. True при успехе."""
    menu = page.query_selector(CHAT_MENU)
    if not menu:
        return False
    menu.click()
    time.sleep(BROWSER_CLICK_PAUSE)
    leave = page.query_selector(CHAT_LEAVE)
    if not leave:
        return False
    leave.click()
    log(f"    Покинут: {company} — {title}")
    time.sleep(BROWSER_LEAVE_PAUSE)
    return True


# Открытие страницы чата иногда падает транзиентно: ERR_NETWORK_IO_SUSPENDED
# (ОС душит сетевой IO, когда машина на миг засыпает), timeout, перебивка
# навигации. Без повтора такой чат молча выпадал бы из проверки на архив.
_GOTO_RETRIES = 3
_GOTO_RETRY_PAUSE = 1.5


def _open_chat_page(page, href: str) -> bool:
    """Открывает страницу чата с повтором при транзиентном сетевом сбое.

    True при успехе; иначе логирует последнюю ошибку и возвращает False.
    """
    url = _full_url(href)
    last_err: Exception | None = None
    for attempt in range(_GOTO_RETRIES):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return True
        except Exception as e:  # pylint: disable=broad-exception-caught
            last_err = e
            if attempt < _GOTO_RETRIES - 1:
                time.sleep(_GOTO_RETRY_PAUSE)
    log(f"    ! Не открылся {href} ({_GOTO_RETRIES} попытки): {last_err}")
    return False


def _leave_chat(page, href: str, company: str, title: str) -> bool:
    """Открывает чат и покидает его. True при успехе."""
    if not _open_chat_page(page, href):
        return False
    time.sleep(BROWSER_NAV_PAUSE)
    return _leave_from_current_page(page, company, title)


def _norm(text: str | None) -> str:
    """Нормализует текст для подстрочного матчинга: &nbsp;→пробел, нижний регистр."""
    return (text or "").replace("\xa0", " ").strip().lower()


def _text_matches(text: str | None, needles) -> bool:
    """True, если нормализованный text содержит любую из подстрок needles."""
    t = _norm(text)
    return any(n in t for n in needles)


def _any_badge_matches(page, selector: str, needles) -> bool:
    """True, если текст любого элемента по selector содержит подстроку из needles."""
    try:
        elements = page.query_selector_all(selector)
    except Exception:  # pylint: disable=broad-exception-caught
        return False
    for el in elements:
        try:
            if _text_matches(el.inner_text(), needles):
                return True
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return False


def _is_archived_in_current_page(page) -> bool:
    """True, если на открытой странице чата вакансия помечена как неактивная.

    Маркер — бейдж-«интенция» с текстом вида «Вакансия в архиве»
    (см. VACANCY_INTENTION / ARCHIVED_VACANCY_TEXTS).
    """
    return _any_badge_matches(page, VACANCY_INTENTION, ARCHIVED_VACANCY_TEXTS)


def _is_interview_in_current_page(page) -> bool:
    """True, если в открытом чате есть событие-собеседование (пузырь «Собеседование»).

    Аналог applicantState=INTERVIEW из API — такие чаты НЕ трогаем.
    """
    return _any_badge_matches(page, INTERVIEW_BUBBLE, INTERVIEW_TEXTS)


# ──────────────────────────── публичные функции ──────────────────────────────


def delete_rejected_chats(context, dry_run: bool = False, limit: int | None = None) -> int:
    """Резервный метод: удаляет чаты с отказами через браузер.

    Многоходовой: каждый проход заново прокручивает список, пока отказов не
    останется. limit — страховочный лимит на общее число удалений (None = без).
    """
    log_section("Удаление чатов с отказами (браузер)")
    page = context.new_page()
    total_deleted = 0

    for round_num in range(1, _MAX_ROUNDS + 1):
        log(f"\n--- Проход {round_num} ---")
        if not _open_chatik(page):
            break

        found = _scroll_collect_all(page)
        targets = [v for v in found.values() if v["is_rejected"]]
        log(f"  Отказов найдено: {len(targets)}")
        if not targets:
            log_ok("Чатов с отказами больше нет.")
            break

        if limit is not None:
            remaining = limit - total_deleted
            if remaining <= 0:
                log_warn(f"Достигнут лимит --max-delete ({limit}) — останавливаюсь.")
                break
            targets = targets[:remaining]

        if dry_run:
            log(f"  [dry-run] Было бы удалено: {len(targets)}")
            break

        deleted = sum(
            1 for item in targets
            if _leave_chat(page, item["href"], item["company"], item["title"])
        )
        log(f"  Удалено в проходе: {deleted}")
        total_deleted += deleted
        if deleted == 0:
            log_warn("Ничего не удалилось — выхожу.")
            break

    log_ok(f"Итого чатов-отказов удалено: {total_deleted}")
    page.close()
    return total_deleted


def delete_old_chats_browser(
    context,
    days: int = OLD_CHATS_DAYS,
    dry_run: bool = False,
    cutoff: datetime | None = None,
    limit: int | None = None,
) -> int:
    """Резервный метод: удаляет чаты старше N дней (или старше cutoff) через браузер.

    cutoff — абсолютная дата среза (из --since). Приоритет над days.
    limit — страховочный лимит на число удалений (None = без ограничения).
    Дату читает из текстовой подписи в списке чатов (один прокрут) и разбирает
    через _parse_chat_date_text. Чат без распознанной даты пропускается.
    """
    log_section(f"Удаление чатов старше {days} дней (браузер)")
    effective_cutoff = cutoff or (datetime.now(timezone.utc) - timedelta(days=days))
    log(f"Порог: {effective_cutoff.strftime('%Y-%m-%d')}")

    page = context.new_page()
    if not _open_chatik(page):
        page.close()
        return 0

    found = _scroll_collect_all(page)
    log(f"Всего чатов собрано: {len(found)}")

    old_items = []
    skipped = 0
    for item in found.values():
        dt = _parse_chat_date_text(item["date_text"])
        if dt is None:
            skipped += 1
        elif dt < effective_cutoff:
            old_items.append(item)

    log(f"Старых чатов: {len(old_items)}")
    if skipped:
        log_warn(f"Пропущено (дата не определена в DOM): {skipped}")

    if limit is not None:
        old_items = old_items[:limit]

    if dry_run:
        log(f"  [dry-run] Было бы удалено: {len(old_items)}")
        page.close()
        return 0

    deleted = sum(
        1 for item in old_items
        if _leave_chat(page, item["href"], item["company"], item["title"])
    )
    log_ok(f"Итого старых чатов удалено: {deleted}")
    page.close()
    return deleted


def delete_archived_vacancy_chats_browser(
    context, dry_run: bool = False, limit: int | None = None
) -> int:
    """Резервный метод: удаляет чаты по архивным вакансиям через браузер.

    limit — страховочный лимит на число удалений (None = без ограничения).
    O(n) по числу чатов — медленнее других методов.
    """
    log_section("Удаление чатов по архивным вакансиям (браузер)")
    log_warn("Проверяется каждый чат — прогон займёт больше времени чем обычно.")

    page = context.new_page()
    if not _open_chatik(page):
        page.close()
        return 0

    found = _scroll_collect_all(page)
    log(f"Всего чатов для проверки: {len(found)}")

    to_leave = []
    for i, item in enumerate(found.values(), 1):
        log(f"  [{i}/{len(found)}] {item['company']}")
        if not _open_chat_page(page, item["href"]):
            continue
        time.sleep(BROWSER_VACANCY_PAUSE)

        if _is_interview_in_current_page(page):
            log("    Пропущено (собеседование).")
            continue

        if _is_archived_in_current_page(page):
            to_leave.append(item)

    log(f"Архивных вакансий найдено: {len(to_leave)}")
    if limit is not None:
        to_leave = to_leave[:limit]
    if dry_run:
        log(f"  [dry-run] Было бы удалено: {len(to_leave)}")
        page.close()
        return 0

    deleted = sum(
        1 for item in to_leave
        if _leave_chat(page, item["href"], item["company"], item["title"])
    )
    log_ok(f"Итого чатов по архивным вакансиям удалено: {deleted}")
    page.close()
    return deleted
