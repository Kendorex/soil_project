from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V2_ROOT = ROOT / "experiments" / "hydrology_v2"
BASE = V2_ROOT / "template" / "standart_hydrology_v2_base.apsimx"
WORKSPACE_MANIFEST = V2_ROOT / "workspace_manifest.json"
FIELD_INPUT = V2_ROOT / "field_input_2023.json"
CANDIDATE_DIR = V2_ROOT / "candidates"
CANDIDATE_MANIFEST = CANDIDATE_DIR / "candidate_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def named(node: Any, name: str) -> dict[str, Any]:
    if isinstance(node, dict):
        if node.get("Name") == name:
            return node
        for value in node.values():
            try:
                return named(value, name)
            except KeyError:
                pass
    elif isinstance(node, list):
        for value in node:
            try:
                return named(value, name)
            except KeyError:
                pass
    raise KeyError(name)


def validate_profile(physical: dict[str, Any], crop: dict[str, Any], water: dict[str, Any]) -> None:
    names = ("Thickness", "BD", "AirDry", "LL15", "DUL", "SAT")
    layer_count = len(physical["Thickness"])
    if layer_count == 0 or any(len(physical[name]) != layer_count for name in names):
        raise ValueError("Physical profile arrays must have equal non-zero lengths")
    if len(crop["LL"]) != layer_count or len(water["InitialValues"]) != layer_count:
        raise ValueError("Wheat LL and initial water must match the physical layer count")
    for index in range(layer_count):
        airdry = float(physical["AirDry"][index])
        ll15 = float(physical["LL15"][index])
        dul = float(physical["DUL"][index])
        sat = float(physical["SAT"][index])
        initial = float(water["InitialValues"][index])
        crop_ll = float(crop["LL"][index])
        if not (0.0 <= airdry <= ll15 < dul < sat < 1.0):
            raise ValueError(
                f"Invalid layer {index + 1}: AirDry={airdry}, LL15={ll15}, DUL={dul}, SAT={sat}"
            )
        if not 0.0 <= initial <= sat:
            raise ValueError(f"Initial water outside 0..SAT in layer {index + 1}")
        if not airdry <= crop_ll < sat:
            raise ValueError(f"Wheat LL outside AirDry..SAT in layer {index + 1}")


def main() -> int:
    workspace = json.loads(WORKSPACE_MANIFEST.read_text(encoding="utf-8"))
    field = json.loads(FIELD_INPUT.read_text(encoding="utf-8"))
    actual_base_hash = sha256(BASE)
    if actual_base_hash != workspace["base_copy_sha256"]:
        raise RuntimeError("Hydrology-v2 base copy changed after workspace creation")

    base = json.loads(BASE.read_text(encoding="utf-8"))
    measured_bd = float(field["known_field_values"]["top_0_20_cm_bulk_density_mean_g_cm3"])
    sat_from_density = 1.0 - measured_bd / 2.65
    definitions = [
        {
            "id": "00_baseline_copy",
            "mode": "unchanged_reference",
            "description": "Byte-derived reference; no field hydrology correction",
        },
        {
            "id": "01_measured_bd_top30",
            "mode": "measured_bd_top30_sat_from_porosity",
            "description": "Measured 2023 field-3 mean BD in layers 0-15 and 15-30 cm; SAT from BD",
        },
    ]

    CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    prepared = []
    for definition in definitions:
        data = deepcopy(base)
        physical = named(data, "Physical")
        crop = named(data, "WheatSoil")
        water = named(data, "Water")
        before = {
            "BD": list(physical["BD"][:2]),
            "SAT": list(physical["SAT"][:2]),
        }
        applied: dict[str, Any] = {"mode": definition["mode"]}
        if definition["mode"] == "measured_bd_top30_sat_from_porosity":
            for index in (0, 1):
                physical["BD"][index] = measured_bd
                physical["SAT"][index] = sat_from_density
            soil = named(data, "Soil")
            soil["DataSource"] = (
                "Hydrology v2 diagnostic candidate: field-3 2023 topsoil BD; "
                "retention and deep profile inherited unvalidated from the base template"
            )
            soil["Comments"] = (
                "Only BD and density-derived SAT in 0-30 cm are field-constrained. "
                "This candidate is not a validated field soil profile."
            )
            applied.update(
                {
                    "layers": [1, 2],
                    "BD_g_cm3": measured_bd,
                    "SAT_m3_m3": sat_from_density,
                    "SAT_formula": "1 - BD / 2.65",
                    "measurement_point_count": field["known_field_values"][
                        "top_0_20_cm_bulk_density_point_count"
                    ],
                    "inherited_unvalidated": ["AirDry", "LL15", "DUL", "WheatSoil.LL", "Ks"],
                }
            )
        validate_profile(physical, crop, water)
        database = f"{definition['id']}.db"
        named(data, "DataStore")["FileName"] = database
        output = CANDIDATE_DIR / f"{definition['id']}.apsimx"
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        prepared.append(
            {
                **definition,
                "apsimx": output.relative_to(ROOT).as_posix(),
                "apsimx_sha256": sha256(output),
                "database": database,
                "before_top30": before,
                "after_top30": {
                    "BD": list(physical["BD"][:2]),
                    "SAT": list(physical["SAT"][:2]),
                },
                "applied": applied,
                "validation": "physical_ordering_passed",
            }
        )

    manifest = {
        "base_copy": BASE.relative_to(ROOT).as_posix(),
        "base_copy_sha256": actual_base_hash,
        "field_input": FIELD_INPUT.relative_to(ROOT).as_posix(),
        "candidates": prepared,
        "status": "diagnostic_candidates_not_validated_for_prediction",
    }
    CANDIDATE_MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"HYDROLOGY V2 CANDIDATES OK: {len(prepared)}")
    for item in prepared:
        print(f"  {item['id']}: {item['apsimx']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
