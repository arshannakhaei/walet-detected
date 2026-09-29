@echo off
REM ChainTrace - Windows launcher: first run creates the environment, later runs just start.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.11+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv || (pause & exit /b 1)
)
call .venv\Scripts\activate.bat
REM Reinstall whenever requirements.txt differs from the copy made at the last install.
fc /b requirements.txt .venv\requirements.installed >nul 2>nul || (
    echo Installing dependencies, this takes a few minutes the first time...
    python -m pip install --upgrade pip >nul
    pip install -r requirements.txt || (pause & exit /b 1)
    copy /y requirements.txt .venv\requirements.installed >nul
)
if not exist .env copy .env.example .env >nul
start "" http://127.0.0.1:8000
python run.py
pause
