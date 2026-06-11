"""Тесты разбора дат: parse_iso_datetime, argparse-тип _iso_date и подпись chatik."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import pytest

from chats_browser import _parse_chat_date_text
from cli import _iso_date
from config import parse_iso_datetime

# Опорное «сейчас» для тестов подписи даты: среда, 10 июня 2026 (UTC).
# Тогда: вчера=09.06(вт), пн=08.06, вс=07.06, сб=06.06, пт=05.06, чт=04.06.
_NOW = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)


def _d(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


class TestParseIsoDatetime:
    def test_none_and_empty_return_none(self):
        assert parse_iso_datetime(None) is None
        assert parse_iso_datetime("") is None
        assert parse_iso_datetime("   ") is None

    def test_invalid_string_returns_none(self):
        assert parse_iso_datetime("не дата") is None
        assert parse_iso_datetime("2025-13-99") is None

    def test_naive_datetime_assumed_utc(self):
        dt = parse_iso_datetime("2025-06-01T12:00:00")
        assert dt is not None
        assert dt == datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
        assert dt.tzinfo is not None

    def test_zulu_suffix_parsed_as_utc(self):
        dt = parse_iso_datetime("2025-06-01T12:00:00Z")
        assert dt == datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)

    def test_explicit_offset_preserved_as_instant(self):
        # +03:00 12:00 — это тот же момент, что 09:00 UTC.
        dt = parse_iso_datetime("2025-06-01T12:00:00+03:00")
        assert dt == datetime(2025, 6, 1, 9, 0, tzinfo=timezone.utc)

    def test_date_only(self):
        dt = parse_iso_datetime("2025-06-01")
        assert dt == datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc)


class TestIsoDate:
    def test_valid_date(self):
        dt = _iso_date("2025-01-15")
        assert dt == datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)

    def test_whitespace_trimmed(self):
        dt = _iso_date("  2025-01-15  ")
        assert dt == datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)

    @pytest.mark.parametrize("bad", ["2025/01/15", "15-01-2025", "хрень", "2025-13-01", ""])
    def test_invalid_format_raises(self, bad):
        # argparse-тип сигналит ошибку через ArgumentTypeError — её argparse
        # превращает в сообщение об ошибке + exit 2.
        with pytest.raises(argparse.ArgumentTypeError):
            _iso_date(bad)

    def test_future_date_rejected(self):
        # Дата из будущего у удаляющего шага означала бы «старше будущего» =
        # почти весь аккаунт. Должна отклоняться на этапе разбора аргументов.
        with pytest.raises(argparse.ArgumentTypeError):
            _iso_date("2999-12-31")


class TestParseChatDateText:
    """Разбор текстовой подписи даты из списка чатов chatik (браузерный путь)."""

    def test_today_word(self):
        assert _parse_chat_date_text("сегодня", _NOW) == _d(2026, 6, 10)

    def test_today_time(self):
        # Сегодняшние чаты могут подписываться временем ЧЧ:ММ — это всё сегодня.
        assert _parse_chat_date_text("14:30", _NOW) == _d(2026, 6, 10)
        assert _parse_chat_date_text("9:05", _NOW) == _d(2026, 6, 10)

    def test_yesterday(self):
        assert _parse_chat_date_text("вчера", _NOW) == _d(2026, 6, 9)

    @pytest.mark.parametrize("label,day", [
        ("пн", 8), ("вс", 7), ("сб", 6), ("пт", 5), ("чт", 4),
    ])
    def test_weekday_abbr(self, label, day):
        # Ближайший прошедший день недели в окне 2–6 дней назад.
        assert _parse_chat_date_text(label, _NOW) == _d(2026, 6, day)

    def test_weekday_case_and_dot(self):
        assert _parse_chat_date_text("Пн.", _NOW) == _d(2026, 6, 8)

    def test_day_month_current_year(self):
        # «ДД.ММ» без года — текущий год.
        assert _parse_chat_date_text("02.06", _NOW) == _d(2026, 6, 2)
        assert _parse_chat_date_text("31.01", _NOW) == _d(2026, 1, 31)

    def test_day_month_two_digit_year(self):
        assert _parse_chat_date_text("31.12.25", _NOW) == _d(2025, 12, 31)

    def test_day_month_four_digit_year(self):
        assert _parse_chat_date_text("31.12.2025", _NOW) == _d(2025, 12, 31)

    @pytest.mark.parametrize("bad", [None, "", "   ", "хрень", "32.13", "99.99.99"])
    def test_unparseable_returns_none(self, bad):
        assert _parse_chat_date_text(bad, _NOW) is None

    def test_old_date_is_before_cutoff(self):
        # Смысловая проверка: «31.12.25» должен попасть под порог 60 дней.
        cutoff = _NOW - timedelta(days=60)
        dt = _parse_chat_date_text("31.12.25", _NOW)
        assert dt is not None and dt < cutoff

    def test_recent_label_after_cutoff(self):
        # «вчера» при пороге 60 дней — не старый.
        cutoff = _NOW - timedelta(days=60)
        dt = _parse_chat_date_text("вчера", _NOW)
        assert dt is not None and dt > cutoff
