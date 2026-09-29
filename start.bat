@echo off
REM ChainTrace - Windows launcher: first run creates the environment, later runs just start.
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.11+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || (echo Python 3.11 or newer is required. & python --version & pause & exit /b 1)
if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv || (pause & exit /b 1)
)
call .venv\Scripts\activate.bat
REM Reinstall whenever requirements.txt differs from the copy made at the last install.
fc /b requirements.txt .venv\requirements.installed >nul 2>nul || (
    echo Installing dependencies, this takes a few minutes the first time...
    python -m pip install --upgrade pip >nul
    pip install -r requirements.txt || (echo. & echo Installation failed. Run doctor.bat and send its output. & pause & exit /b 1)
    copy /y requirements.txt .venv\requirements.installed >nul
)
if not exist .env copy .env.example .env >nul
REM run.py picks a usable port and opens the browser once the server answers.
python run.py
echo.
echo If the page did not open, run doctor.bat and send its output.
pause
