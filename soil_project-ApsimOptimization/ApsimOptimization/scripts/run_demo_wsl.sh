#!/usr/bin/env bash
set -euo pipefail

cd /home/user/ApsimOptimization
source venv/bin/activate

CONFIG=configs/optimizer_config_demo.yaml

python3 -m pytest tests
python3 run_optimizer.py --config "$CONFIG" --check-env
python3 run_optimizer.py --config "$CONFIG" --dry-run
python3 run_optimizer.py \
  --config "$CONFIG" \
  --single-run \
  --scenario s3_norm \
  --scheme F12_NH4NO3N_150_sowing \
  --cultivar Irgina \
  --population 450 \
  --depth 60 \
  --row-spacing 150 \
  --ignore-cache
python3 run_optimizer.py --config "$CONFIG" --optimize-nsga2 --ignore-cache

echo "Demo completed. Open outputs/demo/nsga2_evaluations.csv, pareto_nsga2.csv and best_solution.json."
