#!/usr/bin/env bash
# Lints both Python projects with ruff. iOS has no linter configured in this
# pass (SwiftLint was deliberately not added without justification — see
# docs/ROADMAP.md if you want to add one).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for dir in backend pc-agent; do
  echo "==> Linting $dir"
  (cd "$ROOT_DIR/$dir" && source .venv/bin/activate && ruff check .)
done
