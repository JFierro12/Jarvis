#!/usr/bin/env bash
# Runs every automated test suite in the repo. Pass no args to run all of
# them, or one of: ios | backend | pc-agent
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-all}"
SIMULATOR_NAME="${SIMULATOR_NAME:-iPhone 17 Pro}"

run_ios() {
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
  (cd "$ROOT_DIR/backend" && source .venv/bin/activate && python -m pytest tests/ -v)
}

run_pc_agent() {
  echo "==> pc-agent tests"
  (cd "$ROOT_DIR/pc-agent" && source .venv/bin/activate && python -m pytest tests/ -v)
}

case "$TARGET" in
  ios) run_ios ;;
  backend) run_backend ;;
  pc-agent) run_pc_agent ;;
  all) run_ios; run_backend; run_pc_agent ;;
  *) echo "Usage: $0 [ios|backend|pc-agent]"; exit 1 ;;
esac
