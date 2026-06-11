# Разработка и сборка hhcleaner

## Окружение

**Для разработки** (editable-режим + dev-инструменты):

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

Ставит основные зависимости (requests, playwright, rich), dev-инструменты (pytest, pylint, pyinstaller) и сам пакет в editable-режиме (изменения кода видны сразу).

Браузер для входа берётся системный (Edge/Chrome) через Playwright channel — отдельно `playwright install chromium` запускать не нужно.

**Для конечного использования без .exe** — через [pipx](https://pipx.pypa.io/):

```powershell
pipx install .                  # из локальных исходников
# или:
pipx install git+https://github.com/AJozef/hhcleaner.git
```

pipx управляет изолированным venv сам и добавляет `hhcleaner` в PATH системно — не нужно активировать venv перед каждым запуском. Обновление: `pipx upgrade hhcleaner` (из git/PyPI) или `pipx install --force .` (из исходников).

## Тесты и линтинг

```powershell
pytest                    # все тесты
pytest -v                 # подробно
pytest tests/test_args.py # конкретный файл

pylint hh_cleaner.py cli.py wizard.py runner.py report.py auth.py config.py steps.py chats_api.py chats_browser.py negotiations.py notify.py scheduler.py cli_cmds.py ui_selectors.py
```

## Ручная проверка

```powershell
hhcleaner doctor          # диагностика окружения (браузер, сессия, конфиг)
hhcleaner login           # вход и сохранение сессии
hhcleaner check           # рабочая ли сессия
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
dist\hhcleaner.exe doctor
```

### Что бандлится, а что нет

- **Бандлится:** node-драйвер Playwright (`collect_all('playwright')` в `hhcleaner.spec`) — он нужен, чтобы рулить системным браузером.
- **Не бандлится:** сам браузер. Вход идёт через установленный в системе Edge/Chrome (`auth.launch_context`), поэтому .exe лёгкий и первый запуск без скачивания ~150 МБ Chromium.
- Профиль и cookies живут в `~/.hhcleaner/`, а не внутри .exe.

Двойной клик по .exe без аргументов запускает дружелюбный визард (`wizard.run`); запуск с любыми подкомандами/флагами/шагами — обычный CLI. Поэтому `console=True` в spec.

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
├── hh_cleaner.py        # Точка входа: визард-или-CLI (тонкая main)
├── cli.py               # Подкоманды argparse, их обработчики, диспетчер
├── wizard.py            # Визард двойного клика (.exe без аргументов)
├── runner.py            # Оркестрация: run_steps + получение сессии
├── report.py            # Печать итоговых таблиц (сводка, статистика)
├── auth.py              # Вход через системный браузер, управление сессией
├── config.py            # Константы, логирование, коды выхода, маркер первого прогона
├── steps.py             # Каталог шагов очистки (id, лейблы, порядок)
├── chats_api.py         # Удаление через HTTP API (основной метод, быстро)
├── chats_browser.py     # Удаление через браузер UI (резервный фолбэк / --force-browser)
├── negotiations.py      # Удаление откликов-отказов
├── notify.py            # Windows toast-уведомления
├── scheduler.py         # Регистрация в Windows Task Scheduler
├── cli_cmds.py          # Команды без браузера (doctor, лог)
├── ui_selectors.py      # CSS/data-qa селекторы веб-UI hh.ru
├── hhcleaner.spec       # Конфиг PyInstaller
├── build.bat            # Обёртка сборки .exe
├── tools/               # Dev-утилиты (не входят в пакет)
│   └── inspect_dom.py   # Диагностика вёрстки chatik (см. ниже)
└── tests/               # pytest: args, dates, orchestration, predicates, scheduler, session
```

---

## Диагностика вёрстки hh.ru (`tools/inspect_dom.py`)

Браузерный путь (`chats_browser.py`) цепляется за CSS/`data-qa` chatik, а hh.ru
периодически меняет вёрстку (magritte-классы вида `intention--HASH` обфусцированы
и плавают). Когда какой-то браузерный шаг начинает находить 0 там, где API
находит больше, — вёрстка уехала. Чинить селекторы наугад нельзя, поэтому есть
инспектор:

```powershell
python tools\inspect_dom.py     # нужна сохранённая сессия (hhcleaner login)
```

Он через API находит «эталонные» чаты (архивный, старый), открывает их в браузере
и дампит: где сейчас лежит дата в карточке списка, чем помечена архивная вакансия
и собеседование, плюс прогоняет сами production-детекторы
(`_is_archived_in_current_page` / `_is_interview_in_current_page`) на живой
странице. Вывод — в консоль и `tools/inspect_dom_output.txt`. По нему правятся
`CHAT_TIME` / `ARCHIVED_VACANCY_TEXTS` / `INTERVIEW_*` в `ui_selectors.py`.

Это dev-утилита: в пакет (`pyproject.toml → py-modules`) и в .exe не входит,
запускается из исходников.

---

## Troubleshooting сборки

**PyInstaller не найден** → `pip install "pyinstaller>=6.0"` (или `pip install -e ".[dev]"`).

**`No module named 'hh_cleaner'`** → выполните `pip install -e .` в активированном venv.

**В собранном .exe не стартует браузер** → на машине нет ни Edge, ни Chrome. Установите Microsoft Edge (на Windows 10/11 он есть по умолчанию) или Google Chrome. Проверить: `dist\hhcleaner.exe doctor`.
