"""Тесты стейт-машины получения авторизованного контекста (runner.acquire_context).

Самая ветвистая логика проекта: интерактивный vs безлюдный режим, протухшая
сессия, окно ручного входа. Сети/браузера нет — auth.*, open_and_check и notify
замоканы.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

import runner
from chats_api import SessionStatus
from config import EXIT_LOGIN_FAILED, EXIT_NEED_LOGIN


def _args(no_input=False, relogin=False, headed=False):
    """Минимальный argparse.Namespace для acquire_context."""
    return types.SimpleNamespace(no_input=no_input, relogin=relogin, headed=headed)


def _ok(msg="ok") -> SessionStatus:
    return SessionStatus(True, None, msg)


def _bad(msg="bad") -> SessionStatus:
    return SessionStatus(False, None, msg)


@pytest.fixture
def patched(monkeypatch):
    """Замокивает все внешние зависимости runner: auth, open_and_check, notify.

    Возвращает namespace с моками, чтобы тест донастроил поведение и проверил вызовы.
    Дефолты: launch_context отдаёт ctx; open_and_check нужно задать в каждом тесте
    (side_effect/return_value).
    """
    auth = runner.auth
    ctx = MagicMock(name="context")
    login_ctx = MagicMock(name="login_context")

    mocks = types.SimpleNamespace(
        ctx=ctx,
        login_ctx=login_ctx,
        session_exists=MagicMock(return_value=True),
        launch_context=MagicMock(return_value=ctx),
        interactive_login=MagicMock(return_value=login_ctx),
        clear_session=MagicMock(),
        open_and_check=MagicMock(),
        session_expired=MagicMock(),
    )
    monkeypatch.setattr(auth, "session_exists", mocks.session_exists)
    monkeypatch.setattr(auth, "launch_context", mocks.launch_context)
    monkeypatch.setattr(auth, "interactive_login", mocks.interactive_login)
    monkeypatch.setattr(auth, "clear_session", mocks.clear_session)
    monkeypatch.setattr(runner, "open_and_check", mocks.open_and_check)
    monkeypatch.setattr(runner.notify, "session_expired", mocks.session_expired)
    return mocks


# ──────────────────────────── маршрутизация ──────────────────────────────────


class TestAcquireRouting:
    def test_no_input_goes_noninteractive(self, monkeypatch):
        monkeypatch.setattr(runner, "_acquire_noninteractive", lambda p, a: "NONINT")
        monkeypatch.setattr(runner, "_acquire_interactive", lambda p, a: "INT")
        assert runner.acquire_context(MagicMock(), _args(no_input=True)) == "NONINT"

    def test_default_goes_interactive(self, monkeypatch):
        monkeypatch.setattr(runner, "_acquire_noninteractive", lambda p, a: "NONINT")
        monkeypatch.setattr(runner, "_acquire_interactive", lambda p, a: "INT")
        assert runner.acquire_context(MagicMock(), _args(no_input=False)) == "INT"


# ──────────────────────────── интерактивный режим ────────────────────────────


class TestInteractive:
    def test_valid_session_returns_context_without_login_window(self, patched):
        session = MagicMock()
        patched.open_and_check.return_value = (session, _ok())
        result = runner.acquire_context(MagicMock(), _args())
        assert result == (patched.ctx, session)
        patched.interactive_login.assert_not_called()

    def test_stale_session_opens_login_window_and_succeeds(self, patched):
        s1, s2 = MagicMock(), MagicMock()
        # call1 — протухшая сессия; call2 — после ручного входа.
        patched.open_and_check.side_effect = [(s1, _bad()), (s2, _ok())]
        result = runner.acquire_context(MagicMock(), _args())
        # Контекст — из окна входа (interactive_login), сессия — после входа.
        assert result == (patched.login_ctx, s2)
        patched.interactive_login.assert_called_once()

    def test_stale_session_login_fails_returns_exit_code(self, patched):
        s1, s2 = MagicMock(), MagicMock()
        patched.open_and_check.side_effect = [(s1, _bad()), (s2, _bad())]
        result = runner.acquire_context(MagicMock(), _args())
        assert result == EXIT_LOGIN_FAILED


# ──────────────────────────── безлюдный режим (--no-input) ────────────────────


class TestNoninteractive:
    def test_no_session_no_relogin_notifies_and_exits(self, patched):
        patched.session_exists.return_value = False
        result = runner.acquire_context(MagicMock(), _args(no_input=True))
        assert result == EXIT_NEED_LOGIN
        patched.session_expired.assert_called_once()
        # Окно входа не открываем и браузер даже не поднимаем.
        patched.launch_context.assert_not_called()
        patched.open_and_check.assert_not_called()

    def test_valid_session_returns_context(self, patched):
        session = MagicMock()
        patched.open_and_check.return_value = (session, _ok())
        result = runner.acquire_context(MagicMock(), _args(no_input=True))
        assert result == (patched.ctx, session)
        # Безлюдный режим поднимает браузер скрытым.
        _, kwargs = patched.launch_context.call_args
        assert kwargs == {"headless": True}

    def test_stale_session_notifies_and_exits(self, patched):
        session = MagicMock()
        patched.open_and_check.return_value = (session, _bad())
        result = runner.acquire_context(MagicMock(), _args(no_input=True))
        assert result == EXIT_NEED_LOGIN
        patched.session_expired.assert_called_once()
        patched.ctx.close.assert_called_once()

    def test_relogin_always_refused(self, patched):
        # Сменить аккаунт без окна нечем — в безлюдном режиме --relogin всегда отказ,
        # сессию не трогаем и браузер не поднимаем.
        result = runner.acquire_context(MagicMock(), _args(no_input=True, relogin=True))
        assert result == EXIT_NEED_LOGIN
        patched.session_expired.assert_called_once()
        patched.clear_session.assert_not_called()
        patched.launch_context.assert_not_called()


# ──────────────────────────── prepare_context ────────────────────────────────


class TestPrepareContext:
    def test_relogin_clears_and_logs_in(self, patched):
        result = runner.prepare_context(MagicMock(), relogin=True, headed=False)
        patched.clear_session.assert_called_once()
        patched.interactive_login.assert_called_once_with(patched.ctx)
        assert result == patched.login_ctx

    def test_session_exists_reuses_headless(self, patched):
        result = runner.prepare_context(MagicMock(), relogin=False, headed=False)
        assert result == patched.ctx
        _, kwargs = patched.launch_context.call_args
        assert kwargs == {"headless": True}
        patched.interactive_login.assert_not_called()

    def test_headed_flag_shows_window(self, patched):
        runner.prepare_context(MagicMock(), relogin=False, headed=True)
        _, kwargs = patched.launch_context.call_args
        assert kwargs == {"headless": False}

    def test_no_session_logs_in(self, patched):
        patched.session_exists.return_value = False
        result = runner.prepare_context(MagicMock(), relogin=False, headed=False)
        patched.interactive_login.assert_called_once()
        assert result == patched.login_ctx
