@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Checking the API keys in .env against the real services...
echo.
if exist .venv\Scripts\python.exe (.venv\Scripts\python.exe scripts\check_keys.py) else (python scripts\check_keys.py)
echo.
echo Send a screenshot of this window if something is [FAIL]. Keys are not shown in full.
pause
