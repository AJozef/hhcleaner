"""Тесты предикатов классификации чатов из chats_api."""
from __future__ import annotations

from chats_api import (
    _applicant_state,
    _is_rejected,
    _vacancy_is_archived,
    _vacancy_of,
)


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
