from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.apsim_runner import find_db_for_apsimx
from src.config import load_config
from src.results_reader import read_report
from src.run_preflight import validate_prepared_apsimx


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a CLI- or GUI-produced APSIMX run without accepting silent zero yield"
    )
    parser.add_argument("path", help="prepared .apsimx file or its SQLite .db result")
    parser.add_argument("--config", default="configs/optimizer_config.yaml")
    parser.add_argument("--models-dll", help="Models.dll used by the GUI, for runtime hash verification")
    args = parser.parse_args()

    config = load_config(args.config)
    requested = Path(args.path)
    requested = requested if requested.is_absolute() else ROOT / requested
    preflight = None
    expected_runtime_hash = None
    if requested.suffix.casefold() == ".apsimx":
        manifest_path = requested.parent / "run_manifest.json"
        manifest = None
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected_runtime_hash = (manifest.get("runtime") or {}).get("models_dll_sha256")
        post_run_quality = dict(config["run_quality"])
        if manifest and (manifest.get("preflight") or {}).get("status") == "passed":
            # APSIMX GUI may rewrite a previously verified relative Weather path
            # as an absolute path when it saves the document during Run. Preserve
            # the original manifest evidence and report the mutation as a warning.
            post_run_quality["require_relative_paths"] = False
            post_run_quality["allow_gui_datastore_default"] = True
        preflight = validate_prepared_apsimx(requested, post_run_quality)
        database = find_db_for_apsimx(requested)
        if database is None:
            raise FileNotFoundError(f"No SQLite result found next to {requested}")
    else:
        database = requested

    if args.models_dll:
        actual_models_dll = Path(args.models_dll)
        if not actual_models_dll.is_file():
            raise FileNotFoundError(f"Models.dll not found: {actual_models_dll}")
        actual_runtime_hash = _sha256_file(actual_models_dll)
        if expected_runtime_hash and actual_runtime_hash != expected_runtime_hash:
            raise RuntimeError(
                "APSIM runtime mismatch: GUI Models.dll SHA-256 differs from run_manifest.json"
            )
        runtime_verification = "matched" if expected_runtime_hash else "calculated_without_expected_hash"
    else:
        actual_runtime_hash = None
        runtime_verification = "not_checked" if expected_runtime_hash else "not_declared"

    values, diagnosis = read_report(
        database,
        config["results"],
        config.get("nitrogen_balance", {}),
        config["run_quality"],
    )
    summary = {
        "status": values["run_status"],
        "database": str(database),
        "yield_t_ha": values["yield_t_ha"],
        "yield_status": values["yield_status"],
        "crop_sown": values["crop_sown"],
        "sowing_event_count": values["sowing_event_count"],
        "sowing_detected_date": values["sowing_detected_date"],
        "harvest_reached": values["harvest_reached"],
        "harvest_event_count": values["harvest_event_count"],
        "harvest_detected_date": values["harvest_detected_date"],
        "peak_lai": values["peak_lai"],
        "peak_biomass": values["peak_biomass"],
        "warning": values["warning"],
        "runtime_verification": runtime_verification,
        "expected_models_dll_sha256": expected_runtime_hash,
        "actual_models_dll_sha256": actual_runtime_hash,
        "preflight": preflight,
        "selected_report_rowid": diagnosis.get("selected_report_rowid"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if str(values["run_status"]).startswith("completed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
