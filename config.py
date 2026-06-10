"""Общие константы и утилиты для hh_cleaner."""
from __future__ import annotations

import atexit
import os
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()  # загружает .env рядом со скриптом; без файла — тихо игнорирует

# Каталог данных приложения: профиль браузера и лог. Лежит в домашней папке,
# чтобы установка через pip (в site-packages) не пыталась писать рядом с кодом.
# Переопределяется HHCLEANER_HOME — удобно для переносных установок и тестов.
APP_DIR = os.environ.get("HHCLEANER_HOME") or os.path.join(os.path.expanduser("~"), ".hhcleaner")
DEFAULT_LOG_FILE = os.path.join(APP_DIR, "hhcleaner.log")
USER_DATA_DIR = os.path.join(APP_DIR, "userdata")
# Маркер «первый удаляющий прогон уже был». Пока его нет, интерактивный clean
# гонится как предпросмотр (dry-run), чтобы испуганный новичок не снёс всё с
# первого запуска не глядя. См. first_run_pending / mark_first_run_done.
FIRST_RUN_MARKER = os.path.join(APP_DIR, "first_run_done.flag")


def ensure_app_dir() -> str:
    """Создаёт каталог приложения (если нужно) и возвращает его путь."""
    os.makedirs(APP_DIR, exist_ok=True)
    return APP_DIR


def first_run_pending() -> bool:
    """True, если ещё не было ни одного подтверждённого удаляющего прогона."""
    return not os.path.exists(FIRST_RUN_MARKER)


def mark_first_run_done() -> None:
    """Помечает, что первый реальный прогон состоялся (страховка-предпросмотр снята)."""
    ensure_app_dir()
    try:
        with open(FIRST_RUN_MARKER, "w", encoding="utf-8") as fh:
            fh.write(datetime.now().isoformat())
    except OSError:
        pass  # не смогли записать маркер — не критично, в следующий раз снова предпросмотр


def package_version() -> str:
    """Версия пакета из метаданных, либо 'dev' при запуске из исходников.

    Единый источник для --version и doctor, чтобы не дублировать
    try/except PackageNotFoundError в разных модулях.
    """
    try:
        return version("hhcleaner")
    except PackageNotFoundError:
        return "dev"


def parse_iso_datetime(value) -> datetime | None:
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

# User-Agent для HTTP-запросов к API. Должен совпадать с тем, что отправляет
# реальный браузер — open_session() перезаписывает это значение фактическим UA
# из запущенного Playwright-контекста; строка ниже используется только как запасной
# вариант, если evaluate("navigator.userAgent") не сработает.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0"
)

# Браузеры для входа на hh.ru. Используем УЖЕ установленный в системе браузер
# через Playwright channel — не качаем свой Chromium (на Windows 10/11 Edge есть
# всегда). Порядок = порядок попыток; пустая строка-маркер _BUNDLED в auth.py
# означает запасной встроенный Chromium, если системного браузера вдруг нет.
BROWSER_CHANNELS = ("msedge", "chrome")

# Порог по умолчанию для шага old-chats. Переопределяется флагом --days.
OLD_CHATS_DAYS = 60

# Размер страницы API и паузы между запросами (сек).
CHATS_PER_PAGE = 50   # максимум, который отдаёт API чатов
REQUEST_PAUSE  = 0.2  # между однотипными вызовами (удаление/чтение)
PAGE_PAUSE     = 0.5  # между страницами пагинации
RETRY_BACKOFF  = 5    # база экспоненциального backoff перед повтором запроса

# Паузы для браузерного пути (chats_browser.py).
BROWSER_INIT_PAUSE    = 1.5  # сброс скролла перед началом сбора
BROWSER_SCROLL_PAUSE  = 0.8  # между шагами прокрутки
BROWSER_NAV_PAUSE     = 1.2  # после page.goto в режиме ожидания рендера
BROWSER_CLICK_PAUSE   = 0.7  # после клика по меню чата
BROWSER_LEAVE_PAUSE   = 0.8  # после клика «Покинуть чат»
BROWSER_VACANCY_PAUSE = 1.0  # после goto при проверке архивной вакансии

# Коды выхода процесса. Живут здесь — в общем бездепендном модуле, — чтобы
# hh_cleaner и cli_cmds не держали собственные копии (раньше cli_cmds заводил
# локальный EXIT_OK именно ради того, чтобы не импортировать hh_cleaner и не
# создать цикл).
EXIT_OK           = 0  # успех
EXIT_LOGIN_FAILED = 2  # вход не удался
EXIT_NEED_LOGIN   = 3  # нужен ручной вход (hhcleaner login)

_STATE = {"quiet": False, "file_console": None}

console = Console(highlight=False)
# Ошибки идут в stderr, чтобы не мешаться с обычным выводом в stdout.
err_console = Console(stderr=True, highlight=False)


def set_quiet(value: bool) -> None:
    """Включает или выключает тихий режим вывода."""
    _STATE["quiet"] = value


def is_quiet() -> bool:
    """True, если тихий режим активен."""
    return _STATE["quiet"]


def set_log_file(path: str) -> None:
    """Дублирует весь вывод в файл (append). Файл пишется всегда, даже в --quiet.

    """
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
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
