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

STAMP="$(date +%Y%m%d_%H%M%S)"
VALIDATION_DIR="outputs/multifield/ubuntu_validation_$STAMP"
mkdir -p "$VALIDATION_DIR"
{
  echo "date_utc=$(date -u --iso-8601=seconds)"
  echo "project_root=$PROJECT_ROOT"
  echo "python=$($PYTHON --version 2>&1)"
  echo "apsim_models=$APSIM_MODELS"
  echo "apsim_models_sha256=$(sha256sum "$APSIM_MODELS" | cut -d' ' -f1)"
} > "$VALIDATION_DIR/environment.txt"

exec > >(tee "$VALIDATION_DIR/validation.log") 2>&1

echo "[1/15] Python tests"
"$PYTHON" -m pytest -q

echo "[2/15] Production configuration inventory (missing domain values are expected)"
"$PYTHON" run_optimizer.py inspect

echo "[3/15] Diagnostic configuration validation"
"$PYTHON" run_optimizer.py validate --config configs/multifield_2023_n_diagnostic.yaml

echo "[4/15] Calibrated baseline with explicit perennial transition proxy"
"$PYTHON" run_optimizer.py baseline --config configs/multifield_2023_n_diagnostic.yaml

echo "[5/15] Immutable quality checkpoint and plot-ready history"
"$PYTHON" scripts/record_multifield_quality.py \
  --label "ubuntu_${STAMP}" \
  --notes "Automated Ubuntu baseline validation"

echo "[6/15] Real APSIM NSGA-II smoke with mandatory historical reference"
"$PYTHON" run_optimizer.py optimize \
  --config configs/multifield_2023_n_diagnostic.yaml \
  --population 4 --generations 1

echo "[7/15] Field N-response diagnostic at 0/30/60"
"$PYTHON" scripts/diagnose_field_n_response.py --run --doses 0 30 60

echo "[8/15] Oats companion-competition diagnostic"
"$PYTHON" scripts/diagnose_oats_competition.py --run

echo "[9/15] Winter-rye N timing diagnostic"
"$PYTHON" scripts/diagnose_rye_n_timing.py --run

echo "[10/15] Perennial-to-winter transition diagnostic"
"$PYTHON" scripts/diagnose_forage_transition.py --run

echo "[11/15] 2019 spring-establishment water sensitivity"
"$PYTHON" scripts/diagnose_2019_establishment_water.py --run

echo "[12/15] Soil and root-access sensitivity using model endpoints"
"$PYTHON" scripts/diagnose_soil_root_access.py --run

echo "[13/15] Reproducible quality plots"
"$PYTHON" scripts/plot_multifield_quality.py

echo "[14/15] Pareto plots"
"$PYTHON" scripts/plot_multifield_pareto.py

echo "[15/15] Archive and integrity analysis"
ARCHIVE="$PROJECT_ROOT/multifield_check_$STAMP.tar.gz"
"$PYTHON" scripts/collect_multifield_results.py \
  --output "$ARCHIVE" --extra "$VALIDATION_DIR"
"$PYTHON" scripts/analyze_multifield_archive.py "$ARCHIVE" --summary-only

echo "Validation completed. Send this file for analysis:"
echo "$ARCHIVE"
