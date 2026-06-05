"""Персистентный конфиг приложения: ~/.hhcleaner/config.toml.

Хранит пользовательские дефолты, которые применяются при каждом запуске
без явной передачи флагов. Приоритет: CLI-флаг > config.toml > HH_* env > хардкод.

Команды:
    hhcleaner config show              # текущие настройки + их источник
    hhcleaner config set KEY VALUE     # установить значение
    hhcleaner config reset             # удалить файл (сброс к дефолтам)

Поддерживаемые ключи:
    profile     — профиль по умолчанию (строка, например "work")
    days        — порог для old-chats в днях (целое > 0)
    log         — путь к лог-файлу (строка) или "" чтобы не логировать
    quiet       — тихий режим (true/false)
    headed      — показывать окно браузера (true/false)
    max_delete  — лимит удалений за шаг (целое > 0)
    workers     — потоков для параллельного удаления (1 = последовательно)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# tomllib встроен в Python 3.11+; для 3.9/3.10 используем tomli (зависимость).
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

# Тип конфига — плоский словарь.
AppConfig = dict[str, Any]

# Поддерживаемые ключи, их тип и короткое описание.
KNOWN_KEYS: dict[str, tuple[type, str]] = {
    "profile":    (str,  "профиль по умолчанию"),
    "days":       (int,  "порог old-chats в днях"),
    "log":        (str,  "путь к лог-файлу ('' = выключен)"),
    "quiet":      (bool, "тихий режим"),
    "headed":     (bool, "показывать окно браузера"),
    "max_delete": (int,  "лимит удалений за шаг"),
    "workers":    (int,  "потоков для параллельного удаления (1 = последовательно)"),
}

_BOOL_TRUE  = {"1", "true", "yes", "on", "д", "да"}
_BOOL_FALSE = {"0", "false", "no", "off", "н", "нет"}


def _config_path() -> Path:
    """Путь к config.toml (учитывает HHCLEANER_HOME).

    Намеренно не импортирует APP_DIR из config, чтобы избежать циклического
    импорта (config -> app_config -> config).  Логика дублируется локально.
    """
    app_dir = (
        os.environ.get("HHCLEANER_HOME")
        or os.path.join(os.path.expanduser("~"), ".hhcleaner")
    )
    return Path(app_dir) / "config.toml"


def config_path() -> Path:
    """Публичный доступ к пути config.toml (без нарушения инкапсуляции)."""
    return _config_path()


# ──────────────────────────── read / write ────────────────────────────────────

def load() -> AppConfig:
    """Загружает секцию [defaults] из config.toml. Пустой словарь при любой ошибке."""
    path = _config_path()
    if not path.exists():
        return {}
    if tomllib is None:
        return {}
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        return dict(data.get("defaults", {}))
    except Exception:  # pylint: disable=broad-exception-caught
        return {}


def save(cfg: AppConfig) -> None:
    """Сохраняет конфиг (перезаписывает весь файл)."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Конфиг hhcleaner. Редактировать вручную или через:\n",
        "#   hhcleaner config set KEY VALUE\n",
        "#   hhcleaner config reset\n",
        "\n",
        "[defaults]\n",
    ]
    for key in sorted(KNOWN_KEYS):
        if key not in cfg:
            continue
        val = cfg[key]
        if isinstance(val, bool):
            lines.append(f"{key} = {'true' if val else 'false'}\n")
        elif isinstance(val, int):
            lines.append(f"{key} = {val}\n")
        else:
            escaped = str(val).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"\n')
    path.write_text("".join(lines), encoding="utf-8")


# ──────────────────────────── set / reset ─────────────────────────────────────

def set_key(key: str, raw_value: str) -> tuple[bool, str]:
    """Устанавливает одно значение. Возвращает (ok, сообщение)."""
    if key not in KNOWN_KEYS:
        known = ", ".join(sorted(KNOWN_KEYS))
        return False, f"Неизвестный ключ «{key}». Доступные: {known}."

    expected_type, _ = KNOWN_KEYS[key]
    try:
        if expected_type is bool:
            low = raw_value.lower()
            if low in _BOOL_TRUE:
                value: Any = True
            elif low in _BOOL_FALSE:
                value = False
            else:
                return False, (
                    f"Для bool-ключей ожидается true/false/yes/no/1/0, получено «{raw_value}»."
                )
        elif expected_type is int:
            value = int(raw_value)
            if value <= 0:
                return False, f"«{key}» должно быть положительным числом, получено {value}."
        else:
            value = raw_value
    except ValueError:
        return False, f"Не удалось преобразовать «{raw_value}» в {expected_type.__name__}."

    cfg = load()
    cfg[key] = value
    save(cfg)
    return True, f"{key} = {value!r}"


def unset_key(key: str) -> tuple[bool, str]:
    """Удаляет один ключ из конфига."""
    if key not in KNOWN_KEYS:
        return False, f"Неизвестный ключ «{key}»."
    cfg = load()
    if key not in cfg:
        return False, f"Ключ «{key}» не задан в конфиге."
    del cfg[key]
    save(cfg)
    return True, f"Ключ «{key}» удалён."


def reset() -> bool:
    """Удаляет файл конфига. True если файл был."""
    path = _config_path()
    if path.exists():
        path.unlink()
        return True
    return False


# ──────────────────────── интеграция с argparse ───────────────────────────────

def as_argparse_defaults() -> dict[str, Any]:
    """Конфиг в виде словаря для parser.set_defaults(**...).

    Ключи с подчёркиванием совпадают с dest-именами argparse
    (max_delete, headed и т.д.).
    """
    cfg = load()
    result: dict[str, Any] = {}
    for key in KNOWN_KEYS:
        if key in cfg:
            result[key] = cfg[key]
    return result
