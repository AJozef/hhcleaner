# Разработка и сборка hhcleaner

## Окружение

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

Ставит основные зависимости (requests, playwright, rich, dotenv), dev-инструменты (pytest, pylint, pyinstaller) и сам пакет в editable-режиме (изменения кода видны сразу).

Браузер для входа берётся системный (Edge/Chrome) через Playwright channel — отдельно `playwright install chromium` запускать не нужно.

## Тесты и линтинг

```powershell
pytest                    # все тесты
pytest -v                 # подробно
pytest tests/test_args.py # конкретный файл

pylint hh_cleaner.py auth.py config.py steps.py chats_api.py chats_browser.py negotiations.py notify.py scheduler.py cli_cmds.py ui_selectors.py
```

## Ручная проверка

```powershell
hhcleaner --self-check    # диагностика окружения (браузер, сессия, конфиг)
hhcleaner --login-only    # вход и сохранение сессии
hhcleaner --check         # рабочая ли сессия
hhcleaner --dry-run       # что удалится, без удаления
hhcleaner                 # реальный прогон
```

---

## Сборка .exe

Один самодостаточный `dist\hhcleaner.exe` (~50 МБ, один файл):

```powershell
pyinstaller hhcleaner.spec --distpath dist --workpath build --noconfirm
# либо короче, скриптом-обёрткой:
build.bat
```

Проверка результата:

```powershell
dist\hhcleaner.exe --version
dist\hhcleaner.exe --self-check
```

### Что бандлится, а что нет

- **Бандлится:** node-драйвер Playwright (`collect_all('playwright')` в `hhcleaner.spec`) — он нужен, чтобы рулить системным браузером.
- **Не бандлится:** сам браузер. Вход идёт через установленный в системе Edge/Chrome (`auth.launch_context`), поэтому .exe лёгкий и первый запуск без скачивания ~150 МБ Chromium.
- Профиль и cookies живут в `~/.hhcleaner/`, а не внутри .exe.

Двойной клик по .exe без аргументов запускает дружелюбный визард (`hh_cleaner._wizard`); запуск с любыми флагами/шагами — обычный CLI. Поэтому `console=True` в spec.

### Файлы сборки

- `hhcleaner.spec` — конфиг PyInstaller (onefile, console, сбор драйвера Playwright).
- `build.bat` — обёртка над командой выше + smoke-тест.
- `.github/workflows/build.yml` — GitHub Actions: сборка и релиз при пуше тега.

---

## Релиз

Версия — единственный источник истины в `pyproject.toml`. Семантическое версионирование: патч (`1.1.0→1.1.1`) — багфиксы, минор (`→1.2.0`) — фичи, мажор (`→2.0.0`) — breaking.

Порядок выпуска:

```powershell
# 1. Тесты и линтинг зелёные
pytest -v

# 2. Поднять версию в pyproject.toml, закоммитить
git commit -am "Bump version to 1.2.0"
git push origin main

# 3. Тег → GitHub Actions сам соберёт .exe и создаст Release
git tag v1.2.0
git push origin v1.2.0
```

Прогресс — во вкладке [Actions](https://github.com/AJozef/hhcleaner/actions), результат — в [Releases](https://github.com/AJozef/hhcleaner/releases).

**Откат:** если релиз вышел плохим — удалите тег (`git tag -d v1.2.0; git push origin --delete v1.2.0`) и/или удалите Release на GitHub.

---

## Структура кода

```
hhcleaner/
├── hh_cleaner.py        # Точка входа, CLI-диспетчер, визард двойного клика
├── auth.py              # Вход через системный браузер, управление сессией
├── config.py            # Константы, логирование, коды выхода
├── steps.py             # Каталог шагов очистки (id, лейблы, порядок)
├── chats_api.py         # Удаление через HTTP API (основной метод, быстро)
├── chats_browser.py     # Удаление через браузер UI (резервный фолбэк)
├── negotiations.py      # Удаление откликов-отказов
├── notify.py            # Windows toast-уведомления
├── scheduler.py         # Регистрация в Windows Task Scheduler
├── cli_cmds.py          # Команды без браузера (self-check, лог)
├── ui_selectors.py      # CSS/data-qa селекторы веб-UI hh.ru
├── hhcleaner.spec       # Конфиг PyInstaller
├── build.bat            # Обёртка сборки .exe
└── tests/               # pytest: args, dates, orchestration, predicates, scheduler, session
```

---

## Troubleshooting сборки

**PyInstaller не найден** → `pip install "pyinstaller>=6.0"` (или `pip install -e ".[dev]"`).

**`No module named 'hh_cleaner'`** → выполните `pip install -e .` в активированном venv.

**В собранном .exe не стартует браузер** → на машине нет ни Edge, ни Chrome. Установите Microsoft Edge (на Windows 10/11 он есть по умолчанию) или Google Chrome. Проверить: `dist\hhcleaner.exe --self-check`.
