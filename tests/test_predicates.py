"""Тесты предикатов классификации чатов из chats_api и chats_browser."""
from __future__ import annotations

from unittest.mock import MagicMock

import chats_api
from chats_api import (
    _applicant_state,
    _is_rejected,
    _vacancy_is_archived,
    _vacancy_of,
    delete_chats_api_combined,
)
from chats_browser import _norm, _text_matches
from ui_selectors import ARCHIVED_VACANCY_TEXTS, INTERVIEW_TEXTS


def _item(state=None, vacancy_id=None):
    """Собирает минимальный объект чата для предикатов."""
    item: dict = {}
    if state is not None:
        item["lastMessage"] = {"workflowTransition": {"applicantState": state}}
    if vacancy_id is not None:
        item["resources"] = {"VACANCY": [vacancy_id]}
    return item


class TestApplicantState:
    def test_missing_last_message(self):
        assert _applicant_state({}) is None

    def test_missing_transition(self):
        assert _applicant_state({"lastMessage": {}}) is None

    def test_null_last_message(self):
        assert _applicant_state({"lastMessage": None}) is None

    def test_reads_state(self):
        assert _applicant_state(_item("DISCARD")) == "DISCARD"
        assert _applicant_state(_item("INTERVIEW")) == "INTERVIEW"


class TestIsRejected:
    def test_discard_is_rejected(self):
        assert _is_rejected(_item("DISCARD")) is True

    def test_other_states_not_rejected(self):
        assert _is_rejected(_item("INTERVIEW")) is False
        assert _is_rejected(_item("RESPONSE")) is False
        assert _is_rejected(_item()) is False


class TestVacancyIsArchived:
    def test_none_vacancy(self):
        assert _vacancy_is_archived(None) is False

    def test_empty_dict(self):
        assert _vacancy_is_archived({}) is False

    def test_archived_key_present(self):
        # Признак — наличие ключа 'archived', независимо от значения.
        assert _vacancy_is_archived({"archived": True}) is True
        assert _vacancy_is_archived({"archived": False}) is True
        assert _vacancy_is_archived({"archived": None}) is True

    def test_no_archived_key(self):
        assert _vacancy_is_archived({"name": "Dev"}) is False


class TestVacancyOf:
    def test_resolves_vacancy_by_id(self):
        item = _item(vacancy_id="123")
        vacancies = {"123": {"name": "Backend"}}
        assert _vacancy_of(item, vacancies) == {"name": "Backend"}

    def test_int_id_coerced_to_str(self):
        item = _item(vacancy_id=123)
        vacancies = {"123": {"name": "Backend"}}
        assert _vacancy_of(item, vacancies) == {"name": "Backend"}

    def test_no_vacancy_resource(self):
        assert _vacancy_of({}, {"123": {}}) is None

    def test_unknown_vacancy_id(self):
        item = _item(vacancy_id="999")
        assert _vacancy_of(item, {"123": {}}) is None


class TestCombinedDedup:
    def test_overlapping_ids_left_once(self, monkeypatch):
        # Чат 'b' попадает и в отказы, и в старые: должен покинуться один раз,
        # на первом шаге, и не дублироваться во втором.
        monkeypatch.setattr(
            chats_api, "_collect_multi_chat_ids",
            lambda session, predicates: {
                "chats-rejected": ["a", "b"],
                "old-chats": ["b", "c"],
            },
        )
        leave_calls: list[list[str]] = []
        monkeypatch.setattr(
            chats_api, "_leave_chats",
            lambda session, ids, dry_run=False, limit=None: (
                leave_calls.append(list(ids)) or len(ids)
            ),
        )
        results = delete_chats_api_combined(
            MagicMock(), ["chats-rejected", "old-chats"], days=30,
        )
        assert leave_calls == [["a", "b"], ["c"]]
        assert results == {"chats-rejected": 2, "old-chats": 1}

    def test_dry_run_does_not_dedup(self, monkeypatch):
        # В dry-run ничего не удаляется, поэтому исключать 'b' из второго шага
        # нельзя — иначе предпросмотр занизил бы число старых чатов.
        monkeypatch.setattr(
            chats_api, "_collect_multi_chat_ids",
            lambda session, predicates: {
                "chats-rejected": ["a", "b"],
                "old-chats": ["b", "c"],
            },
        )
        leave_calls: list[list[str]] = []
        monkeypatch.setattr(
            chats_api, "_leave_chats",
            lambda session, ids, dry_run=False, limit=None: (
                leave_calls.append(list(ids)) or 0
            ),
        )
        delete_chats_api_combined(
            MagicMock(), ["chats-rejected", "old-chats"], days=30, dry_run=True,
        )
        assert leave_calls == [["a", "b"], ["b", "c"]]


class TestBrowserTextMatch:
    """Текстовая классификация архива/собеседования в браузерном пути."""

    def test_norm_nbsp_and_case(self):
        # &nbsp; (\xa0) между «в» и «архиве» нормализуется в обычный пробел.
        assert _norm("Вакансия в\xa0архиве") == "вакансия в архиве"

    def test_archived_real_marker(self):
        # Реальная формулировка hh из DOM-образца (с неразрывным пробелом).
        assert _text_matches("Вакансия в\xa0архиве", ARCHIVED_VACANCY_TEXTS) is True

    def test_archived_other_phrasings(self):
        assert _text_matches("Вакансия закрыта", ARCHIVED_VACANCY_TEXTS) is True
        assert _text_matches("Вакансия удалена", ARCHIVED_VACANCY_TEXTS) is True
        assert _text_matches("Вакансия снята с публикации", ARCHIVED_VACANCY_TEXTS) is True

    def test_archived_does_not_match_active_or_remote(self):
        # Обычный статус и «удалённая работа» (формат, не статус) — НЕ архив.
        assert _text_matches("Отклик на вакансию", ARCHIVED_VACANCY_TEXTS) is False
        assert _text_matches("Удалённая работа", ARCHIVED_VACANCY_TEXTS) is False
        assert _text_matches("Удаленная работа", ARCHIVED_VACANCY_TEXTS) is False

    def test_interview_marker(self):
        assert _text_matches("Собеседование", INTERVIEW_TEXTS) is True

    def test_interview_does_not_match_response(self):
        assert _text_matches("Отклик на вакансию", INTERVIEW_TEXTS) is False

    def test_empty_and_none(self):
        assert _text_matches(None, ARCHIVED_VACANCY_TEXTS) is False
        assert _text_matches("", INTERVIEW_TEXTS) is False
