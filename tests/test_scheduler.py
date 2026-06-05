"""Тесты сборки команды планировщика."""
from __future__ import annotations

import scheduler


class TestScheduledRunCommand:
    def test_contains_unattended_flags(self):
        cmd = scheduler._scheduled_run_command()
        assert "--quiet" in cmd
        assert "--no-input" in cmd
        assert "--log" in cmd
