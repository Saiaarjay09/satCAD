#!/bin/sh
# Starts the satellite-cad connector (an MCP server that talks to Claude over stdin/stdout).
# Claude launches this for you once it is registered; you normally never run it by hand.
DIR="$(cd "$(dirname "$0")" && pwd)"
export SATCAD_OUT="${SATCAD_OUT:-$DIR/cad_output}"   # designs, pictures and exports are saved here
cd "$DIR" || exit 1
exec "$DIR/.venv/bin/python" -m satcad.server
