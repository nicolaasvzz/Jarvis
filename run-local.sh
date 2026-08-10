#!/usr/bin/env bash
# Run the Investment Bot locally for free (macOS / Linux).
#
#   ./run-local.sh            # live terminal dashboard (start here)
#   ./run-local.sh api        # HTTP API on :8000, for the web dashboard
#   ./run-local.sh once       # single cycle, then exit (for cron)
#   ./run-local.sh backtest
#
# First run creates a .venv and installs dependencies; later runs reuse it.

set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-terminal}"
PORT="${2:-8000}"
CONFIG="config.local.yaml"
VENV_PY=".venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "=== First run: creating virtual environment ==="
    python3 -m venv .venv
    echo "=== Installing dependencies (one time, ~1 min) ==="
    "$VENV_PY" -m pip install --quiet --upgrade pip
    "$VENV_PY" -m pip install --quiet -r requirements.txt
    echo "Done."
fi

case "$MODE" in
    terminal)
        echo "=== Paper trading (Ctrl-C to stop; state is saved) ==="
        exec "$VENV_PY" -m investment_bot trade -c "$CONFIG"
        ;;
    once)
        exec "$VENV_PY" -m investment_bot trade --once -c "$CONFIG"
        ;;
    backtest)
        exec "$VENV_PY" -m investment_bot backtest -c "$CONFIG" --html report.html
        ;;
    api)
        echo "=== API on http://localhost:$PORT ==="
        echo "For phone access, run in a second terminal:"
        echo "  cloudflared tunnel --url http://localhost:$PORT"
        exec "$VENV_PY" -m investment_bot serve -c "$CONFIG" --port "$PORT"
        ;;
    *)
        echo "Unknown mode: $MODE (expected terminal, api, once, or backtest)" >&2
        exit 1
        ;;
esac
