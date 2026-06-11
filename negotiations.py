"""Удаление откликов (переговоров) со статусом «отказ» через браузер."""
from __future__ import annotations

import re

from config import NEGOTIATIONS_URL, log, log_ok, log_section, log_warn
from ui_selectors import (
    DELETE_CONFIRM_BUTTONS,
    MODAL_ANY_BUTTON,
    MODAL_CONTAINER,
    NEGOTIATIONS_BATCH_REMOVE,
    NEGOTIATIONS_CHECKBOX,
    NEGOTIATIONS_DISCARD,
    NEGOTIATIONS_ITEM,
    NEGOTIATIONS_ITEM_LINK,
    NEGOTIATIONS_LIST,
)


def _dump_dialog_buttons(page) -> None:
    """Логирует видимые кнопки окна (data-qa и текст) для отладки селектора."""
    log_warn("Видимые кнопки (data-qa | текст) — пришлите их, чтобы уточнить селектор:")
    scope = page.query_selector(MODAL_CONTAINER) or page
    for btn in scope.query_selector_all(MODAL_ANY_BUTTON):
        try:
            if not btn.is_visible():
                continue
            data_qa = btn.get_attribute("data-qa") or "-"
            text = (btn.inner_text() or "").strip().replace("\n", " ")[:50]
            log_warn(f"   {data_qa} | {text}")
        except Exception:  # pylint: disable=broad-exception-caught
            continue


def _confirm_deletion(page) -> bool:
    """Находит и нажимает кнопку подтверждения в модальном окне. True при успехе."""
    try:
        page.wait_for_selector(", ".join(DELETE_CONFIRM_BUTTONS), timeout=5000, state="visible")
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    for sel in DELETE_CONFIRM_BUTTONS:
        btn = page.query_selector(sel)
        if btn and btn.is_visible():
            log(f"  Подтверждаю: {sel}")
            btn.click()
            return True

    try:
        page.wait_for_selector(MODAL_CONTAINER, timeout=5000, state="visible")
    except Exception:  # pylint: disable=broad-exception-caught
        return False

    modal = page.locator(MODAL_CONTAINER)
    if modal.count() == 0:
        return False
    scope = modal.last

    name_patterns = [re.compile(r"^Удалить"), re.compile(r"[Уу]далить"), re.compile(r"^Да\b")]
    for pattern in name_patterns:
        confirm = scope.get_by_role("button", name=pattern)
        if confirm.count() > 0:
            try:
                confirm.first.click(timeout=3000)
                log(f"  Подтверждаю кнопкой окна по тексту: {pattern.pattern}")
                return True
            except Exception:  # pylint: disable=broad-exception-caught
                continue

    return False


_PAGE_LIMIT = 100  # предохранитель от бесконечной пагинации


def _open_negotiations(page, idx: int) -> bool:
    """Открывает страницу откликов по индексу (0-based) и ждёт список."""
    url = NEGOTIATIONS_URL if idx == 0 else f"{NEGOTIATIONS_URL}?page={idx}"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_selector(NEGOTIATIONS_LIST, timeout=15000)
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _page_key(page) -> str | None:
    """Отпечаток страницы — href первого отклика (или None, если откликов нет)."""
    first = page.query_selector(NEGOTIATIONS_ITEM_LINK)
    return first.get_attribute("href") if first else None


def _rejected_items(page) -> list:
    """Список откликов-отказов на текущей странице (по пометке discard)."""
    return [
        item for item in page.query_selector_all(NEGOTIATIONS_ITEM)
        if item.query_selector(NEGOTIATIONS_DISCARD)
    ]


def _goto_negotiations(page) -> bool:
    """Переходит к разделу откликов с повторами."""
    for attempt in range(5):
        if _open_negotiations(page, 0):
            log("Страница откликов загружена.")
            return True
        log(f"Попытка {attempt + 1}: текущий URL {page.url}, жду...")
    log("Не удалось перейти к откликам, пропускаю.")
    return False


def _count_all_rejected(page) -> int:
    """Проходит ВСЕ страницы откликов и считает отказы (для dry-run)."""
    total = 0
    seen: set[str] = set()
    for idx in range(_PAGE_LIMIT):
        if not _open_negotiations(page, idx):
            break
        key = _page_key(page)
        if key is None or key in seen:
            break
        seen.add(key)
        rejected = len(_rejected_items(page))
        log(f"Страница {idx + 1}: отказов {rejected}")
        total += rejected
    return total


def delete_rejected_negotiations(
    page,
    dry_run: bool = False,
    limit: int | None = None,
) -> int:
    """Отмечает и удаляет все отклики со статусом «отказ» по всем страницам.

    limit — максимальное число удалений (None = без ограничения).
    """
    log_section("Удаление откликов с отказами")

    if not _goto_negotiations(page):
        return 0

    if dry_run:
        total = _count_all_rejected(page)
        log(f"  [dry-run] Было бы удалено: {total}")
        log_ok(f"Итого откликов под удаление: {total}")
        return 0

    total_deleted = 0
    for round_num in range(1, _PAGE_LIMIT + 1):
        # Каждый проход ищем отказы заново с первой страницы.
        target = None
        seen: set[str] = set()
        for idx in range(_PAGE_LIMIT):
            if not _open_negotiations(page, idx):
                break
            key = _page_key(page)
            if key is None or key in seen:
                break
            seen.add(key)
            rejected = _rejected_items(page)
            if rejected:
                target = rejected
                break

        if not target:
            log_ok("Отказов больше нет.")
            break

        checked = 0
        for item in target:
            checkbox = item.query_selector(NEGOTIATIONS_CHECKBOX)
            if checkbox and not checkbox.is_checked():
                checkbox.check()
                checked += 1
        log(f"Проход {round_num}: отмечено отказов {checked}")
        if checked == 0:
            log_warn("Отказы найдены, но не удалось отметить чекбоксы — останавливаюсь.")
            break

        delete_btn = page.query_selector(NEGOTIATIONS_BATCH_REMOVE)
        if not delete_btn:
            log("Не нашёл кнопку 'Удалить выбранное'.")
            break
        delete_btn.click()

        if not _confirm_deletion(page):
            log_warn("Не нашёл кнопку подтверждения в окне — вёрстка изменилась.")
            _dump_dialog_buttons(page)
            break

        total_deleted += checked
        log(f"  Удалено: {checked}")
        # Ждём, пока список откликов обновится после удаления. wait_for_selector
        # надёжнее фиксированного таймаута: продолжаем сразу, когда DOM готов.
        try:
            page.wait_for_selector(NEGOTIATIONS_LIST, timeout=5000, state="visible")
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        if limit is not None and total_deleted >= limit:
            log_warn(f"Достигнут лимит --max-delete ({limit}) — останавливаюсь.")
            break

    else:
        log_warn(
            f"Остановлено после {_PAGE_LIMIT} проходов — возможно, остались непрочитанные отказы. "
            "Запустите hhcleaner ещё раз."
        )
    log_ok(f"Итого откликов удалено: {total_deleted}")
    return total_deleted
