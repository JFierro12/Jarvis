#!/usr/bin/env bash
# Runs every automated test suite in the repo. Pass no args to run all of
# them, or one of: ios | backend | pc-agent | training | web
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-all}"
SIMULATOR_NAME="${SIMULATOR_NAME:-iPhone 17 Pro}"

# Resolve a venv's interpreter on either layout. This script previously did
# `source .venv/bin/activate`, which does not exist on Windows (venvs there put
# the interpreter in Scripts/), so every target failed on the only machine this
# project is currently developed on. Calling the interpreter directly also
# avoids needing `activate` at all.
venv_python() {
  local dir="$1"
  if [ -x "$dir/.venv/Scripts/python.exe" ]; then
    echo "$dir/.venv/Scripts/python.exe"
  elif [ -x "$dir/.venv/bin/python" ]; then
    echo "$dir/.venv/bin/python"
  else
    echo ""
  fi
}

require_python() {
  local dir="$1" name="$2" python
  python="$(venv_python "$dir")"
  if [ -z "$python" ]; then
    echo "    no virtualenv found for $name at $dir/.venv" >&2
    echo "    create one with:  python -m venv $dir/.venv && $dir/.venv/*/python -m pip install -e '$dir[dev]'" >&2
    return 1
  fi
  echo "$python"
}

run_ios() {
  if ! command -v xcodebuild >/dev/null 2>&1; then
    echo "==> iOS tests skipped (no xcodebuild; macOS with Xcode required)"
    return 0
  fi
  echo "==> JarvisKit unit tests"
  (cd "$ROOT_DIR/ios/JarvisKit" && xcodebuild test -scheme JarvisKit \
    -destination "platform=iOS Simulator,name=$SIMULATOR_NAME")

  echo "==> Jarvis app tests (JarvisTests)"
  (cd "$ROOT_DIR/ios/Jarvis" && xcodebuild test -project Jarvis.xcodeproj -scheme Jarvis \
    -destination "platform=iOS Simulator,name=$SIMULATOR_NAME" -only-testing:JarvisTests)

  echo "==> Jarvis UI tests (may require an interactive Xcode session — see docs/ROADMAP.md)"
  (cd "$ROOT_DIR/ios/Jarvis" && xcodebuild test -project Jarvis.xcodeproj -scheme Jarvis \
    -destination "platform=iOS Simulator,name=$SIMULATOR_NAME" -only-testing:JarvisUITests) || \
    echo "    (UI tests failed to launch — known headless-environment limitation, see docs/ROADMAP.md)"
}

run_backend() {
  echo "==> Backend tests"
  local python
  python="$(require_python "$ROOT_DIR/backend" backend)"
  (cd "$ROOT_DIR/backend" && "$python" -m pytest tests/ -v)
}

run_pc_agent() {
  echo "==> pc-agent tests"
  local python
  python="$(venv_python "$ROOT_DIR/pc-agent")"
  if [ -z "$python" ]; then
    # pc-agent's deps are a subset of the backend's; reuse that interpreter
    # rather than failing, so the suite is runnable without a second venv.
    python="$(require_python "$ROOT_DIR/backend" backend)"
    echo "    (no pc-agent venv; using the backend interpreter)"
  fi
  # The Quartz-backed gesture tests skip themselves off macOS — app/main.py
  # only mounts /gesture/stream on darwin, so there is nothing to exercise.
  (cd "$ROOT_DIR/pc-agent" && "$python" -m pytest tests/ -v)
}

run_web() {
  # Voice-path state machines (assets/voice.js). These cover the parts of the
  # browser voice stack that are pure logic over timestamps and amplitudes --
  # self-transcription suppression, VAD, stale-turn rejection and spoken
  # confirmation -- using synthesised audio, so they need no microphone and no
  # browser. Physical microphone verification is separate and is listed in
  # docs/OVERNIGHT_LOG.md.
  if ! command -v node >/dev/null 2>&1; then
    echo "==> Web/voice tests skipped (node not found)"
    return 0
  fi
  echo "==> Web voice-path tests"
  (cd "$ROOT_DIR/backend/app/static/assets" && node --test)
}

run_training() {
  echo "==> Training dataset-contract tests"
  # Deliberately stdlib-only, so they run on the backend interpreter without
  # training/.venv's torch/transformers stack being installed.
  local python
  python="$(venv_python "$ROOT_DIR/training")"
  if [ -z "$python" ]; then
    python="$(require_python "$ROOT_DIR/backend" backend)"
    echo "    (no training venv; using the backend interpreter — contract tests need only stdlib)"
  fi
  (cd "$ROOT_DIR/training" && "$python" -m pytest tests/ -v)
}

case "$TARGET" in
  ios) run_ios ;;
  backend) run_backend ;;
  pc-agent) run_pc_agent ;;
  training) run_training ;;
  web) run_web ;;
  all) run_ios; run_backend; run_pc_agent; run_training; run_web ;;
  *) echo "Usage: $0 [ios|backend|pc-agent|training|web]"; exit 1 ;;
esac
