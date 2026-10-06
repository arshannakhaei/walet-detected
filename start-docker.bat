@echo off
REM ChainTrace in Docker (needs Docker Desktop running). Stop with: docker compose down
cd /d "%~dp0"
where docker >nul 2>nul || (echo Docker Desktop is not installed: https://www.docker.com/products/docker-desktop/ & pause & exit /b 1)
docker info >nul 2>nul || (echo Docker Desktop is installed but not running. Start it and try again. & pause & exit /b 1)
if not exist .env copy .env.example .env >nul
docker compose up -d --build || (
  echo.
  echo Build failed. If the error mentions files.pythonhosted.org or "name resolution",
  echo Python packages cannot be downloaded from this network: turn on a VPN or change DNS
  echo for the build, or set PIP_INDEX_URL to a PyPI mirror in .env. See README, Docker section.
  pause & exit /b 1
)
echo.
echo ChainTrace is starting at http://127.0.0.1:8765
timeout /t 5 >nul
start "" http://127.0.0.1:8765
pause
