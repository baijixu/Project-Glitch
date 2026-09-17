@echo off
setlocal enabledelayedexpansion

where uv >nul 2>nul
if errorlevel 1 (
    echo uv is required but was not found on PATH.
    echo Install it: https://docs.astral.sh/uv/getting-started/installation/
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo npm is required but was not found on PATH. Install Node.js: https://nodejs.org
    exit /b 1
)

echo Setting up brain...
pushd brain
call uv sync
if errorlevel 1 exit /b 1
popd

echo Setting up renderer shell...
pushd renderer\shell
call uv sync
if errorlevel 1 exit /b 1
popd

echo Setting up renderer...
pushd renderer
call npm install
if errorlevel 1 exit /b 1
popd

if not exist config.yaml (
    copy config.example.yaml config.yaml >nul
    echo Created config.yaml from config.example.yaml -- edit it with your real settings.
)

if not exist renderer\.env (
    copy renderer\.env.example renderer\.env >nul
    echo Created renderer\.env from renderer\.env.example.
)

echo.
echo Setup complete. Next steps:
echo   1. Edit config.yaml with your LLM endpoint.
echo   2. cd renderer ^&^& npm run dev   (or run renderer\shell\launch.py once the dev server is up)
echo   3. cd brain ^&^& uv run main.py
echo.
echo Optional: local Kokoro (brain\voice\tts.py's KokoroTTS) works with no extra setup and is
echo always available as the "Default" speech engine in Glitch's settings panel. To run Kokoro
echo in Docker instead (docker-compose.yml) and add it there as a saved engine:
echo   docker compose up -d
