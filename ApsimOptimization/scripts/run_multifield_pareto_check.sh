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
    echo "APSIM Models not found. Run with APSIM_MODELS=/absolute/path/to/Models" >&2
    exit 2
  fi
fi
if [[ ! -x "$APSIM_MODELS" ]]; then
  echo "APSIM_MODELS is not executable: $APSIM_MODELS" >&2
  exit 2
fi

POPULATION="${POPULATION:-12}"
GENERATIONS="${GENERATIONS:-3}"
if ! [[ "$POPULATION" =~ ^[0-9]+$ ]] || (( POPULATION < 4 )); then
  echo "POPULATION must be an integer >= 4" >&2
  exit 2
fi
if ! [[ "$GENERATIONS" =~ ^[0-9]+$ ]] || (( GENERATIONS < 1 )); then
  echo "GENERATIONS must be an integer >= 1" >&2
  exit 2
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
VALIDATION_DIR="outputs/multifield/pareto_validation_$STAMP"
mkdir -p "$VALIDATION_DIR"
{
  echo "date_utc=$(date -u --iso-8601=seconds)"
  echo "project_root=$PROJECT_ROOT"
  echo "python=$($PYTHON --version 2>&1)"
  echo "apsim_models=$APSIM_MODELS"
  echo "apsim_models_sha256=$(sha256sum "$APSIM_MODELS" | cut -d' ' -f1)"
  echo "population=$POPULATION"
  echo "generations=$GENERATIONS"
} > "$VALIDATION_DIR/environment.txt"

exec > >(tee "$VALIDATION_DIR/validation.log") 2>&1

echo "[1/5] Python tests"
"$PYTHON" -m pytest -q

echo "[2/5] Configuration checks"
"$PYTHON" run_optimizer.py inspect
"$PYTHON" run_optimizer.py validate --config configs/multifield_2023_n_diagnostic.yaml

echo "[3/5] APSIM NSGA-II diagnostic: population=$POPULATION generations=$GENERATIONS"
"$PYTHON" run_optimizer.py optimize \
  --config configs/multifield_2023_n_diagnostic.yaml \
  --population "$POPULATION" --generations "$GENERATIONS"

echo "[4/5] Pareto plots"
"$PYTHON" scripts/plot_multifield_pareto.py

echo "[5/5] Archive and integrity analysis"
ARCHIVE="$PROJECT_ROOT/multifield_pareto_$STAMP.tar.gz"
"$PYTHON" scripts/collect_multifield_results.py \
  --output "$ARCHIVE" --extra "$VALIDATION_DIR"
"$PYTHON" scripts/analyze_multifield_archive.py "$ARCHIVE" --summary-only

echo "Pareto check completed. Send this file for analysis:"
echo "$ARCHIVE"
