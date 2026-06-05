"""Тесты check_session и has_login_credentials для путей без сети/браузера."""
from __future__ import annotations

import auth
from chats_api import SessionStatus, check_session


def test_none_session_is_not_ok():
    status = check_session(None)
    assert isinstance(status, SessionStatus)
    assert status.ok is False
    assert status.chats is None
    assert status.message  # непустое человекочитаемое описание


class TestHasLoginCredentials:
    def test_false_when_both_missing(self, monkeypatch):
        monkeypatch.delenv("HH_EMAIL", raising=False)
        monkeypatch.delenv("HH_PASSWORD", raising=False)
        assert auth.has_login_credentials() is False

    def test_false_when_only_email(self, monkeypatch):
        monkeypatch.setenv("HH_EMAIL", "x@y.z")
        monkeypatch.delenv("HH_PASSWORD", raising=False)
        assert auth.has_login_credentials() is False

    def test_false_when_only_password(self, monkeypatch):
        monkeypatch.delenv("HH_EMAIL", raising=False)
        monkeypatch.setenv("HH_PASSWORD", "pw")
        assert auth.has_login_credentials() is False

    def test_true_when_both_set(self, monkeypatch):
        monkeypatch.setenv("HH_EMAIL", "x@y.z")
        monkeypatch.setenv("HH_PASSWORD", "pw")
        assert auth.has_login_credentials() is True

    def test_false_when_only_whitespace(self, monkeypatch):
        monkeypatch.setenv("HH_EMAIL", "   ")
        monkeypatch.setenv("HH_PASSWORD", "   ")
        assert auth.has_login_credentials() is False
