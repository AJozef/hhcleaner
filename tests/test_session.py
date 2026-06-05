"""Тесты check_session, has_login_credentials и _configure_pool — без сети/браузера."""
from __future__ import annotations

import requests

import auth
from chats_api import SessionStatus, _configure_pool, check_session


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


class TestConfigurePool:
    def test_default_adapter_when_workers_below_threshold(self):
        session = requests.Session()
        original = session.adapters["https://"]
        _configure_pool(session, workers=5)
        # При workers <= 10 ничего не меняем — дефолтный адаптер на месте.
        assert session.adapters["https://"] is original

    def test_at_threshold_no_change(self):
        session = requests.Session()
        original = session.adapters["https://"]
        _configure_pool(session, workers=10)
        assert session.adapters["https://"] is original

    def test_new_adapter_when_workers_above_threshold(self):
        session = requests.Session()
        original = session.adapters["https://"]
        _configure_pool(session, workers=20)
        # При workers > 10 монтируем свой адаптер с расширенным пулом.
        adapter = session.adapters["https://"]
        assert adapter is not original
        # Размер пула отдан под все потоки.
        assert adapter._pool_maxsize == 20
