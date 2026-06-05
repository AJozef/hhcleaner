"""Удаление чатов через API chatik.hh.ru — основной быстрый метод."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Callable, NamedTuple

import requests
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.progress import track as _track

from config import (
    CHATIK_URL,
    CHATS_ENDPOINT,
    CHATS_PER_PAGE,
    LEAVE_ENDPOINT,
    MARK_READ_ENDPOINT,
    OLD_CHATS_DAYS,
    PAGE_PAUSE,
    REQUEST_PAUSE,
    RETRY_BACKOFF,
    USER_AGENT,
    console,
    is_quiet,
    log,
    log_ok,
    log_section,
    parse_iso_datetime,
)


class ChatAPIError(Exception):
    """API чатов вернул ошибку авторизации (401/403) — нужен браузерный фолбэк."""


# ──────────────────────────── session ────────────────────────────────────────


def open_session(context) -> requests.Session | None:
    """Открывает chatik, забирает куки и создаёт requests-сессию (или None).

    Страницу НЕ закрываем намеренно: в persistent context закрытие последней
    страницы автоматически закрывает весь контекст, и последующие new_page()
    падают с TargetClosedError.
    """
    page = context.pages[0] if context.pages else context.new_page()

    opened = False
    for attempt in range(3):
        try:
            page.goto(CHATIK_URL, wait_until="domcontentloaded", timeout=30000)
            opened = True
            break
        except Exception as e:  # pylint: disable=broad-exception-caught
            log(f"  ! chatik не открылся (попытка {attempt + 1}/3): {e}")
    if not opened:
        log("Не удалось открыть chatik — сессию создать нельзя.")
        return None

    cookies = context.cookies()
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    xsrf = next((c["value"] for c in cookies if c["name"] == "_xsrf"), None)

    if not xsrf:
        return None

    session = requests.Session()
    for k, v in {
        "User-Agent":        USER_AGENT,
        "Accept":            "application/json",
        "Content-Type":      "application/json",
        "X-Requested-With":  "XMLHttpRequest",
        "X-Xsrftoken":       xsrf,
        "X-hhtmSource":      "app",
        "Origin":            "https://hh.ru",
        "Referer":           "https://hh.ru/",
        "Cookie":            cookie_str,
    }.items():
        session.headers[str(k)] = str(v)

    return session


# ──────────────────────────── HTTP helpers ────────────────────────────────────


def _request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    what: str = "запрос",
    retries: int = 3,
    **kwargs,
) -> requests.Response | None:
    """Выполняет HTTP-запрос с повтором при сетевых сбоях.

    Возвращает Response или None при исчерпании попыток.
    Статусы 4xx/5xx не считаются сбоем — отдаются вызывающему.
    Задержка между попытками — экспоненциальная: RETRY_BACKOFF × 2^attempt.
    """
    kwargs.setdefault("timeout", 30)
    for attempt in range(retries):
        try:
            return session.request(method, url, **kwargs)
        except requests.exceptions.RequestException as e:
            log(f"  ! Ошибка {what} (попытка {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
    return None


def _get_chats_page(
    session: requests.Session,
    page_num: int,
    only_unread: bool = False,
) -> requests.Response | None:
    """Получает одну страницу чатов с retry."""
    return _request_with_retry(
        session, "GET", CHATS_ENDPOINT,
        what="получения чатов",
        params={
            "page":                          page_num,
            "perPage":                       CHATS_PER_PAGE,
            "filterUnread":                  "true" if only_unread else "false",
            "filterHasTextMessage":          "false",
            "do_not_track_session_events":   "true",
        },
    )


def _get_all_chats_pages(session: requests.Session):
    """Генератор — отдаёт страницы от первой к последней.

    Yield: (page_num, items, vacancies).
    """
    page_num = 0
    while True:
        resp = _get_chats_page(session, page_num)
        if resp is None:
            log("Не удалось получить страницу, останавливаюсь.")
            break
        if resp.status_code != 200:
            if resp.status_code in (401, 403):
                raise ChatAPIError(
                    f"chatik API: статус {resp.status_code} — авторизация не прошла"
                )
            log(f"Ошибка получения чатов: {resp.status_code}")
            break

        data = resp.json()
        chats = data.get("chats", {})
        items = chats.get("items", [])
        vacancies = data.get("resources", {}).get("vacancies", {})

        if page_num == 0:
            log(f"Всего чатов: {chats.get('found', 0)}")

        yield page_num, items, vacancies

        if not chats.get("hasNextPage", False):
            break
        page_num += 1
        time.sleep(PAGE_PAUSE)


# ──────────────────────────── deletion ────────────────────────────────────────


def _leave_one(session: requests.Session, chat_id: str) -> bool:
    """Покидает один чат. True при успехе. Потокобезопасен."""
    resp = _request_with_retry(
        session, "POST", LEAVE_ENDPOINT, what="leave", json={"chatId": chat_id}
    )
    return resp is not None and resp.status_code == 200


def _leave_chats(
    session: requests.Session,
    chat_ids: list[str],
    dry_run: bool = False,
    limit: int | None = None,
    workers: int = 1,
) -> int:
    """Покидает список чатов по ID, возвращает количество удалённых.

    workers > 1 — параллельное удаление через ThreadPoolExecutor.
    Прогресс-бар: в параллельном режиме используется BarColumn с процентом.
    """
    if limit is not None:
        chat_ids = chat_ids[:limit]
    if not chat_ids:
        return 0
    if dry_run:
        log(f"  [dry-run] Было бы удалено: {len(chat_ids)}")
        return 0

    deleted = 0
    errors  = 0

    if workers > 1:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
            disable=is_quiet(),
            transient=True,
        ) as progress:
            task = progress.add_task("  Удаляю чаты  ", total=len(chat_ids))
            # Сессия шарится между потоками. Это безопасно здесь: заголовки
            # выставлены до старта пула и только читаются, пул соединений
            # urllib3 потокобезопасен, а cookiejar обновляется под локом.
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_leave_one, session, cid): cid
                    for cid in chat_ids
                }
                for future in as_completed(futures):
                    if future.result():
                        deleted += 1
                    else:
                        errors += 1
                    progress.advance(task)
    else:
        for chat_id in _track(
            chat_ids, description="  Удаляю чаты  ", disable=is_quiet(), transient=True
        ):
            if _leave_one(session, chat_id):
                deleted += 1
            else:
                errors += 1
            time.sleep(REQUEST_PAUSE)

    msg = f"  Удалено: {deleted}"
    if errors:
        msg += f", ошибок: {errors}"
    log_ok(msg)
    return deleted


# ──────────────────────────── predicates ─────────────────────────────────────


def _applicant_state(item: dict) -> str | None:
    """Статус отклика из последнего сообщения (RESPONSE/DISCARD/INTERVIEW/None)."""
    return (
        ((item.get("lastMessage") or {}).get("workflowTransition") or {})
        .get("applicantState")
    )


def _is_rejected(item: dict) -> bool:
    """True, если по чату пришёл отказ работодателя (applicantState == DISCARD)."""
    return _applicant_state(item) == "DISCARD"


def _vacancy_is_archived(vacancy: dict | None) -> bool:
    """True, если вакансия в архиве: в её объекте присутствует элемент 'archived'."""
    return bool(vacancy) and ("archived" in vacancy)


def _vacancy_of(item: dict, vacancies: dict) -> dict | None:
    """Возвращает объект вакансии чата из словаря vacancies (или None)."""
    vid = ((item.get("resources") or {}).get("VACANCY") or [None])[0]
    return vacancies.get(str(vid)) if vid else None


_parse_creation_time = parse_iso_datetime  # алиас для читаемости


# ──────────────────────────── scan helpers ───────────────────────────────────


def _collect_chat_ids(
    session: requests.Session,
    predicate: Callable[[dict, dict], bool],
) -> list[str]:
    """Сканирует все страницы и собирает id чатов по предикату."""
    ids: list[str] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        disable=is_quiet(),
        transient=True,
    ) as progress:
        task = progress.add_task("  Сканирую чаты… найдено 0")
        for _page_num, items, vacancies in _get_all_chats_pages(session):
            ids.extend(it["id"] for it in items if predicate(it, vacancies))
            progress.update(task, description=f"  Сканирую чаты… найдено {len(ids)}")
    return ids


def _collect_multi_chat_ids(
    session: requests.Session,
    predicates: dict[str, Callable[[dict, dict], bool]],
) -> dict[str, list[str]]:
    """Единый проход по всем страницам — собирает id для нескольких предикатов.

    Экономит N-1 полных проходов по сравнению с N вызовами _collect_chat_ids.
    """
    ids: dict[str, list[str]] = {key: [] for key in predicates}
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        disable=is_quiet(),
        transient=True,
    ) as progress:
        task = progress.add_task("  Сканирую чаты…")
        for _page_num, items, vacancies in _get_all_chats_pages(session):
            for item in items:
                for key, pred in predicates.items():
                    if pred(item, vacancies):
                        ids[key].append(item["id"])
            total = sum(len(v) for v in ids.values())
            progress.update(task, description=f"  Сканирую чаты… {total} под удаление")
    return ids


# ──────────────────────────── public API ─────────────────────────────────────


def _mark_read(
    session: requests.Session,
    chat_id: str,
    message_id: str,
) -> bool:
    """Помечает чат прочитанным до указанного сообщения. True при успехе."""
    resp = _request_with_retry(
        session, "POST", MARK_READ_ENDPOINT,
        what="mark_read", json={"chatId": chat_id, "messageId": message_id},
    )
    return resp is not None and resp.status_code == 200


def mark_all_chats_read(session: requests.Session) -> int:
    """Помечает прочитанными все непрочитанные чаты во вкладке «Все»."""
    log_section("Прочтение всех непрочитанных чатов")

    targets: list[tuple[str, str]] = []
    page_num = 0
    while True:
        resp = _get_chats_page(session, page_num, only_unread=True)
        if not resp or resp.status_code != 200:
            break
        chats = resp.json().get("chats", {})
        for item in chats.get("items", []):
            mid = (item.get("lastMessage") or {}).get("id")
            if mid is not None:
                targets.append((item["id"], mid))
        if not chats.get("hasNextPage"):
            break
        page_num += 1
        time.sleep(PAGE_PAUSE)

    log(f"Непрочитанных чатов: {len(targets)}")
    read = 0
    for chat_id, message_id in _track(
        targets, description="  Читаю чаты   ", disable=is_quiet(), transient=True
    ):
        if _mark_read(session, chat_id, message_id):
            read += 1
        time.sleep(REQUEST_PAUSE)
    log_ok(f"Прочитано: {read}")
    return read


class SessionStatus(NamedTuple):
    """Результат проверки сессии.

    ok      — рабочая ли сессия.
    chats   — число чатов в аккаунте (None, если неизвестно/сессия не работает).
    message — человекочитаемое описание для лога.
    """
    ok: bool
    chats: int | None
    message: str


def check_session(session: requests.Session | None) -> SessionStatus:
    """Проверяет, рабочая ли сессия. Возвращает SessionStatus."""
    if not session:
        return SessionStatus(
            False, None, "сессия невалидна (нет _xsrf) — вход не выполнен или истёк"
        )
    resp = _get_chats_page(session, 0)
    if resp is not None and resp.status_code == 200:
        found = resp.json().get("chats", {}).get("found")
        count = found if isinstance(found, int) else None
        shown = count if count is not None else "?"
        return SessionStatus(True, count, f"сессия рабочая, чатов в аккаунте: {shown}")
    code = resp.status_code if resp is not None else "нет ответа"
    return SessionStatus(False, None, f"API чатов вернул {code}")


def gather_stats(
    session: requests.Session,
    days: int = OLD_CHATS_DAYS,
) -> dict[str, int]:
    """Считает статистику по чатам за один проход, ничего не удаляя.

    Возвращает словарь: total, unread, rejected, archived_vacancy, old.
    """
    log_section("Статистика по чатам")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stats: dict[str, int] = {
        "total": 0, "unread": 0, "rejected": 0, "archived_vacancy": 0, "old": 0,
    }

    resp = _get_chats_page(session, 0, only_unread=True)
    if resp is not None and resp.status_code == 200:
        stats["unread"] = resp.json().get("chats", {}).get("found", 0)

    try:
        for _page_num, items, vacancies in _get_all_chats_pages(session):
            for item in items:
                stats["total"] += 1
                state = _applicant_state(item)
                if state == "DISCARD":
                    stats["rejected"] += 1
                vacancy = _vacancy_of(item, vacancies)
                if _vacancy_is_archived(vacancy) and state != "INTERVIEW":
                    stats["archived_vacancy"] += 1
                dt = _parse_creation_time(
                    (item.get("lastMessage") or {}).get("creationTime")
                )
                if dt is not None and dt < cutoff:
                    stats["old"] += 1
    except ChatAPIError as e:
        log(f"Статистика неполная: {e}")

    return stats


# Шаги, объединяемые в единый проход пагинации.
_API_STEPS = ("chats-rejected", "archived-vacancy", "old-chats")

_STEP_LABELS: dict[str, str] = {
    "chats-rejected":  "Чатов с отказами",
    "archived-vacancy": "Чатов по архивным вакансиям",
}


def delete_chats_api_combined(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    session: requests.Session,
    steps: list[str],
    days: int = OLD_CHATS_DAYS,
    dry_run: bool = False,
    limit: int | None = None,
    cutoff: datetime | None = None,
    workers: int = 1,
) -> dict[str, int]:
    """Удаляет чаты нескольких типов за один проход по пагинации.

    cutoff — абсолютная дата среза для old-chats (из --since). Если None —
    вычисляется из days. Приоритет: cutoff > days.
    workers — потоков для параллельного удаления (1 = последовательно).
    """
    active = [s for s in _API_STEPS if s in steps]
    if not active:
        return {}

    log_section("Сканирование чатов (единый проход)")
    effective_cutoff = cutoff or (datetime.now(timezone.utc) - timedelta(days=days))
    log(f"Порог для старых чатов: {effective_cutoff.strftime('%Y-%m-%d')}")

    predicates: dict[str, Callable[[dict, dict], bool]] = {}
    if "chats-rejected" in active:
        predicates["chats-rejected"] = lambda it, _vac: _is_rejected(it)
    if "archived-vacancy" in active:
        predicates["archived-vacancy"] = lambda it, vac: (
            _vacancy_is_archived(_vacancy_of(it, vac)) and _applicant_state(it) != "INTERVIEW"
        )
    if "old-chats" in active:
        _eff = effective_cutoff  # захват локальной переменной для лямбды

        def _is_old(it: dict, _vac: dict) -> bool:
            dt = _parse_creation_time((it.get("lastMessage") or {}).get("creationTime"))
            return dt is not None and dt < _eff

        predicates["old-chats"] = _is_old

    collected = _collect_multi_chat_ids(session, predicates)

    results: dict[str, int] = {}
    for step in active:
        ids = collected.get(step, [])
        label = _STEP_LABELS.get(step, f"Чатов старше {days} дней")
        log(f"{label}: {len(ids)}")
        results[step] = _leave_chats(
            session, ids, dry_run=dry_run, limit=limit, workers=workers
        )

    return results
