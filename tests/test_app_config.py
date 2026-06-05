"""Тесты персистентного конфига: валидация, round-trip, источники."""
from __future__ import annotations

import app_config


class TestSetKeyValidation:
    def test_unknown_key_rejected(self, isolated_home):
        ok, msg = app_config.set_key("nonexistent", "1")
        assert ok is False
        assert "Неизвестный ключ" in msg

    def test_int_must_be_positive(self, isolated_home):
        ok, msg = app_config.set_key("days", "0")
        assert ok is False
        ok, _ = app_config.set_key("days", "-5")
        assert ok is False

    def test_int_non_numeric_rejected(self, isolated_home):
        ok, _ = app_config.set_key("days", "abc")
        assert ok is False

    def test_bool_parsing(self, isolated_home):
        ok, _ = app_config.set_key("quiet", "yes")
        assert ok is True
        assert app_config.load()["quiet"] is True

        ok, _ = app_config.set_key("quiet", "нет")
        assert ok is True
        assert app_config.load()["quiet"] is False

    def test_bool_invalid_rejected(self, isolated_home):
        ok, _ = app_config.set_key("quiet", "maybe")
        assert ok is False


class TestRoundTrip:
    def test_set_load_persists(self, isolated_home):
        app_config.set_key("days", "30")
        app_config.set_key("profile", "work")
        cfg = app_config.load()
        assert cfg["days"] == 30
        assert cfg["profile"] == "work"

    def test_unset_removes_key(self, isolated_home):
        app_config.set_key("days", "30")
        ok, _ = app_config.unset_key("days")
        assert ok is True
        assert "days" not in app_config.load()

    def test_unset_missing_key(self, isolated_home):
        ok, _ = app_config.unset_key("days")
        assert ok is False

    def test_reset_removes_file(self, isolated_home):
        app_config.set_key("days", "30")
        assert app_config.config_path().exists()
        assert app_config.reset() is True
        assert not app_config.config_path().exists()
        # Повторный reset — файла уже нет.
        assert app_config.reset() is False

    def test_string_value_escaping(self, isolated_home):
        # Путь с обратными слэшами (Windows) должен пережить round-trip.
        app_config.set_key("log", r"C:\logs\hh.log")
        assert app_config.load()["log"] == r"C:\logs\hh.log"


class TestArgparseDefaults:
    def test_empty_when_no_config(self, isolated_home):
        assert app_config.as_argparse_defaults() == {}

    def test_only_known_keys(self, isolated_home):
        app_config.set_key("days", "30")
        defaults = app_config.as_argparse_defaults()
        assert defaults == {"days": 30}
