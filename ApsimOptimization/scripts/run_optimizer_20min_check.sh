#!/usr/bin/env bash
set -uo pipefail

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
if [[ ! -x "$APSIM_MODELS" ]]; then
  echo "APSIM_MODELS is not executable: $APSIM_MODELS" >&2
  exit 2
fi
if ! command -v timeout >/dev/null 2>&1; then
  echo "The GNU timeout command is required" >&2
  exit 2
fi

POPULATION=8
GENERATIONS=2
STAMP="$(date +%Y%m%d_%H%M%S)"
VALIDATION_DIR="outputs/multifield/optimizer_20min_validation_$STAMP"
mkdir -p "$VALIDATION_DIR"
LOG="$VALIDATION_DIR/validation.log"
OPTIMIZER_LOG="$VALIDATION_DIR/optimizer.log"

exec > >(tee "$LOG") 2>&1

echo "Optimizer integration check"
echo "Python: $($PYTHON --version 2>&1)"
echo "APSIM: $APSIM_MODELS"
echo "Budget: less than 20 minutes"
echo "Search: population=$POPULATION generations=$GENERATIONS"

echo "[1/4] Focused multi-field unit tests (limit 90 s)"
timeout --signal=TERM --kill-after=5s 90s \
  "$PYTHON" -m pytest -q -p no:cacheprovider \
  tests/test_multifield.py tests/test_multifield_sowing_decisions.py
test_status=$?
if [[ $test_status -ne 0 ]]; then
  echo "Unit tests failed or timed out: status=$test_status" >&2
  exit $test_status
fi

echo "[2/4] Diagnostic configuration validation (limit 30 s)"
timeout --signal=TERM --kill-after=5s 30s \
  "$PYTHON" run_multifield.py validate \
  --config configs/multifield_2023_n_diagnostic.yaml
config_status=$?
if [[ $config_status -ne 0 ]]; then
  echo "Configuration validation failed or timed out: status=$config_status" >&2
  exit $config_status
fi

echo "[3/4] Real APSIM + NSGA-II run (limit 16 min)"
set +e
timeout --signal=TERM --kill-after=10s 16m \
  "$PYTHON" run_multifield.py optimize \
  --config configs/multifield_2023_n_diagnostic.yaml \
  --population "$POPULATION" --generations "$GENERATIONS" \
  2>&1 | tee "$OPTIMIZER_LOG"
optimizer_status=${PIPESTATUS[0]}
if [[ $optimizer_status -ne 0 ]]; then
  echo "Optimizer failed or exceeded 16 minutes: status=$optimizer_status" >&2
  exit $optimizer_status
fi

RUN_DIR="$(sed -n 's/^Run directory: //p' "$OPTIMIZER_LOG" | tail -n 1 | tr -d '\r')"
if [[ -z "$RUN_DIR" || ! -d "$RUN_DIR" ]]; then
  echo "Could not locate the optimizer result directory: $RUN_DIR" >&2
  exit 1
fi

echo "[4/4] Independent result checks and compact archive (limit 30 s)"
timeout --signal=TERM --kill-after=5s 30s \
  "$PYTHON" scripts/validate_optimizer_smoke.py \
  --run-dir "$RUN_DIR" \
  --validation-dir "$VALIDATION_DIR" \
  --minimum-evaluated 8 \
  --expected-population "$POPULATION" \
  --expected-generations "$GENERATIONS"
check_status=$?
if [[ $check_status -ne 0 ]]; then
  echo "Result validation failed or timed out: status=$check_status" >&2
  exit $check_status
fi

echo "OPTIMIZER CHECK PASSED"
