"""Тесты check_session, retry и освежения кук сессии — без сети."""
from __future__ import annotations

from unittest.mock import MagicMock

import requests

import chats_api
from chats_api import (
    SessionStatus,
    _parse_retry_after,
    _request_with_retry,
    check_session,
)


def test_none_session_is_not_ok():
    status = check_session(None)
    assert isinstance(status, SessionStatus)
    assert status.ok is False
    assert status.chats is None
    assert status.message  # непустое человекочитаемое описание


class TestParseRetryAfter:
    def test_none_and_empty(self):
        assert _parse_retry_after(None) is None
        assert _parse_retry_after("") is None
        assert _parse_retry_after("   ") is None

    def test_seconds_as_int(self):
        assert _parse_retry_after("120") == 120.0

    def test_seconds_as_float(self):
        assert _parse_retry_after("2.5") == 2.5

    def test_zero_is_allowed(self):
        assert _parse_retry_after("0") == 0.0

    def test_negative_clamped_to_zero(self):
        # Спецификация не запрещает, но по смыслу «уже можно».
        assert _parse_retry_after("-5") == 0.0

    def test_invalid_string(self):
        # Любая неразбираемая строка (включая HTTP-date) → None,
        # и caller уходит в обычный backoff.
        assert _parse_retry_after("not a number") is None
        assert _parse_retry_after("Wed, 21 Oct 2025 07:28:00 GMT") is None


class TestRequestWithRetryOn429:
    @staticmethod
    def _make_response(status: int, retry_after: str | None = None) -> MagicMock:
        resp = MagicMock(spec=requests.Response)
        resp.status_code = status
        resp.headers = {"Retry-After": retry_after} if retry_after else {}
        return resp

    def test_200_returns_immediately(self, monkeypatch):
        session = MagicMock()
        ok = self._make_response(200)
        session.request.return_value = ok
        slept: list[float] = []
        monkeypatch.setattr(chats_api.time, "sleep", lambda s: slept.append(s))

        result = _request_with_retry(session, "GET", "https://x/", what="t")

        assert result is ok
        assert session.request.call_count == 1
        assert slept == []

    def test_429_then_200_retries_with_pause(self, monkeypatch):
        session = MagicMock()
        session.request.side_effect = [
            self._make_response(429, retry_after="3"),
            self._make_response(200),
        ]
        slept: list[float] = []
        monkeypatch.setattr(chats_api.time, "sleep", lambda s: slept.append(s))

        result = _request_with_retry(session, "GET", "https://x/", what="t")

        assert result is not None
        assert result.status_code == 200
        assert session.request.call_count == 2
        # Пауза должна быть взята из Retry-After (3 секунды).
        assert slept == [3.0]

    def test_429_without_retry_after_uses_backoff(self, monkeypatch):
        session = MagicMock()
        session.request.side_effect = [
            self._make_response(429),
            self._make_response(200),
        ]
        slept: list[float] = []
        monkeypatch.setattr(chats_api.time, "sleep", lambda s: slept.append(s))

        result = _request_with_retry(session, "GET", "https://x/", what="t")

        assert result is not None
        assert result.status_code == 200
        # Без Retry-After: первый attempt → RETRY_BACKOFF * 2^0 = RETRY_BACKOFF.
        assert slept == [chats_api.RETRY_BACKOFF]

    def test_429_persistent_returns_last_response(self, monkeypatch):
        session = MagicMock()
        session.request.side_effect = [
            self._make_response(429, retry_after="1"),
            self._make_response(429, retry_after="1"),
            self._make_response(429, retry_after="1"),
        ]
        monkeypatch.setattr(chats_api.time, "sleep", lambda s: None)

        result = _request_with_retry(session, "GET", "https://x/", what="t", retries=3)

        # После исчерпания retry возвращаем последний 429, чтобы caller узнал.
        assert result is not None
        assert result.status_code == 429
        assert session.request.call_count == 3

    def test_4xx_other_than_429_not_retried(self, monkeypatch):
        # 404 — не 429 и не 401/403, поэтому не ретраится (401/403 — отдельный кейс ниже).
        session = MagicMock()
        session.request.return_value = self._make_response(404)
        slept: list[float] = []
        monkeypatch.setattr(chats_api.time, "sleep", lambda s: slept.append(s))

        result = _request_with_retry(session, "GET", "https://x/", what="t")

        assert result is not None
        assert result.status_code == 404
        assert session.request.call_count == 1
        assert slept == []


class TestRequestWithRetryOnAuth:
    """401/403: освежение кук из браузера и повтор (ротация _xsrf на длинном прогоне)."""

    @staticmethod
    def _resp(status: int) -> MagicMock:
        resp = MagicMock(spec=requests.Response)
        resp.status_code = status
        resp.headers = {}
        return resp

    def test_403_refreshes_cookies_and_retries(self, monkeypatch):
        session = MagicMock()
        session.request.side_effect = [self._resp(403), self._resp(200)]
        monkeypatch.setattr(chats_api.time, "sleep", lambda s: None)
        monkeypatch.setattr(chats_api, "_refresh_session_cookies", lambda s: True)

        result = _request_with_retry(session, "GET", "https://x/", what="t")

        assert result is not None and result.status_code == 200
        assert session.request.call_count == 2

    def test_401_also_triggers_refresh(self, monkeypatch):
        session = MagicMock()
        session.request.side_effect = [self._resp(401), self._resp(200)]
        monkeypatch.setattr(chats_api.time, "sleep", lambda s: None)
        monkeypatch.setattr(chats_api, "_refresh_session_cookies", lambda s: True)

        result = _request_with_retry(session, "GET", "https://x/", what="t")

        assert result is not None and result.status_code == 200
        assert session.request.call_count == 2

    def test_403_not_retried_when_refresh_fails(self, monkeypatch):
        # Освежать нечем (нет контекста / нет _xsrf) → отдаём 403 как есть, 1 запрос.
        session = MagicMock()
        session.request.return_value = self._resp(403)
        monkeypatch.setattr(chats_api.time, "sleep", lambda s: None)
        monkeypatch.setattr(chats_api, "_refresh_session_cookies", lambda s: False)

        result = _request_with_retry(session, "GET", "https://x/", what="t")

        assert result is not None and result.status_code == 403
        assert session.request.call_count == 1


class TestApplySessionCookies:
    """Снятие/освежение кук из браузерного контекста в заголовки сессии."""

    def test_sets_cookie_and_xsrf_when_present(self):
        session = requests.Session()
        context = MagicMock()
        context.cookies.return_value = [
            {"name": "_xsrf", "value": "tok"},
            {"name": "hhuid", "value": "abc"},
        ]
        assert chats_api._apply_session_cookies(session, context) is True
        assert session.headers["X-Xsrftoken"] == "tok"
        assert "_xsrf=tok" in session.headers["Cookie"]
        assert "hhuid=abc" in session.headers["Cookie"]

    def test_false_when_no_xsrf(self):
        session = requests.Session()
        context = MagicMock()
        context.cookies.return_value = [{"name": "hhuid", "value": "abc"}]
        assert chats_api._apply_session_cookies(session, context) is False

    def test_refresh_without_registered_context_is_false(self):
        # Сессия не из open_session → в реестре контекста нет → освежать нечем.
        session = requests.Session()
        assert chats_api._refresh_session_cookies(session) is False
