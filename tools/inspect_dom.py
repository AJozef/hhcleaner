"""Dev-утилита: инспектор DOM chatik — чтобы чинить сгнившие селекторы по факту.

Не входит в пакет и .exe (см. DEVELOPMENT.md → «Диагностика вёрстки hh.ru»).
Зачем: браузерный путь (chats_browser.py) цепляется за вёрстку chatik, а hh.ru
её периодически меняет — тогда какой-то шаг начинает находить 0 там, где API
находит больше. Чтобы поправить селекторы по факту, а не наугад, нужен реальный DOM.

Инспектор использует уже сохранённую сессию (как обычный прогон), через API
находит «ground truth» — id архивного и старого чата, — и выдёргивает РОВНО те
куски DOM, которые нужны:
  1) как сейчас выглядит дата в элементе списка чатов;
  2) чем помечена архивная вакансия в открытом чате.
Сам сужает вывод (короткие кандидаты), чтобы не копировать килобайты.

Запуск (из корня репозитория, в активированном venv):
    python tools/inspect_dom.py

Результат печатается в консоль и сохраняется в tools/inspect_dom_output.txt —
пришли его (или консольный вывод) обратно. Ничего не удаляет, только смотрит.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

# Импортируем из корня репозитория.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright  # noqa: E402

import auth  # noqa: E402
import chats_browser  # noqa: E402
from chats_api import (  # noqa: E402
    _applicant_state,
    _get_all_chats_pages,
    _vacancy_is_archived,
    _vacancy_of,
    open_session,
)
from config import CHATIK_URL, parse_iso_datetime  # noqa: E402
from ui_selectors import CHAT_LINK, CHAT_LIST_CONTAINER  # noqa: E402

_OUT_LINES: list[str] = []


def out(line: str = "") -> None:
    """Печатает и копит строку для файла."""
    print(line)
    _OUT_LINES.append(line)


# JS: ищет в открытом чате кандидатов-маркеры архивной вакансии (по атрибутам и тексту).
_FIND_ARCHIVED_JS = r"""
() => {
  const KW = ['архив','Архив','АРХИВ','закрыт','Закрыт','удал','Удал','снят','Снят',
              'не существ','неактив','Неактив','завершен','Завершён',
              'собесед','Собесед','интервью','Интервью','риглашени'];
  const res = [];
  const seen = new Set();
  const push = (el, why) => {
    let html = '';
    try { html = el.outerHTML; } catch (e) { return; }
    if (!html || html.length > 700 || seen.has(html)) return;
    seen.add(html);
    res.push({
      why,
      tag: el.tagName,
      dataQa: el.getAttribute ? el.getAttribute('data-qa') : null,
      cls: (typeof el.className === 'string') ? el.className : null,
      html: html
    });
  };
  // По атрибутам data-qa / class (маркеры архива И собеседования).
  document.querySelectorAll('[data-qa],[class]').forEach(el => {
    const a = (el.getAttribute('data-qa') || '') + ' ' +
              ((typeof el.className === 'string') ? el.className : '');
    if (/archiv|closed|deleted|vacancy-(deleted|archived|closed)|inactive|interview/i.test(a)) {
      push(el, 'attr');
    }
  });
  // По тексту (почти-листовые узлы).
  document.querySelectorAll('*').forEach(el => {
    if (el.children.length > 2) return;
    const t = (el.textContent || '').trim();
    if (t.length < 4 || t.length > 90) return;
    if (KW.some(k => t.includes(k))) push(el, 'text:' + t.slice(0, 50));
  });
  return res.slice(0, 30);
}
"""

# JS: в элементе списка чатов ищет любые узлы с датой/временем.
_FIND_DATE_JS = r"""
(el) => {
  const dated = [];
  el.querySelectorAll('*').forEach(n => {
    const dt = n.getAttribute ? n.getAttribute('datetime') : null;
    if (dt) {
      dated.push({
        datetime: dt,
        tag: n.tagName,
        dataQa: n.getAttribute('data-qa'),
        cls: (typeof n.className === 'string') ? n.className : null,
        html: n.outerHTML.slice(0, 300)
      });
    }
  });
  return { anchorHtml: el.outerHTML.slice(0, 2000), datedNodes: dated };
}
"""


def _find_anchor(page, cid: str):
    """Скроллит список, пока не найдёт якорь чата с данным id (или None)."""
    variants = [
        f"[data-qa='chatik-open-chat-{cid}']",
        f"[data-qa*='{cid}']",
        f"a[href*='{cid}']",
    ]
    for _ in range(80):
        for sv in variants:
            try:
                el = page.query_selector(sv)
            except Exception:  # noqa: BLE001
                el = None
            if el:
                return el
        moved = page.evaluate(
            "(sel)=>{const s=document.querySelector(sel); if(!s)return false;"
            "const b=s.scrollTop; s.scrollTop+=s.clientHeight*0.8; return s.scrollTop!==b;}",
            CHAT_LIST_CONTAINER,
        )
        page.wait_for_timeout(400)
        if not moved:
            break
    return None


def main() -> int:
    out("=== inspect_dom: ищу образцы DOM через сохранённую сессию ===")
    with sync_playwright() as p:
        ctx = auth.launch_context(p, headless=False)
        session = open_session(ctx)
        if session is None:
            out("Сессия не открылась (нет _xsrf). Сначала: hhcleaner login")
            ctx.close()
            return 1

        # 1) Через API находим один архивный и один старый чат — это «правда».
        # Архивный берём ДАЖЕ если это собеседование: для образца маркера статус
        # не важен (а удаление такие чаты и так пропускает по INTERVIEW).
        archived_id = None
        archived_is_interview = False
        old_id = None
        cutoff = datetime.now(timezone.utc) - timedelta(days=60)
        for _pn, items, vac in _get_all_chats_pages(session):
            for it in items:
                cid = it.get("id")
                if archived_id is None:
                    v = _vacancy_of(it, vac)
                    if _vacancy_is_archived(v):
                        archived_id = cid
                        archived_is_interview = _applicant_state(it) == "INTERVIEW"
                if old_id is None:
                    dt = parse_iso_datetime((it.get("lastMessage") or {}).get("creationTime"))
                    if dt is not None and dt < cutoff:
                        old_id = cid
            if archived_id and old_id:
                break
        out(f"API нашёл: архивный чат id={archived_id!r} (собеседование={archived_is_interview}), "
            f"старый чат id={old_id!r}")

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(CHATIK_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # 2) ДАТА: дамп структуры пары элементов списка (старого, если нашёлся, + первого).
        out("\n──────── (A) ЭЛЕМЕНТЫ СПИСКА ЧАТОВ — где сейчас дата ────────")
        anchors = []
        if old_id:
            a = _find_anchor(page, old_id)
            if a:
                anchors.append(("old", a))
        rendered = page.query_selector_all(CHAT_LINK)
        for el in rendered[:2]:
            anchors.append(("sample", el))
        if not anchors:
            out("!! Не нашёл ни одного элемента списка по селектору CHAT_LINK = "
                f"{CHAT_LINK!r}. Возможно, изменился и он.")
        for tag, el in anchors:
            try:
                info = el.evaluate(_FIND_DATE_JS)
            except Exception as e:  # noqa: BLE001
                out(f"[{tag}] не смог разобрать элемент: {e}")
                continue
            out(f"\n[{tag}] datetime-узлов внутри: {len(info['datedNodes'])}")
            for d in info["datedNodes"]:
                out("   " + json.dumps(d, ensure_ascii=False))
            out(f"[{tag}] anchor.outerHTML (обрезан):")
            out(info["anchorHtml"])

        # 3) АРХИВ: открываем архивный чат и ищем маркеры.
        out("\n──────── (B) ОТКРЫТЫЙ АРХИВНЫЙ ЧАТ — чем помечена архивная вакансия ────────")
        if not archived_id:
            out("API не нашёл архивных чатов сейчас — пропускаю эту часть.")
        else:
            a = _find_anchor(page, archived_id)
            if not a:
                out(f"Не доскроллил до архивного чата id={archived_id}. "
                    "Открой его в окне вручную и перезапусти — или скажи, попробуем иначе.")
            else:
                a.click()
                page.wait_for_timeout(3500)
                if archived_is_interview:
                    out("(этот архивный чат — собеседование; нужны оба маркера: "
                        "архива И собеседования)")
                try:
                    cands = page.evaluate(_FIND_ARCHIVED_JS)
                except Exception as e:  # noqa: BLE001
                    cands = []
                    out(f"Не смог просканировать страницу чата: {e}")
                out(f"Кандидатов-маркеров найдено: {len(cands)}")
                for c in cands:
                    out("   " + json.dumps(c, ensure_ascii=False))

                # Видимый текст шапки/области чата — чтобы увидеть человеческую
                # формулировку («Вакансия в архиве» и т.п.), даже если по
                # атрибутам/ключам ничего не зацепилось.
                try:
                    layout = page.query_selector("[data-qa='chatik-layout']")
                    txt = (layout.inner_text() if layout else "")[:2500]
                    out("\n--- видимый текст открытого чата (обрезан) ---")
                    out(txt)
                except Exception as e:  # noqa: BLE001
                    out(f"Не смог снять видимый текст: {e}")

                # Прогон РЕАЛЬНЫХ production-детекторов на этой живой странице —
                # проверяем, что починенные селекторы+тексты срабатывают.
                try:
                    arch = chats_browser._is_archived_in_current_page(page)
                    interv = chats_browser._is_interview_in_current_page(page)
                    out("\n--- проверка детекторов на живом DOM ---")
                    out(f"_is_archived_in_current_page  = {arch}  (ожидаем True)")
                    out(f"_is_interview_in_current_page = {interv}  "
                        f"(ожидаем {archived_is_interview} для этого чата)")
                except Exception as e:  # noqa: BLE001
                    out(f"Не смог прогнать детекторы: {e}")

        ctx.close()

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inspect_dom_output.txt")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_OUT_LINES))
    print(f"\n[+] Сохранено в {out_path} — пришли его содержимое.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
