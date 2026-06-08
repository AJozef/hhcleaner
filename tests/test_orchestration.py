"""Тесты run_steps — диспетчер шагов очистки, без сети/браузера."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import hh_cleaner
from chats_api import ChatAPIError


@pytest.fixture
def mocked_steps(monkeypatch):
    """Подменяет все step-функции в hh_cleaner моками. Возвращает их dict."""
    mocks = {
        "mark_all_chats_read": MagicMock(return_value=5),
        "delete_rejected_negotiations": MagicMock(return_value=3),
        "delete_chats_api_combined": MagicMock(return_value={
            "chats-rejected": 2,
            "archived-vacancy": 1,
            "old-chats": 4,
        }),
        "delete_rejected_chats": MagicMock(return_value=7),
        "delete_archived_vacancy_chats_browser": MagicMock(return_value=8),
        "delete_old_chats_browser": MagicMock(return_value=9),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(hh_cleaner, name, mock)
    return mocks


def _make_context():
    """Мок-контекст: new_page() возвращает мок-страницу с close()."""
    page = MagicMock()
    ctx = MagicMock()
    ctx.new_page.return_value = page
    return ctx, page


class TestRunSteps:
    def test_read_all_calls_only_mark_all(self, mocked_steps):
        ctx, _ = _make_context()
        session = MagicMock()
        results = hh_cleaner.run_steps(ctx, session, ["read-all"], days=30)
        assert results == {"read-all": 5}
        mocked_steps["mark_all_chats_read"].assert_called_once_with(session)
        mocked_steps["delete_rejected_negotiations"].assert_not_called()
        mocked_steps["delete_chats_api_combined"].assert_not_called()

    def test_negotiations_opens_and_closes_own_page(self, mocked_steps):
        ctx, page = _make_context()
        session = MagicMock()
        results = hh_cleaner.run_steps(
            ctx, session, ["negotiations"], days=30, dry_run=True, limit=10
        )
        assert results == {"negotiations": 3}
        ctx.new_page.assert_called_once()
        page.close.assert_called_once()
        mocked_steps["delete_rejected_negotiations"].assert_called_once_with(
            page, dry_run=True, limit=10
        )

    def test_api_steps_grouped_into_single_call(self, mocked_steps):
        ctx, _ = _make_context()
        session = MagicMock()
        results = hh_cleaner.run_steps(
            ctx, session, ["chats-rejected", "archived-vacancy", "old-chats"],
            days=45, dry_run=False, limit=None, cutoff=None,
        )
        assert mocked_steps["delete_chats_api_combined"].call_count == 1
        kwargs = mocked_steps["delete_chats_api_combined"].call_args.kwargs
        assert kwargs["dry_run"] is False
        assert kwargs["limit"] is None
        assert kwargs["cutoff"] is None
        assert results["chats-rejected"] == 2
        assert results["archived-vacancy"] == 1
        assert results["old-chats"] == 4

    def test_chat_api_error_triggers_browser_fallback(self, mocked_steps):
        ctx, _ = _make_context()
        session = MagicMock()
        mocked_steps["delete_chats_api_combined"].side_effect = ChatAPIError("401")
        results = hh_cleaner.run_steps(
            ctx, session, ["chats-rejected", "archived-vacancy", "old-chats"],
            days=30,
        )
        mocked_steps["delete_rejected_chats"].assert_called_once_with(
            ctx, dry_run=False, limit=None
        )
        mocked_steps["delete_archived_vacancy_chats_browser"].assert_called_once_with(
            ctx, dry_run=False, limit=None
        )
        mocked_steps["delete_old_chats_browser"].assert_called_once()
        assert results["chats-rejected"] == 7
        assert results["archived-vacancy"] == 8
        assert results["old-chats"] == 9

    def test_negotiations_page_closed_on_exception(self, mocked_steps):
        ctx, page = _make_context()
        session = MagicMock()
        mocked_steps["delete_rejected_negotiations"].side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            hh_cleaner.run_steps(ctx, session, ["negotiations"], days=30)
        page.close.assert_called_once()

    def test_empty_steps_returns_empty_dict(self, mocked_steps):
        ctx, _ = _make_context()
        results = hh_cleaner.run_steps(ctx, MagicMock(), [], days=30)
        assert results == {}
        # Никакая step-функция не вызвалась.
        for name, mock in mocked_steps.items():
            assert mock.call_count == 0, f"{name} был вызван без причины"