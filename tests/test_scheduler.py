"""Тесты сборки команды планировщика и проверки наличия задачи."""
from __future__ import annotations

import types

import scheduler


class TestScheduledRunCommand:
    def test_contains_unattended_flags(self):
        cmd = scheduler._scheduled_run_command()
        assert "--quiet" in cmd
        assert "--no-input" in cmd
        assert "--log" in cmd

    def test_frozen_calls_exe_directly(self, monkeypatch):
        # В собранном .exe sys.executable И ЕСТЬ бинарь — зовём его напрямую,
        # даже если файл переименован (напр. «hhcleaner (1).exe»).
        monkeypatch.setattr(scheduler.sys, "frozen", True, raising=False)
        monkeypatch.setattr(scheduler.sys, "executable", r"C:\dl\hhcleaner (1).exe", raising=False)
        cmd = scheduler._scheduled_run_command()
        assert cmd.startswith(r'"C:\dl\hhcleaner (1).exe"')
        assert "--no-input" in cmd


class TestScheduleExists:
    def test_non_windows_is_false(self, monkeypatch):
        monkeypatch.setattr(scheduler.platform, "system", lambda: "Linux")
        assert scheduler.schedule_exists() is False

    def test_true_when_query_returns_zero(self, monkeypatch):
        monkeypatch.setattr(scheduler.platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            scheduler.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0),
        )
        assert scheduler.schedule_exists() is True

    def test_false_when_query_returns_nonzero(self, monkeypatch):
        monkeypatch.setattr(scheduler.platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            scheduler.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=1),
        )
        assert scheduler.schedule_exists() is False

    def test_false_when_schtasks_missing(self, monkeypatch):
        monkeypatch.setattr(scheduler.platform, "system", lambda: "Windows")

        def _raise(*_a, **_k):
            raise FileNotFoundError

        monkeypatch.setattr(scheduler.subprocess, "run", _raise)
        assert scheduler.schedule_exists() is False
