#!/usr/bin/env bash
# Sets up local dev environments for backend and pc-agent. iOS has no
# bootstrap step beyond opening the Xcode project (see docs/SETUP_IOS.md) —
# Xcode resolves both the local JarvisKit package and the remote Meta DAT
# SDK package automatically.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

setup_python_project() {
  local dir="$1"
  echo "==> Setting up $dir"
  (
    cd "$ROOT_DIR/$dir"
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --quiet --upgrade pip
    pip install --quiet -e ".[dev]"
    if [ -f .env.example ] && [ ! -f .env ]; then
      cp .env.example .env
      echo "    Created $dir/.env from .env.example — edit it before real use."
    fi
  )
}

setup_python_project backend
setup_python_project pc-agent

echo "==> Done. Next steps:"
echo "    - iOS: open ios/Jarvis/Jarvis.xcodeproj"
echo "    - Backend: cd backend && source .venv/bin/activate && uvicorn app.main:app --reload"
echo "    - pc-agent: cd pc-agent && source .venv/bin/activate && uvicorn app.main:app --port 8765"
