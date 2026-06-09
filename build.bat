@echo off
REM ============================================================================
REM build.bat — сборка hhcleaner.exe из исходников через PyInstaller.
REM
REM Использование (из корня репозитория):
REM     build.bat            Полная сборка (ставит зависимости + собирает)
REM     build.bat --quick    Только пересборка (без переустановки зависимостей)
REM
REM Результат:
REM     dist\hhcleaner.exe   (один файл, ~50 МБ; браузер НЕ встроен — берётся
REM                           системный Edge/Chrome при запуске)
REM
REM Требуется: Python 3.9+ в PATH (venv опционально).
REM ============================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0" || exit /b 1

set "QUICK=0"
if "%~1"=="--quick" set QUICK=1

echo [*] Building hhcleaner.exe...

if "!QUICK!"=="0" (
    if exist build rmdir /s /q build >nul 2>&1
    if exist dist rmdir /s /q dist >nul 2>&1
    echo [*] Installing dependencies...
    pip install -e ".[dev]" >nul 2>&1
    if errorlevel 1 (
        echo [!] Failed to install dependencies
        exit /b 1
    )
)

echo [*] Running PyInstaller...
python -m PyInstaller hhcleaner.spec --distpath dist --workpath build --noconfirm
if errorlevel 1 (
    echo [!] PyInstaller build failed
    exit /b 1
)

if not exist "dist\hhcleaner.exe" (
    echo [!] dist\hhcleaner.exe not found
    exit /b 1
)

echo [*] Smoke-test: dist\hhcleaner.exe --version
dist\hhcleaner.exe --version >nul 2>&1
if errorlevel 1 (
    echo [!] .exe is not working
    exit /b 1
)

echo.
echo [+] Build successful: dist\hhcleaner.exe
echo     Next: dist\hhcleaner.exe --self-check  ^|  upload to GitHub Releases
exit /b 0
