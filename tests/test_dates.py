"""Тесты разбора дат: parse_iso_datetime и _parse_since."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from config import parse_iso_datetime
from hh_cleaner import _parse_since


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


class TestParseSince:
    def test_none_returns_none(self):
        assert _parse_since(None) is None
        assert _parse_since("") is None

    def test_valid_date(self):
        dt = _parse_since("2025-01-15")
        assert dt == datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)

    def test_whitespace_trimmed(self):
        dt = _parse_since("  2025-01-15  ")
        assert dt == datetime(2025, 1, 15, 0, 0, tzinfo=timezone.utc)

    @pytest.mark.parametrize("bad", ["2025/01/15", "15-01-2025", "хрень", "2025-13-01"])
    def test_invalid_format_returns_none(self, bad):
        assert _parse_since(bad) is None
