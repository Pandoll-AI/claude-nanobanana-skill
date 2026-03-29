#!/usr/bin/env bash
# Gemini Image Gen — CDP 모드 Chrome 실행기
# Usage: ./launch_chrome.sh [--port 9222] [--profile /tmp/gemini-cdp]

set -euo pipefail

PORT="${GEMINI_CDP_PORT:-9222}"
PROFILE_DIR="${GEMINI_CDP_PROFILE:-/tmp/gemini-cdp-profile}"

# 인자 파싱
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --profile) PROFILE_DIR="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# 이미 포트 사용 중인지 확인
if lsof -i ":$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "ALREADY_RUNNING port=$PORT"
  exit 0
fi

# OS별 Chrome 경로 탐색
find_chrome() {
  local OS
  OS="$(uname -s)"

  case "$OS" in
    Darwin)
      local candidates=(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        "/Applications/Chromium.app/Contents/MacOS/Chromium"
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"
      )
      ;;
    Linux)
      local candidates=(
        "$(command -v google-chrome 2>/dev/null || true)"
        "$(command -v google-chrome-stable 2>/dev/null || true)"
        "$(command -v chromium-browser 2>/dev/null || true)"
        "$(command -v chromium 2>/dev/null || true)"
      )
      ;;
    MINGW*|CYGWIN*|MSYS*)
      local candidates=(
        "/c/Program Files/Google/Chrome/Application/chrome.exe"
        "/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"
      )
      ;;
    *)
      echo "ERROR: Unsupported OS: $OS" >&2
      exit 1
      ;;
  esac

  for c in "${candidates[@]}"; do
    if [[ -n "$c" && -x "$c" ]]; then
      echo "$c"
      return 0
    fi
  done

  echo "ERROR: Chrome not found. Install Google Chrome and retry." >&2
  exit 1
}

CHROME="$(find_chrome)"
mkdir -p "$PROFILE_DIR"

echo "Launching Chrome..."
echo "  Binary : $CHROME"
echo "  Profile: $PROFILE_DIR"
echo "  CDP port: $PORT"

# Chrome를 백그라운드로 실행
"$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --disable-sync \
  "https://gemini.google.com/app" \
  &

CHROME_PID=$!
echo "CHROME_PID=$CHROME_PID"

# CDP 포트 준비 대기 (최대 10초)
for i in $(seq 1 20); do
  if curl -s "http://localhost:$PORT/json/version" >/dev/null 2>&1; then
    echo "READY port=$PORT pid=$CHROME_PID"
    exit 0
  fi
  sleep 0.5
done

echo "ERROR: Chrome CDP did not become ready within 10 seconds." >&2
exit 1
