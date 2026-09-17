@echo off
REM Starts Brain and the Renderer dev server, each in its own window.
REM Closing one window only stops that piece -- closing this launcher
REM window itself does nothing since Brain/Vite run in their own windows.

start "Glitch - Brain" cmd /k "cd /d "%~dp0brain" && uv run main.py"
start "Glitch - Renderer" cmd /k "cd /d "%~dp0renderer" && npm run dev"
