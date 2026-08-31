@echo off
REM Run this ON the Windows laptop. PyInstaller cannot cross-compile, so this
REM cannot be run from macOS/Linux to produce a Windows .exe.
setlocal

cd /d "%~dp0"

if not exist venv (
    python -m venv venv
)
call venv\Scripts\activate.bat

pip install --upgrade pip
pip install -r ..\backend\requirements.txt
pip install -r requirements.txt

pyinstaller --noconfirm jarvis.spec

REM PyInstaller's COLLECT step rebuilds dist\JARVIS from scratch each time,
REM so a .env placed there by hand gets wiped on every rebuild. Carry the
REM one already configured for local dev over automatically instead.
if exist ..\backend\.env (
    copy /y ..\backend\.env dist\JARVIS\.env >nul
    echo Copied backend\.env into dist\JARVIS\.env
) else (
    echo NOTE: no backend\.env found — dist\JARVIS will run in mock mode until you add one.
)

echo.
echo Build complete: dist\JARVIS\JARVIS.exe
endlocal
