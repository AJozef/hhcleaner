"""Общие фикстуры для тестов hhcleaner."""
from __future__ import annotations

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Изолирует каталог данных приложения во временную папку.

    app_config._config_path() и config.* читают HHCLEANER_HOME при каждом
    вызове, поэтому достаточно подменить переменную окружения — реальный
    ~/.hhcleaner не трогается.
    """
    monkeypatch.setenv("HHCLEANER_HOME", str(tmp_path))
    return tmp_path
