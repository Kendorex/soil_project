#!/usr/bin/env bash
set -euo pipefail

cd /home/user/ApsimOptimization
source venv/bin/activate

python3 -m pytest tests
python3 run_optimizer.py --check-env
python3 run_optimizer.py --dry-run
python3 tools/zmq_oneshot.py --dry-run
python3 run_optimizer.py --diagnose-soil-outputs
python3 run_optimizer.py --test-soil-output-candidates --scenario s3_norm
python3 run_optimizer.py --single-run --scenario s3_norm --scheme F0_no_fertilizer \
  --population 250 --depth 30 --row-spacing 250 --ignore-cache
python3 run_optimizer.py --evaluate-technology --scheme F0_no_fertilizer \
  --population 250 --depth 30 --row-spacing 250 --ignore-cache

echo "Validation sequence completed."
echo "Long optimization runs are intentionally not started by this script."
echo "Quick NSGA-II:"
echo "python3 run_optimizer.py --config configs/optimizer_config_quick_two_cultivars_no_cold.yaml --optimize-nsga2"
echo "Full grid:"
echo "python3 run_optimizer.py --full-grid --allow-large-grid"
