#!/usr/bin/env python3
"""Inspect or run the minimal BMI adapter for multi-field optimization."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.bmi_multifield import BmiApsimOptimization


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parents[1] / "configs" / "multifield.yaml"),
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="run one complete optimization; without this flag only print the interface",
    )
    args = parser.parse_args(argv)

    model = BmiApsimOptimization()
    print(f"component_name={model.get_component_name()}")
    print(f"input_variables={model.get_input_var_names()}")
    print(f"output_variables={model.get_output_var_names()}")
    if not args.run:
        return 0

    try:
        result = model.run(args.config)
        summary = {
            "status": result.get("status"),
            "evaluated": result.get("evaluated"),
            "failed": result.get("failed"),
            "pareto_solutions": len(result.get("pareto") or []),
            "output_dir": model.get_output_directory(),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if result.get("pareto") else 3
    finally:
        model.finalize()


if __name__ == "__main__":
    raise SystemExit(main())
