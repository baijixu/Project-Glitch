#!/usr/bin/env bash
# Setup for macOS/Linux (SPEC.md section 7 -- never a .ps1, this repo has none).
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required but was not found on PATH."
    echo "Install it: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required but was not found on PATH. Install Node.js: https://nodejs.org"
    exit 1
fi

echo "Setting up brain..."
(cd brain && uv sync)

echo "Setting up renderer shell..."
(cd renderer/shell && uv sync)

if [ "$(uname)" = "Linux" ]; then
    echo
    echo "Linux note: pywebview needs the system WebKitGTK libraries --"
    echo "pip can't install them -- on Debian/Ubuntu:"
    echo "    sudo apt install python3-gi gir1.2-webkit2-4.1"
    echo "See SPEC.md section 7 for other distros."
    echo
fi

echo "Setting up renderer..."
(cd renderer && npm install)

if [ ! -f config.yaml ]; then
    cp config.example.yaml config.yaml
    echo "Created config.yaml from config.example.yaml -- edit it with your real settings."
fi

if [ ! -f renderer/.env ]; then
    cp renderer/.env.example renderer/.env
    echo "Created renderer/.env from renderer/.env.example."
fi

echo
echo "Setup complete. Next steps:"
echo "  1. Edit config.yaml with your LLM endpoint."
echo "  2. cd renderer && npm run dev   (or run renderer/shell/launch.py once the dev server is up)"
echo "  3. cd brain && uv run main.py"
echo
echo "Optional: local Kokoro (brain/voice/tts.py's KokoroTTS) works with no extra setup and is"
echo "always available as the \"Default\" speech engine in Glitch's settings panel. To run Kokoro"
echo "in Docker instead (docker-compose.yml) and add it there as a saved engine:"
echo "  docker compose up -d"
