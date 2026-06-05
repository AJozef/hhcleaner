"""Тесты сборки команды планировщика."""
from __future__ import annotations

import scheduler


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
