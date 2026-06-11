"""Тесты run_steps — диспетчер шагов очистки, без сети/браузера."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import runner
from chats_api import ChatAPIError
from steps import CleanOptions


@pytest.fixture
def mocked_steps(monkeypatch):
    """Подменяет все step-функции в runner моками. Возвращает их dict."""
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
        monkeypatch.setattr(runner, name, mock)
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
        results = runner.run_steps(ctx, session, ["read-all"], CleanOptions(days=30))
        assert results == {"read-all": 5}
        mocked_steps["mark_all_chats_read"].assert_called_once_with(session)
        mocked_steps["delete_rejected_negotiations"].assert_not_called()
        mocked_steps["delete_chats_api_combined"].assert_not_called()

    def test_read_all_api_error_does_not_crash(self, mocked_steps):
        # read-all ходит только через API и не имеет браузерного фолбэка:
        # при ChatAPIError прогон не валится, шаг фиксируется как 0.
        ctx, _ = _make_context()
        session = MagicMock()
        mocked_steps["mark_all_chats_read"].side_effect = ChatAPIError("401")
        results = runner.run_steps(ctx, session, ["read-all"], CleanOptions(days=30))
        assert results == {"read-all": 0}

    def test_negotiations_opens_and_closes_own_page(self, mocked_steps):
        ctx, page = _make_context()
        session = MagicMock()
        results = runner.run_steps(
            ctx, session, ["negotiations"], CleanOptions(days=30, dry_run=True, limit=10)
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
        opts = CleanOptions(days=45, dry_run=False, limit=None, cutoff=None)
        results = runner.run_steps(
            ctx, session, ["chats-rejected", "archived-vacancy", "old-chats"], opts
        )
        assert mocked_steps["delete_chats_api_combined"].call_count == 1
        passed_opts = mocked_steps["delete_chats_api_combined"].call_args.args[2]
        assert passed_opts.dry_run is False
        assert passed_opts.limit is None
        assert passed_opts.cutoff is None
        assert results["chats-rejected"] == 2
        assert results["archived-vacancy"] == 1
        assert results["old-chats"] == 4

    def test_chat_api_error_triggers_browser_fallback(self, mocked_steps):
        ctx, _ = _make_context()
        session = MagicMock()
        mocked_steps["delete_chats_api_combined"].side_effect = ChatAPIError("401")
        results = runner.run_steps(
            ctx, session, ["chats-rejected", "archived-vacancy", "old-chats"],
            CleanOptions(days=30),
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

    def test_force_browser_skips_api(self, mocked_steps):
        # --force-browser: API не дёргаем, сразу идём в браузерный путь.
        ctx, _ = _make_context()
        session = MagicMock()
        results = runner.run_steps(
            ctx, session, ["chats-rejected", "archived-vacancy", "old-chats"],
            CleanOptions(days=30, force_browser=True),
        )
        mocked_steps["delete_chats_api_combined"].assert_not_called()
        mocked_steps["delete_rejected_chats"].assert_called_once_with(
            ctx, dry_run=False, limit=None
        )
        assert results["chats-rejected"] == 7
        assert results["archived-vacancy"] == 8
        assert results["old-chats"] == 9

    def test_force_browser_skips_read_all(self, mocked_steps):
        # read-all ходит только через API — в --force-browser он пропускается (0).
        ctx, _ = _make_context()
        results = runner.run_steps(
            ctx, MagicMock(), ["read-all"], CleanOptions(days=30, force_browser=True)
        )
        assert results == {"read-all": 0}
        mocked_steps["mark_all_chats_read"].assert_not_called()

    def test_negotiations_page_closed_on_exception(self, mocked_steps):
        ctx, page = _make_context()
        session = MagicMock()
        mocked_steps["delete_rejected_negotiations"].side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            runner.run_steps(ctx, session, ["negotiations"], CleanOptions(days=30))
        page.close.assert_called_once()

    def test_empty_steps_returns_empty_dict(self, mocked_steps):
        ctx, _ = _make_context()
        results = runner.run_steps(ctx, MagicMock(), [], CleanOptions(days=30))
        assert results == {}
        # Никакая step-функция не вызвалась.
        for name, mock in mocked_steps.items():
            assert mock.call_count == 0, f"{name} был вызван без причины"