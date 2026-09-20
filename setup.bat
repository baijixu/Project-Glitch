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
echo Optional voice: Glitch speaks through any OpenAI-style text-to-speech server, and stays
echo text-only until you add one. To run the bundled Kokoro service:
echo   docker compose up -d
echo then open Settings, Speech Engine in the app and add an engine with the endpoint
echo http://localhost:8880/v1
