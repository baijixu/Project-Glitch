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
echo "  1. In one terminal:     cd brain && uv run main.py"
echo "  2. In another terminal: cd renderer && npm run dev"
echo "  3. Open https://localhost:5173, accept the certificate warning, then in"
echo "     Settings > LLM add your LLM server's endpoint and model, and select it"
echo
echo "Optional voice: Glitch speaks through any OpenAI-style text-to-speech server, and stays"
echo "text-only until you add one. To run the bundled Kokoro service:"
echo "  docker compose up -d"
echo "then open Settings > Speech Engine in the app and add an engine with the endpoint"
echo "http://localhost:8880/v1"
