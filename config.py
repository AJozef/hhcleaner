"""Общие константы и утилиты для hh_cleaner."""
from __future__ import annotations

import atexit
import os
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()  # загружает .env рядом со скриптом; без файла — тихо игнорирует

# Каталог данных приложения: профиль браузера и лог. Лежит в домашней папке,
# чтобы установка через pip (в site-packages) не пыталась писать рядом с кодом.
# Переопределяется HHCLEANER_HOME — удобно для переносных установок и тестов.
APP_DIR = os.environ.get("HHCLEANER_HOME") or os.path.join(os.path.expanduser("~"), ".hhcleaner")
DEFAULT_LOG_FILE = os.path.join(APP_DIR, "hhcleaner.log")
USER_DATA_DIR = os.path.join(APP_DIR, "userdata")


def ensure_app_dir() -> str:
    """Создаёт каталог приложения (если нужно) и возвращает его путь."""
    os.makedirs(APP_DIR, exist_ok=True)
    return APP_DIR


def parse_iso_datetime(value) -> Optional[datetime]:
    """Парсит ISO-строку (или None) в tz-aware datetime (UTC). None при ошибке.

    Единственный источник логики разбора дат: импортируется из chats_api и
    chats_browser, чтобы не дублировать обработку «Z» и naive-tzinfo.
    """
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


HH_URL = "https://hh.ru"
LOGIN_URL = "https://hh.ru/account/login"  # прямая форма входа (роль «соискатель» по умолчанию)
NEGOTIATIONS_URL = "https://hh.ru/applicant/negotiations"
CHATIK_URL = "https://chatik.hh.ru/?platform=xhh"

# Эндпойнты API чатов (chatik). Собираются от базы — не повторяем хост в коде.
CHATIK_API = "https://chatik.hh.ru/chatik/api"
CHATS_ENDPOINT = f"{CHATIK_API}/chats"
LEAVE_ENDPOINT = f"{CHATIK_API}/leave"
MARK_READ_ENDPOINT = f"{CHATIK_API}/mark_read"

# User-Agent для запросов к API (требуется, иначе часть ответов отличается).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) "
    "Gecko/20100101 Firefox/150.0"
)

OLD_CHATS_DAYS = int(os.environ.get("HH_OLD_DAYS", 60))
CHATS_PER_PAGE = int(os.environ.get("HH_CHATS_PER_PAGE", 50))

# Паузы между API-запросами (сек): защита от троттлинга. Настраиваются из .env.
REQUEST_PAUSE = float(os.environ.get("HH_REQUEST_PAUSE", 0.2))  # между однотипными вызовами
PAGE_PAUSE = float(os.environ.get("HH_PAGE_PAUSE", 0.5))        # между страницами пагинации
RETRY_BACKOFF = float(os.environ.get("HH_RETRY_BACKOFF", 5))    # пауза перед повтором запроса

_STATE = {"quiet": False, "file_console": None}

console = Console(highlight=False)
# Ошибки идут в stderr: stdout остаётся чистым для машинного вывода (--output json).
err_console = Console(stderr=True, highlight=False)


def set_quiet(value: bool) -> None:
    """Включает или выключает тихий режим вывода."""
    _STATE["quiet"] = value


def is_quiet() -> bool:
    """True, если тихий режим активен."""
    return _STATE["quiet"]


MAX_LOG_SIZE = 5 * 1024 * 1024  # 5 МБ: порог ротации перед открытием файла
MAX_LOG_BACKUPS = 3             # .1 / .2 / .3 — старейший перезаписывается


def _rotate_log(path: str) -> None:
    """Если лог превысил MAX_LOG_SIZE — ротирует файлы (best-effort).

    path     → path.1 → path.2 → path.3 (старейший удаляется).
    При следующем открытии path создаётся заново с чистого листа.
    """
    try:
        if not (os.path.isfile(path) and os.path.getsize(path) > MAX_LOG_SIZE):
            return
        # Сдвигаем бэкапы от старшего к младшему, чтобы не потерять .1 раньше времени.
        for n in range(MAX_LOG_BACKUPS, 0, -1):
            src = f"{path}.{n - 1}" if n > 1 else path
            dst = f"{path}.{n}"
            if os.path.isfile(src):
                if os.path.isfile(dst):
                    os.remove(dst)
                os.rename(src, dst)
    except Exception:  # pylint: disable=broad-exception-caught
        pass  # ротация некритична — продолжаем дописывать в старый файл


def set_log_file(path: str) -> None:
    """Дублирует весь вывод в файл (append). Файл пишется всегда, даже в --quiet.

    При превышении MAX_LOG_SIZE старый лог переименовывается в .1 перед открытием.
    Прогресс-бары в файл не идут — там нет смысла от анимации.
    """
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    _rotate_log(path)
    # Хендл живёт весь процесс (в него пишут логгеры), поэтому не `with`, а atexit.
    fh = open(path, "a", encoding="utf-8", buffering=1)  # pylint: disable=consider-using-with
    atexit.register(fh.close)  # гарантированно сбрасываем буфер и закрываем при выходе
    fc = Console(file=fh, no_color=True, highlight=False, width=100)
    fc.print(f"\n===== Запуск {datetime.now():%Y-%m-%d %H:%M:%S} =====")
    _STATE["file_console"] = fc


def file_console():
    """Console для файла-лога или None. Нужен, чтобы зеркалить итоговую таблицу."""
    return _STATE["file_console"]


def _to_file(method: str, *args, **kwargs) -> None:
    """Зеркалит вывод в файл-лог, если он настроен."""
    fc = _STATE["file_console"]
    if fc is not None:
        getattr(fc, method)(*args, **kwargs)


def log(msg: str = "") -> None:
    """Выводит сообщение, если тихий режим не активен."""
    if not _STATE["quiet"]:
        console.print(msg, markup=False)
    _to_file("print", msg, markup=False)


def log_section(title: str) -> None:
    """Горизонтальный разделитель с заголовком раздела."""
    if not _STATE["quiet"]:
        console.rule(f"[bold cyan]{title}[/bold cyan]")
    _to_file("rule", title)


def log_ok(msg: str) -> None:
    """Зелёное сообщение — успешное завершение операции."""
    if not _STATE["quiet"]:
        console.print(f"[green]{msg}[/green]")
    _to_file("print", msg, markup=False)


def log_err(msg: str) -> None:
    """Красное сообщение об ошибке — в stderr, выводится даже в тихом режиме."""
    err_console.print(f"[bold red]{msg}[/bold red]")
    _to_file("print", msg, markup=False)


def log_warn(msg: str) -> None:
    """Жёлтое предупреждение."""
    if not _STATE["quiet"]:
        console.print(f"[yellow]{msg}[/yellow]")
    _to_file("print", msg, markup=False)
