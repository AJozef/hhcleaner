"""Тесты диспетчера подкоманды config (cli_cmds.cmd_config)."""
from __future__ import annotations

import pytest

import app_config
from cli_cmds import cmd_config


def test_set_then_persisted(isolated_home):
    assert cmd_config(["set", "days", "30"]) == 0
    assert app_config.load()["days"] == 30


def test_set_invalid_value_returns_1(isolated_home):
    assert cmd_config(["set", "days", "abc"]) == 1


def test_unset_returns_0(isolated_home):
    cmd_config(["set", "days", "30"])
    assert cmd_config(["unset", "days"]) == 0
    assert "days" not in app_config.load()


def test_show_returns_0(isolated_home):
    assert cmd_config(["show"]) == 0


def test_bare_defaults_to_show(isolated_home):
    assert cmd_config([]) == 0


def test_bad_key_exits_via_argparse(isolated_home):
    # argparse при неверном choice печатает usage и делает sys.exit(2).
    with pytest.raises(SystemExit) as exc:
        cmd_config(["set", "bogus", "1"])
    assert exc.value.code == 2
