#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON="$PYTHON_BIN"
elif [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
else
  PYTHON=python3
fi

if [[ -z "${APSIM_MODELS:-}" ]]; then
  DEFAULT_MODELS="$HOME/ApsimX/bin/Release/net8.0/Models"
  if [[ -x "$DEFAULT_MODELS" ]]; then
    export APSIM_MODELS="$DEFAULT_MODELS"
  elif command -v Models >/dev/null 2>&1; then
    export APSIM_MODELS="$(command -v Models)"
  else
    echo "APSIM Models not found. Set APSIM_MODELS=/absolute/path/to/Models" >&2
    exit 2
  fi
fi

echo "[1/2] Focused Python tests"
"$PYTHON" -m pytest -q tests/test_multifield.py

echo "[2/2] Two APSIM runs: static control and observed-N historical replay"
"$PYTHON" scripts/evaluate_historical_replay.py --run
