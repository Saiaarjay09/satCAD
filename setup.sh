#!/bin/sh
# One-time setup:   ./setup.sh              install everything and run the self-tests
#                   ./setup.sh --register   ...and also register the connector with Claude Code
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"

"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null || {
  echo "Python 3.10 or newer is required (found: $("$PY" --version 2>&1)). Install it from python.org and retry."; exit 1; }

echo "1/3  Creating a private Python environment in .venv/ ..."
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip

echo "2/3  Installing the CAD engine and connector libraries (this can take a minute) ..."
.venv/bin/python -m pip install --quiet -r requirements.txt

echo "3/3  Running the self-tests ..."
.venv/bin/python -m pytest -q
chmod +x run_server.sh

SERVER="$PWD/run_server.sh"
echo
echo "Setup complete. The connector command is:  $SERVER"

if [ "$1" = "--register" ]; then
  if command -v claude >/dev/null 2>&1; then
    claude mcp add --scope user satellite-cad -- "$SERVER"
    echo "Registered 'satellite-cad' with Claude Code. Restart Claude Code, then ask it to design a satellite."
  else
    echo "The 'claude' command was not found, so I could not register automatically."
    echo "Add this entry under \"mcpServers\" in ~/.claude.json, then restart Claude Code:"
    printf '  "satellite-cad": { "command": "%s", "args": [] }\n' "$SERVER"
  fi
else
  echo "To let Claude use it, run:   ./setup.sh --register"
fi
