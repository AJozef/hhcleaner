"""Тесты маршрутизации команд (cli.dispatch): какая команда в какой обработчик.

Без сети/браузера: для команд с браузером подменяем sync_playwright фейковым
контекст-менеджером и мокаем _cmd_*; для остальных — мокаем функции-обработчики.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import cli
from config import EXIT_LOGIN_FAILED, EXIT_OK


class _FakePW:
    """Контекст-менеджер вместо sync_playwright(): отдаёт мок playwright."""

    def __enter__(self):
        return MagicMock(name="playwright")

    def __exit__(self, *exc):
        return False


# ──────────────────────────── команды без браузера ───────────────────────────


class TestNoBrowserCommands:
    def test_log_show(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            cli, "show_log",
            lambda lines, path: captured.update(lines=lines, path=path) or EXIT_OK,
        )
        assert cli.dispatch(cli.parse_args(["log", "show", "100"])) == EXIT_OK
        assert captured["lines"] == 100
        assert captured["path"] is None  # --log не задан

    def test_log_clear_passes_yes(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            cli, "clear_log",
            lambda path, assume_yes: captured.update(yes=assume_yes) or EXIT_OK,
        )
        assert cli.dispatch(cli.parse_args(["log", "clear", "--yes"])) == EXIT_OK
        assert captured["yes"] is True

    def test_schedule_install(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            cli, "install_schedule",
            lambda day, time: captured.update(day=day, time=time) or 0,
        )
        assert cli.dispatch(cli.parse_args(
            ["schedule", "install", "--day", "FRI", "--time", "10:00"])) == 0
        assert captured == {"day": "FRI", "time": "10:00"}

    def test_schedule_uninstall(self, monkeypatch):
        called = MagicMock(return_value=0)
        monkeypatch.setattr(cli, "uninstall_schedule", called)
        assert cli.dispatch(cli.parse_args(["schedule", "uninstall"])) == 0
        called.assert_called_once_with()

    def test_doctor(self, monkeypatch):
        monkeypatch.setattr(cli, "self_check", lambda: 0)
        assert cli.dispatch(cli.parse_args(["doctor"])) == 0


# ──────────────────────────── команды с браузером ────────────────────────────


class TestBrowserCommands:
    def test_bare_clean_routes_to_cmd_clean(self, monkeypatch):
        monkeypatch.setattr(cli, "sync_playwright", lambda: _FakePW())
        monkeypatch.setattr(cli, "_cmd_clean", lambda p, args: EXIT_OK)
        assert cli.dispatch(cli.parse_args([])) == EXIT_OK

    def test_login_routes_to_cmd_login(self, monkeypatch):
        monkeypatch.setattr(cli, "sync_playwright", lambda: _FakePW())
        sentinel = MagicMock(return_value=EXIT_OK)
        monkeypatch.setattr(cli, "_cmd_login", sentinel)
        assert cli.dispatch(cli.parse_args(["login"])) == EXIT_OK
        sentinel.assert_called_once()

    def test_check_routes_to_cmd_check(self, monkeypatch):
        monkeypatch.setattr(cli, "sync_playwright", lambda: _FakePW())
        monkeypatch.setattr(cli, "_cmd_check", lambda p, args: 7)
        assert cli.dispatch(cli.parse_args(["check"])) == 7

    def test_status_routes_to_cmd_status(self, monkeypatch):
        monkeypatch.setattr(cli, "sync_playwright", lambda: _FakePW())
        monkeypatch.setattr(cli, "_cmd_status", lambda p, args: 0)
        assert cli.dispatch(cli.parse_args(["status"])) == 0

    def test_login_error_maps_to_exit_code(self, monkeypatch):
        # LoginError из любого браузерного обработчика → код «вход не удался».
        monkeypatch.setattr(cli, "sync_playwright", lambda: _FakePW())

        def _boom(p):
            raise cli.auth.LoginError("браузер не стартовал")

        monkeypatch.setattr(cli, "_cmd_login", _boom)
        assert cli.dispatch(cli.parse_args(["login"])) == EXIT_LOGIN_FAILED
