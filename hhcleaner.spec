# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec для hhcleaner. Собирает один самодостаточный hhcleaner.exe.

Запуск:
    pyinstaller hhcleaner.spec
Результат:
    dist/hhcleaner.exe   (один файл, ~30–50 МБ)

Важно:
  - Браузер НЕ встраивается: вход открывается в системном Edge/Chrome через
    Playwright channel (см. auth.launch_context). На Windows 10/11 Edge есть
    всегда, поэтому .exe лёгкий и первый запуск без скачивания ~150 МБ Chromium.
  - В .exe кладём node-драйвер Playwright (collect_all) — он нужен, чтобы рулить
    системным браузером. Сам профиль и cookies лежат в ~/.hhcleaner (не в .exe).
  - console=True: при двойном клике открывается консоль-визард (hh_cleaner._wizard).
"""
from PyInstaller.utils.hooks import collect_all

# Playwright тащит за собой node-драйвер и пакетные данные, которые PyInstaller
# не находит статически — собираем явно.
pw_datas, pw_binaries, pw_hiddenimports = collect_all("playwright")

a = Analysis(
    ["hh_cleaner.py"],
    pathex=[],
    binaries=pw_binaries,
    datas=pw_datas,
    hiddenimports=pw_hiddenimports + ["rich", "requests"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "pylint"],  # GUI/dev-инструменты в рантайме не нужны
    noarchive=False,
)

pyz = PYZ(a.pure)

# Один файл: бинари и данные включаются прямо в EXE, COLLECT не используется.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="hhcleaner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
