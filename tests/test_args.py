"""Тесты parse_args — дефолты, env-переменные, precedence."""
from __future__ import annotations

import pytest

import hh_cleaner


def _parse(monkeypatch, argv):
    """Хелпер: подменяет sys.argv и зовёт parse_args()."""
    monkeypatch.setattr("sys.argv", ["hhcleaner"] + list(argv))
    return hh_cleaner.parse_args()


class TestDefaults:
    def test_no_args_uses_baseline(self, monkeypatch):
        monkeypatch.delenv("HH_DELETE_WORKERS", raising=False)
        args = _parse(monkeypatch, [])
        # steps пуст — caller подставит DEFAULT_STEPS.
        assert args.steps == []
        assert args.workers == 1
        assert args.dry_run is False
        assert args.no_input is False
        assert args.quiet is False
        assert args.headed is False
        assert args.since is None


class TestWorkersPrecedence:
    def test_workers_from_cli(self, monkeypatch):
        monkeypatch.delenv("HH_DELETE_WORKERS", raising=False)
        args = _parse(monkeypatch, ["--workers", "5"])
        assert args.workers == 5

    def test_workers_from_env(self, monkeypatch):
        monkeypatch.setenv("HH_DELETE_WORKERS", "4")
        args = _parse(monkeypatch, [])
        assert args.workers == 4

    def test_cli_overrides_env(self, monkeypatch):
        monkeypatch.setenv("HH_DELETE_WORKERS", "4")
        args = _parse(monkeypatch, ["--workers", "8"])
        assert args.workers == 8

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("HH_DELETE_WORKERS", "not a number")
        args = _parse(monkeypatch, [])
        assert args.workers == 1


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

    def test_since_passed_as_string(self, monkeypatch):
        args = _parse(monkeypatch, ["--since", "2025-01-15"])
        assert args.since == "2025-01-15"

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