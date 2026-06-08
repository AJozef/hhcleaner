"""Тесты разбора дат: parse_iso_datetime и argparse-тип _iso_date."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pytest

from config import parse_iso_datetime
from hh_cleaner import _iso_date


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
