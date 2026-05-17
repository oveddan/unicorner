#!/bin/bash
# Unicorner Controller — double-click launcher
# Starts the Vite dev server in controller/ and opens the app in your default browser.

set -e
cd "$(dirname "$0")"

CONTROLLER_DIR="controller"
URL="http://localhost:5173/"

if [ ! -d "$CONTROLLER_DIR" ]; then
  echo "ERROR: $CONTROLLER_DIR/ not found in $(pwd)"
  echo "Press any key to close."
  read -n 1 -s
  exit 1
fi

cd "$CONTROLLER_DIR"

# Make sure Node + npm are available
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "Node.js / npm is not installed."
  echo "Install from https://nodejs.org (LTS) and re-run this launcher."
  echo
  echo "Press any key to close."
  read -n 1 -s
  exit 1
fi

# Install deps the first time
if [ ! -d "node_modules" ]; then
  echo "First run — installing dependencies (this can take a minute)..."
  npm install
fi

echo "================================================="
echo "  Unicorner Controller — local launcher"
echo "================================================="
echo "  Folder : $(pwd)"
echo "  URL    : $URL"
echo "  Port   : 5173"
echo
echo "  Make sure TouchDesigner is open with td/main.toe so"
echo "  the controller can connect on ws://127.0.0.1:9980."
echo
echo "  Leave this Terminal window OPEN while you use the app."
echo "  Close the window (or press Ctrl+C) to stop the server."
echo "================================================="
echo

# Start Vite dev server in background, suppress noisy output to keep the terminal clean
npm run dev >/tmp/unicorner-vite.log 2>&1 &
SERVER_PID=$!

# Clean up on exit
trap 'echo; echo "Stopping dev server (PID $SERVER_PID)..."; kill $SERVER_PID 2>/dev/null; exit 0' INT TERM EXIT

# Wait for Vite to actually bind to the port before opening the browser
for i in $(seq 1 30); do
  if lsof -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if ! lsof -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Vite didn't start within 15s. Check /tmp/unicorner-vite.log for errors."
  echo "Press any key to close."
  read -n 1 -s
  exit 1
fi

echo "  Server up. Opening $URL ..."
open "$URL"

# Keep the script alive so the server keeps running
wait $SERVER_PID
