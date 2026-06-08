"""Единый каталог шагов очистки: id, лейблы, набор по умолчанию.

Предикаты остаются в chats_api (они завязаны на
форму ответа API).

Строковые значения шагов — часть публичного контракта CLI (argparse choices,
ключи результатов, аргументы из примеров в README): менять их нельзя.
"""
from __future__ import annotations

# ── Идентификаторы шагов ──────────────────────────────────────────────────────
READ_ALL         = "read-all"           # пометить прочитанными все непрочитанные
NEGOTIATIONS     = "negotiations"       # отклики со статусом «отказ»
CHATS_REJECTED   = "chats-rejected"     # чаты с отказами (API, фолбэк — браузер)
ARCHIVED_VACANCY = "archived-vacancy"   # чаты по архивным вакансиям, кроме собесов
OLD_CHATS        = "old-chats"          # чаты старше N дней

# Порядок = порядок выполнения шагов и порядок строк в сводке.
ALL_STEPS = [READ_ALL, NEGOTIATIONS, CHATS_REJECTED, ARCHIVED_VACANCY, OLD_CHATS]
# По умолчанию запускаются все шаги, если в командной строке ничего не указано.
DEFAULT_STEPS = list(ALL_STEPS)

# Шаги, которые chats_api объединяет в один проход пагинации.
API_STEPS = (CHATS_REJECTED, ARCHIVED_VACANCY, OLD_CHATS)

# Лейблы для финальной сводки (hh_cleaner._print_summary).
STEP_LABELS = {
    READ_ALL:         "Чатов прочитано",
    NEGOTIATIONS:     "Откликов удалено",
    CHATS_REJECTED:   "Чатов-отказов удалено",
    ARCHIVED_VACANCY: "Чатов по архивным вакансиям удалено",
    OLD_CHATS:        "Старых чатов удалено",
}

# Лейблы строки сканирования в chats_api (old-chats получает лейбл от даты-среза,
# поэтому его здесь нет).
SCAN_LABELS = {
    CHATS_REJECTED:   "Чатов с отказами",
    ARCHIVED_VACANCY: "Чатов по архивным вакансиям",
}

# Короткие лейблы для toast-уведомления (notify.done).
NOTIFY_LABELS = {
    READ_ALL:         "прочитано чатов",
    NEGOTIATIONS:     "откликов удалено",
    CHATS_REJECTED:   "чатов-отказов удалено",
    ARCHIVED_VACANCY: "архивных чатов удалено",
    OLD_CHATS:        "старых чатов удалено",
}
