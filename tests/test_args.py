"""Тесты parse_args — подкоманды, дефолты, неявный clean, precedence."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import cli


def _parse(argv):
    """Хелпер: разбирает аргументы через cli.parse_args (минуя sys.argv)."""
    return cli.parse_args(list(argv))


class TestDefaultCommand:
    def test_no_args_is_clean(self):
        args = _parse([])
        assert args.command == "clean"
        # steps пуст — caller подставит DEFAULT_STEPS.
        assert args.steps == []
        assert args.dry_run is False
        assert args.no_input is False
        assert args.quiet is False
        assert args.headed is False
        assert args.since is None
        assert args.yes is False
        assert args.force_browser is False

    def test_bare_steps_imply_clean(self):
        args = _parse(["negotiations", "old-chats"])
        assert args.command == "clean"
        assert args.steps == ["negotiations", "old-chats"]

    def test_bare_options_imply_clean(self):
        # Плановый прогон `hhcleaner --quiet --no-input --log` — неявный clean.
        args = _parse(["--quiet", "--no-input"])
        assert args.command == "clean"
        assert args.quiet is True
        assert args.no_input is True

    def test_explicit_clean(self):
        args = _parse(["clean", "read-all"])
        assert args.command == "clean"
        assert args.steps == ["read-all"]


class TestCleanFlags:
    def test_days_flag(self):
        assert _parse(["--days", "45"]).days == 45

    def test_dry_run_flag(self):
        assert _parse(["--dry-run"]).dry_run is True

    def test_force_browser_flag(self):
        assert _parse(["--force-browser"]).force_browser is True

    def test_relogin_flag(self):
        assert _parse(["--relogin"]).relogin is True

    def test_since_parsed_to_utc_datetime(self):
        args = _parse(["--since", "2025-01-15"])
        assert args.since == datetime(2025, 1, 15, tzinfo=timezone.utc)

    def test_since_invalid_date_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["--since", "2025-13-99"])

    def test_max_delete(self):
        assert _parse(["--max-delete", "20"]).max_delete == 20


class TestSteps:
    def test_invalid_step_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["bogus-step"])

    def test_single_step(self):
        assert _parse(["read-all"]).steps == ["read-all"]

    def test_all_steps(self):
        assert _parse(["read-all", "negotiations"]).steps == ["read-all", "negotiations"]

    def test_empty_steps_ok_on_all_python_versions(self):
        # Регрессия: nargs='*' + choices=ALL_STEPS падал на Python 3.9–3.12
        # («invalid choice: []») при пустых steps — а это голый `hhcleaner` и
        # плановый `--no-input` прогон. Теперь шаги валидируются через type=_step,
        # пустой список проходит без ошибки.
        assert _parse([]).steps == []
        assert _parse(["--no-input"]).steps == []


class TestSubcommands:
    def test_login(self):
        assert _parse(["login"]).command == "login"

    def test_check(self):
        assert _parse(["check"]).command == "check"

    def test_status_with_days(self):
        args = _parse(["status", "--days", "10"])
        assert args.command == "status"
        assert args.days == 10

    def test_doctor(self):
        assert _parse(["doctor"]).command == "doctor"

    def test_schedule_install(self):
        args = _parse(["schedule", "install", "--day", "FRI", "--time", "10:00"])
        assert args.command == "schedule"
        assert args.action == "install"
        assert args.day == "FRI"
        assert args.time == "10:00"

    def test_schedule_uninstall(self):
        args = _parse(["schedule", "uninstall"])
        assert args.command == "schedule"
        assert args.action == "uninstall"

    def test_schedule_bad_day_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["schedule", "install", "--day", "FUNDAY"])

    def test_schedule_bad_time_rejected(self):
        with pytest.raises(SystemExit):
            _parse(["schedule", "install", "--time", "9am"])

    def test_log_show_with_count(self):
        args = _parse(["log", "show", "100"])
        assert args.command == "log"
        assert args.action == "show"
        assert args.lines == 100

    def test_log_show_default_count(self):
        args = _parse(["log", "show"])
        assert args.action == "show"
        assert args.lines == 50

    def test_log_clear_yes(self):
        args = _parse(["log", "clear", "--yes"])
        assert args.action == "clear"
        assert args.yes is True


class TestNumericValidation:
    @pytest.mark.parametrize("flag", ["--days", "--max-delete"])
    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "1.5"])
    def test_rejects_non_positive_and_non_int(self, flag, bad):
        with pytest.raises(SystemExit):
            _parse([flag, bad])

    @pytest.mark.parametrize("flag,attr", [("--days", "days"), ("--max-delete", "max_delete")])
    def test_accepts_positive(self, flag, attr):
        assert getattr(_parse([flag, "7"]), attr) == 7
