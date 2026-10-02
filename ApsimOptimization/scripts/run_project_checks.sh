#!/usr/bin/env bash
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
else
  PYTHON=python3
fi

STAMP="$(date -u +%Y%m%d_%H%M%S)"
OUTPUT="outputs/project_checks/$STAMP"
mkdir -p "$OUTPUT"
export PYTHONDONTWRITEBYTECODE=1

set +e
"$PYTHON" -m pytest -q -p no:cacheprovider \
  tests/test_multifield.py \
  tests/test_multifield_sowing_decisions.py \
  tests/test_historical_readiness.py \
  tests/test_multifield_quality.py \
  tests/test_historical_bias.py \
  2>&1 | tee "$OUTPUT/pytest.log"
PYTEST_STATUS=${PIPESTATUS[0]}

"$PYTHON" run_multifield.py validate \
  --config configs/multifield_2023_n_diagnostic.yaml \
  2>&1 | tee "$OUTPUT/configuration.log"
CONFIG_STATUS=${PIPESTATUS[0]}
set -e

{
  echo "pytest_status=$PYTEST_STATUS"
  echo "configuration_status=$CONFIG_STATUS"
  if [[ $PYTEST_STATUS -eq 0 && $CONFIG_STATUS -eq 0 ]]; then
    echo "overall_status=passed"
  else
    echo "overall_status=failed"
  fi
} | tee "$OUTPUT/status.txt"

echo "RESULT DIRECTORY: $OUTPUT"
if [[ $PYTEST_STATUS -ne 0 || $CONFIG_STATUS -ne 0 ]]; then
  exit 1
fi
