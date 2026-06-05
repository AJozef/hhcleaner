"""Тесты вспомогательных таблиц и сборки команды планировщика."""
from __future__ import annotations

import scheduler

_DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


class TestDayTables:
    def test_cron_table_complete(self):
        assert set(scheduler._DAY_TO_CRON) == set(_DAYS)
        # cron: воскресенье = 0, понедельник = 1.
        assert scheduler._DAY_TO_CRON["SUN"] == "0"
        assert scheduler._DAY_TO_CRON["MON"] == "1"

    def test_systemd_table_complete(self):
        assert set(scheduler._DAY_TO_SYSTEMD) == set(_DAYS)

    def test_launchd_table_complete(self):
        assert set(scheduler._DAY_TO_LAUNCHD) == set(_DAYS)
        # launchd: воскресенье = 0.
        assert scheduler._DAY_TO_LAUNCHD["SUN"] == 0


class TestScheduledRunCommand:
    def test_contains_unattended_flags(self):
        cmd = scheduler._scheduled_run_command("default")
        assert "--quiet" in cmd
        assert "--no-input" in cmd
        assert "--log" in cmd

    def test_default_profile_not_passed(self):
        cmd = scheduler._scheduled_run_command("default")
        assert "--profile" not in cmd

    def test_named_profile_passed(self):
        cmd = scheduler._scheduled_run_command("work")
        assert '--profile "work"' in cmd
