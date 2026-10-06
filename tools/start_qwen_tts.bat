@echo off
rem Glitch's Qwen3-TTS speech server (qwen_tts_server.py), kept running: if it ever
rem stops, it starts again 10 seconds later. Sits next to the server in its install
rem folder; Windows runs it at startup (see qwen_tts_server.py). Output goes to qwen_tts.log.
cd /d "%~dp0"
set HF_HOME=%~dp0hf-cache
set PYTHONUNBUFFERED=1
:run
echo [%date% %time%] starting >> qwen_tts.log
qwen-tts-env\Scripts\python qwen_tts_server.py >> qwen_tts.log 2>&1
rem ping, not timeout: timeout needs a console, and a startup task has none
ping -n 11 127.0.0.1 >nul
goto run
