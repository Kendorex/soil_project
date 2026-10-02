#!/usr/bin/env python3
"""Build and audit the traceable calibrated multi-field APSIMX model."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.model_lineage import build_calibrated_model, sha256


DEFAULT_EXTERNAL_SOURCE = (
    ROOT.parent / "more_calibration" / "full_field2019_2023_calibrated_no_rye.apsimx"
)
SNAPSHOT = ROOT / "simulation/source/full_field2019_2023_calibrated_no_rye_user_20260908.apsimx"
OUTPUT = ROOT / "simulation/calibrated/full_field2019_2023_calibrated_v2.apsimx"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_EXTERNAL_SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.source.is_file():
        raise SystemExit(f"Source model not found: {args.source}")
    if args.output.exists() and not args.force:
        raise SystemExit("Output already exists; use --force for a deterministic rebuild")

    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    if SNAPSHOT.exists() and sha256(SNAPSHOT) != sha256(args.source) and not args.force:
        raise SystemExit("Existing source snapshot differs; use --force after reviewing the new source")
    if not SNAPSHOT.exists() or args.force:
        shutil.copyfile(args.source, SNAPSHOT)
    if sha256(SNAPSHOT) != sha256(args.source):
        raise SystemExit("Source snapshot hash differs after copy")

    provenance = build_calibrated_model(
        source_model=SNAPSHOT,
        destination=args.output,
        zlata_result_path=ROOT / "outputs/cultivar_calibration_zlata_final_evaluation/best_cultivar_parameters.json",
        rye_provenance_path=ROOT / "simulation/calibrated/full_field2019_2023_calibrated_with_rye.provenance.json",
        irgina_reference_model=ROOT / "simulation/standart.apsimx",
        original_user_model=args.source,
        weather_source=ROOT / "data/weather/weather_2019_2023_normalized.met",
        weather_corrected=ROOT / "data/weather/weather_2019_2023_units_corrected.met",
    )
    print(json.dumps({
        "output": str(args.output),
        "output_sha256": provenance["output_model_sha256"],
        "source_snapshot_sha256": sha256(SNAPSHOT),
        "changed_commands": len(provenance["changes"]),
        "cultivar_variants": {
            name: row["variant_count"] for name, row in provenance["cultivars"].items()
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
