"""Резервный браузерный метод удаления чатов (через клики в UI chatik).

Используется автоматически как фолбэк, если chatik.hh.ru/chatik/api вернул
401/403. Работает через Playwright с полными куками браузера.

Медленнее API (навигация + клики вместо HTTP-запросов), но не зависит от
стабильности внутреннего API: работает до тех пор, пока жив веб-UI chatik.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import (
    CHATIK_URL, OLD_CHATS_DAYS, log, log_ok, log_section, log_warn, parse_iso_datetime,
)
from ui_selectors import (
    ARCHIVED_VACANCY_MARKERS,
    ARCHIVED_VACANCY_TEXT_HOSTS,
    ARCHIVED_VACANCY_TEXTS,
    CHAT_COMPANY,
    CHAT_LEAVE,
    CHAT_LINK,
    CHAT_LIST_CONTAINER,
    CHAT_MENU,
    CHAT_REJECTED_MARK,
    CHAT_REJECTED_TEXT,
    CHAT_TIME_FALLBACKS,
    CHAT_TIME_PRIMARY,
    CHAT_TITLE,
    CHATIK_LAYOUT,
    INTERVIEW_MARKERS,
)

_CHATIK_BASE = "https://chatik.hh.ru"

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
    return f"{_CHATIK_BASE}{href}" if href.startswith("/") else href


def _scroll_collect_all(page) -> dict[str, dict]:
    """Прокручивает весь список чатов и возвращает {href: info_dict}.

    info_dict: href, company, title, is_rejected, date_iso (str | None).
    """
    page.evaluate(
        "(sel) => { const s = document.querySelector(sel); if (s) s.scrollTop = 0; }",
        CHAT_LIST_CONTAINER,
    )
    time.sleep(1.5)

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
                    "date_iso":    _extract_date_iso(chat),
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
        time.sleep(0.8)

    return collected


def _extract_date_iso(chat_el) -> Optional[str]:
    """Ищет ISO-дату последнего сообщения в элементе списка чатов.

    Порядок попыток (от надёжного к ненадёжному):
    1. <time datetime="..."> — стандарт HTML5, machine-readable.
    2. [data-qa*='date|time'] или [class*='date--|time--'] с атрибутом datetime.
    Без datetime-атрибута возвращаем None — дата не определена, чат не удаляем.
    """
    try:
        el = chat_el.query_selector(CHAT_TIME_PRIMARY)
        if el:
            return el.get_attribute("datetime")
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    for sel in CHAT_TIME_FALLBACKS:
        try:
            el = chat_el.query_selector(sel)
            if el:
                v = el.get_attribute("datetime")
                if v:
                    return v
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return None


def _leave_from_current_page(page, company: str, title: str) -> bool:
    """Покидает чат, предполагая, что его страница уже открыта. True при успехе."""
    menu = page.query_selector(CHAT_MENU)
    if not menu:
        return False
    menu.click()
    time.sleep(0.7)
    leave = page.query_selector(CHAT_LEAVE)
    if not leave:
        return False
    leave.click()
    log(f"    Покинут: {company} — {title}")
    time.sleep(0.8)
    return True


def _leave_chat(page, href: str, company: str, title: str) -> bool:
    """Открывает чат и покидает его. True при успехе."""
    try:
        page.goto(_full_url(href), wait_until="domcontentloaded", timeout=15000)
    except Exception as e:  # pylint: disable=broad-exception-caught
        log(f"    ! Не открылся {href}: {e}")
        return False
    time.sleep(1.2)
    return _leave_from_current_page(page, company, title)


def _is_archived_in_current_page(page) -> bool:
    """Проверяет признаки архивной вакансии на уже открытой странице чата."""
    for sel in ARCHIVED_VACANCY_MARKERS:
        try:
            if page.query_selector(sel):
                return True
        except Exception:  # pylint: disable=broad-exception-caught
            continue

    for sel in ARCHIVED_VACANCY_TEXT_HOSTS:
        try:
            el = page.query_selector(sel)
            if el:
                text = el.inner_text() or ""
                if any(m in text for m in ARCHIVED_VACANCY_TEXTS):
                    return True
        except Exception:  # pylint: disable=broad-exception-caught
            continue
    return False


def _safe_query(page, sel: str) -> bool:
    """query_selector без исключений — для использования в any()."""
    try:
        return bool(page.query_selector(sel))
    except Exception:  # pylint: disable=broad-exception-caught
        return False


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
    cutoff: Optional[datetime] = None,
    limit: int | None = None,
) -> int:
    """Резервный метод: удаляет чаты старше N дней (или старше cutoff) через браузер.

    cutoff — абсолютная дата среза (из --since). Приоритет над days.
    limit — страховочный лимит на число удалений (None = без ограничения).
    Дату читает из <time datetime="..."> в списке чатов (один прокрут).
    Чат без определённой даты пропускается.
    """
    log_section(f"Удаление чатов старше {days} дней (браузер-резерв)")
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
        dt = parse_iso_datetime(item["date_iso"])
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
    log_section("Удаление чатов по архивным вакансиям (браузер-резерв)")
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
        try:
            page.goto(_full_url(item["href"]), wait_until="domcontentloaded", timeout=15000)
        except Exception as e:  # pylint: disable=broad-exception-caught
            log(f"    ! Не открылся: {e}")
            continue
        time.sleep(1.0)

        is_interview = any(_safe_query(page, sel) for sel in INTERVIEW_MARKERS)
        if is_interview:
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
