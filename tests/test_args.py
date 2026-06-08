"""Тесты parse_args — дефолты, env-переменные, precedence."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import hh_cleaner


def _parse(monkeypatch, argv):
    """Хелпер: подменяет sys.argv и зовёт parse_args()."""
    monkeypatch.setattr("sys.argv", ["hhcleaner"] + list(argv))
    return hh_cleaner.parse_args()


class TestDefaults:
    def test_no_args_uses_baseline(self, monkeypatch):
        args = _parse(monkeypatch, [])
        # steps пуст — caller подставит DEFAULT_STEPS.
        assert args.steps == []
        assert args.dry_run is False
        assert args.no_input is False
        assert args.quiet is False
        assert args.headed is False
        assert args.since is None


class TestFlags:
    def test_days_flag(self, monkeypatch):
        args = _parse(monkeypatch, ["--days", "45"])
        assert args.days == 45

    def test_quiet_flag(self, monkeypatch):
        args = _parse(monkeypatch, ["--quiet"])
        assert args.quiet is True

    def test_dry_run_flag(self, monkeypatch):
        args = _parse(monkeypatch, ["--dry-run"])
        assert args.dry_run is True

    def test_no_input_flag(self, monkeypatch):
        args = _parse(monkeypatch, ["--no-input"])
        assert args.no_input is True

    def test_headed_flag(self, monkeypatch):
        args = _parse(monkeypatch, ["--headed"])
        assert args.headed is True

    def test_since_parsed_to_utc_datetime(self, monkeypatch):
        args = _parse(monkeypatch, ["--since", "2025-01-15"])
        assert args.since == datetime(2025, 1, 15, tzinfo=timezone.utc)

    def test_since_invalid_date_rejected(self, monkeypatch):
        # Битая дата у удаляющего инструмента должна остановить запуск,
        # а не молча уйти на дефолтное окно --days.
        with pytest.raises(SystemExit):
            _parse(monkeypatch, ["--since", "2025-13-99"])

    def test_max_delete(self, monkeypatch):
        args = _parse(monkeypatch, ["--max-delete", "20"])
        assert args.max_delete == 20


class TestSteps:
    def test_valid_steps(self, monkeypatch):
        args = _parse(monkeypatch, ["negotiations", "old-chats"])
        assert args.steps == ["negotiations", "old-chats"]

    def test_invalid_step_rejected(self, monkeypatch):
        with pytest.raises(SystemExit):
            _parse(monkeypatch, ["bogus-step"])

    def test_single_step(self, monkeypatch):
        args = _parse(monkeypatch, ["read-all"])
        assert args.steps == ["read-all"]


class TestNumericValidation:
    @pytest.mark.parametrize("flag", ["--days", "--max-delete"])
    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5"])
    def test_rejects_non_positive_and_non_int(self, monkeypatch, flag, bad):
        with pytest.raises(SystemExit):
            _parse(monkeypatch, [flag, bad])

    @pytest.mark.parametrize("flag,attr", [("--days", "days"), ("--max-delete", "max_delete")])
    def test_accepts_positive(self, monkeypatch, flag, attr):
        args = _parse(monkeypatch, [flag, "7"])
        assert getattr(args, attr) == 7