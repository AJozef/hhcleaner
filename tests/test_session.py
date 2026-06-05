"""Тесты check_session для путей без сети."""
from __future__ import annotations

from chats_api import SessionStatus, check_session


def test_none_session_is_not_ok():
    status = check_session(None)
    assert isinstance(status, SessionStatus)
    assert status.ok is False
    assert status.chats is None
    assert status.message  # непустое человекочитаемое описание
