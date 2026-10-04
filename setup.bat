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
echo   1. Run start-glitch.bat (starts the Brain and the Renderer)
echo   2. Open https://localhost:5173 and accept the certificate warning
echo   3. In Settings, LLM, add your LLM server's endpoint and model, and select it
echo.
echo Optional voice: Glitch speaks through any OpenAI-style text-to-speech server, and stays
echo text-only until you add one. To run the bundled Kokoro service:
echo   docker compose up -d
echo then open Settings, Speech Engine in the app and add an engine with the endpoint
echo http://localhost:8880/v1
