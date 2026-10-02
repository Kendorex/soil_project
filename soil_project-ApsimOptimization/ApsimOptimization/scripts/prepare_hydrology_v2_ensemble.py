#!/usr/bin/env python3
"""Build a small, data-bounded topsoil retention ensemble on isolated copies."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "experiments" / "hydrology_v2"
SOURCE = V2 / "candidates" / "01_measured_bd_top30.apsimx"
OBSERVATIONS = V2 / "audit" / "topsoil_water_observations.csv"
OUTPUT = V2 / "ensemble"
CONFIG_TEMPLATE = ROOT / "configs" / "hydrology_v2_zlata_2020_2023_bd_only.yaml"


def walk(node):
    if isinstance(node, dict):
        yield node
        for child in node.get("Children", []):
            yield from walk(child)
    elif isinstance(node, list):
        for child in node:
            yield from walk(child)


def named(data, name):
    matches = [node for node in walk(data) if node.get("Name") == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one node named {name!r}; found {len(matches)}")
    return matches[0]


def quantile(values, probability):
    values = sorted(float(value) for value in values)
    if not values:
        raise ValueError("Cannot calculate a quantile of an empty sample")
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_target_values():
    with OBSERVATIONS.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = csv.DictReader(handle)
        return [
            float(row["volumetric_water_m3_m3"])
            for row in rows
            if row["year"] == "2023"
            and row["field"] == "3"
            and row["quality_flag"] == "ok"
        ]


def validate(physical, crop):
    for index in range(2):
        values = (
            physical["AirDry"][index],
            physical["LL15"][index],
            physical["DUL"][index],
            physical["SAT"][index],
        )
        if not values[0] < values[1] < values[2] < values[3]:
            raise RuntimeError(f"Invalid AirDry < LL15 < DUL < SAT in layer {index + 1}: {values}")
        if crop["LL"][index] != physical["LL15"][index]:
            raise RuntimeError("Wheat lower limit must match the tested physical LL15 in topsoil")


def main():
    if not SOURCE.is_file() or not OBSERVATIONS.is_file():
        raise SystemExit("Run the hydrology-v2 audit and candidate preparation first")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    values = load_target_values()
    if len(values) != 120:
        raise SystemExit(f"Expected 120 valid 2023 field-3 observations; found {len(values)}")

    airdry = quantile(values, 0.01)
    ll_hypotheses = [("q05", quantile(values, 0.05)), ("q10", quantile(values, 0.10)), ("q25", quantile(values, 0.25))]
    # DUL was not measured directly. The 0.03 margin above the wet-tail
    # observation is an explicit sensitivity assumption, not a measurement.
    dul_hypotheses = [
        ("q95p03", quantile(values, 0.95) + 0.03, "wet-tail observation + 0.03"),
        ("q99p03", quantile(values, 0.99) + 0.03, "wet-tail observation + 0.03"),
        ("s032", 0.32, "unmeasured DUL sensitivity value within porosity bound"),
        ("s040", 0.40, "unmeasured DUL sensitivity value within porosity bound"),
        ("s048", 0.48, "unmeasured DUL sensitivity value within porosity bound"),
    ]
    base = json.loads(SOURCE.read_text(encoding="utf-8"))
    template_text = CONFIG_TEMPLATE.read_text(encoding="utf-8")
    candidates = []
    for ll_label, ll15 in ll_hypotheses:
        for dul_label, dul, dul_source in dul_hypotheses:
            candidate_id = f"top30_ll_{ll_label}_dul_{dul_label}"
            data = json.loads(json.dumps(base))
            physical = named(data, "Physical")
            crop = named(data, "WheatSoil")
            water = named(data, "Water")
            for index in range(2):
                physical["AirDry"][index] = airdry
                physical["LL15"][index] = ll15
                physical["DUL"][index] = dul
                crop["LL"][index] = ll15
                water["InitialValues"][index] = dul
            soil = named(data, "Soil")
            soil["DataSource"] = "Hydrology v2 empirical topsoil sensitivity ensemble"
            soil["Comments"] = (
                "Top 0-30 cm AirDry/LL15 hypotheses are quantile-derived from 2023 field-3 "
                "0-20 cm water observations; DUL includes an explicit +0.03 sensitivity margin. "
                "Deep profile and hydraulic conductivity remain inherited and unvalidated."
            )
            validate(physical, crop)
            apsimx = OUTPUT / f"{candidate_id}.apsimx"
            apsimx.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            config_path = ROOT / "configs" / f"hydrology_v2_{candidate_id}.yaml"
            config_text = template_text.replace(
                "experiments/hydrology_v2/candidates/01_measured_bd_top30.apsimx",
                f"experiments/hydrology_v2/ensemble/{candidate_id}.apsimx",
            ).replace(
                "outputs/.hydrology_v2_zlata_bd_only_work",
                f"outputs/.hydrology_v2_{candidate_id}_work",
            ).replace(
                "outputs/hydrology_v2_zlata_bd_only",
                f"outputs/hydrology_v2_{candidate_id}",
            )
            config_path.write_text(config_text, encoding="utf-8")
            screening_config_path = ROOT / "configs" / f"hydrology_v2_{candidate_id}_2023_screen.yaml"
            screening_text = config_text.replace("years: [2020, 2021, 2022, 2023]", "years: [2023]")
            screening_text = screening_text.replace("calibration_years: [2020, 2021, 2022]", "calibration_years: []")
            screening_text = screening_text.replace(
                f"outputs/.hydrology_v2_{candidate_id}_work",
                f"outputs/.hydrology_v2_{candidate_id}_2023_screen_work",
            ).replace(
                f"outputs/hydrology_v2_{candidate_id}",
                f"outputs/hydrology_v2_{candidate_id}_2023_screen",
            )
            screening_config_path.write_text(screening_text, encoding="utf-8")
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "apsimx": apsimx.relative_to(ROOT).as_posix(),
                    "config": config_path.relative_to(ROOT).as_posix(),
                    "screening_config_2023": screening_config_path.relative_to(ROOT).as_posix(),
                    "sha256": sha256(apsimx),
                    "top30_airdry_m3_m3": airdry,
                    "top30_ll15_m3_m3": ll15,
                    "top30_dul_m3_m3": dul,
                    "top30_dul_source": dul_source,
                    "top30_sat_m3_m3": physical["SAT"][0],
                    "deep_profile_status": "inherited_unvalidated",
                    "prediction_status": "diagnostic_only",
                }
            )

    manifest = {
        "schema_version": 1,
        "source_candidate": SOURCE.relative_to(ROOT).as_posix(),
        "source_sha256": sha256(SOURCE),
        "observation_source": OBSERVATIONS.relative_to(ROOT).as_posix(),
        "observation_filter": "year=2023, field=3, quality_flag=ok",
        "observation_count": len(values),
        "derivation": {
            "AirDry": "q01 of point volumetric water observations",
            "LL15": ["q05", "q10", "q25"],
            "DUL": ["q95 + 0.03", "q99 + 0.03", "0.32", "0.40", "0.48"],
            "DUL_status": "explicit sensitivity axis; field capacity was not measured",
            "layers": "same hypothesis applied to APSIM layers 0-15 and 15-30 cm",
        },
        "candidates": candidates,
        "status": "bounded_sensitivity_ensemble_not_validated_soil_profiles",
    }
    manifest_path = OUTPUT / "ensemble_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(candidates)} isolated ensemble candidates")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
