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

echo.
echo Build complete: dist\JARVIS\JARVIS.exe
endlocal
