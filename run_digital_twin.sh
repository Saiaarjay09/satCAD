#!/bin/sh
# Digital Twin demo for macOS / Linux: satSTK (orbit simulator, broadcasting telemetry) and satCAD (3D model in Digital Twin Listener Mode) side by side.
#
#   ./run_digital_twin.sh              start satCAD, then satSTK (found next to this folder, or in $SATSTK_DIR)
#   ./run_digital_twin.sh --demo       no satSTK needed: stream made-up telemetry from tools/twin_demo_sender.py instead
#   SATSTK_DIR=/path/to/STK ./run_digital_twin.sh
#
# Close either window, or press Ctrl+C here, and both programs stop. See README.md ("Live Digital Twin with satSTK") and docs/TELEMETRY_PROTOCOL.md.
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python
[ -x "$PY" ] || { echo "Run ./setup.sh first (it creates the .venv this launcher uses)."; exit 1; }
MODE=satstk
[ "$1" = "--demo" ] && MODE=demo

# On macOS, put satCAD on the left half of the screen when we can read the screen size (best effort: silently skipped otherwise).
GEOM=""
if command -v osascript >/dev/null 2>&1; then
  B=$(osascript -e 'tell application "Finder" to get bounds of window of desktop' 2>/dev/null | tr -d ' ')
  W=$(echo "$B" | cut -d, -f3); H=$(echo "$B" | cut -d, -f4)
  if [ -n "$W" ] && [ -n "$H" ]; then GEOM="--geometry 0,40,$((W / 2)),$((H - 80))"; fi
fi

PIDS=""
cleanup() { for p in $PIDS; do kill "$p" 2>/dev/null; done; }
trap cleanup EXIT INT TERM

echo "Starting satCAD in Digital Twin Listener Mode (listening on 127.0.0.1:9876) ..."
# shellcheck disable=SC2086
"$PY" main.py --digital-twin $GEOM &
SATCAD_PID=$!
PIDS="$SATCAD_PID"
sleep 3

if [ "$MODE" = "demo" ]; then
  echo "Streaming demo telemetry (Ctrl+C to stop) ..."
  "$PY" tools/twin_demo_sender.py &
  PIDS="$PIDS $!"
else
  SATSTK_DIR=${SATSTK_DIR:-../STK}
  [ -d "$SATSTK_DIR" ] || SATSTK_DIR=../satSTK
  if [ ! -d "$SATSTK_DIR" ]; then
    echo "Couldn't find satSTK. Set SATSTK_DIR to its folder (for example: SATSTK_DIR=\$HOME/Developer/STK ./run_digital_twin.sh),"
    echo "or run ./run_digital_twin.sh --demo to try the twin with made-up telemetry."
    exit 1
  fi
  echo "Starting satSTK from $SATSTK_DIR (broadcasting to 127.0.0.1:9876) ..."
  # shellcheck disable=SC2086
  (cd "$SATSTK_DIR" && exec ./run.sh ${SATSTK_ARGS:---broadcast}) &
  PIDS="$PIDS $!"
fi

if [ "$MODE" = "demo" ]; then
  echo "Demo telemetry is flowing: the satCAD HUD should turn LIVE. Close the satCAD window to stop everything."
else
  echo "Both are running. In satSTK press Play; in satCAD the HUD turns LIVE. Close the satCAD window to stop everything."
fi
wait "$SATCAD_PID"
