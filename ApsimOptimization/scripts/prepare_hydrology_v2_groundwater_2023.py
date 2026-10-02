#!/usr/bin/env python3
"""Create landscape-specific 2023 groundwater scenarios on isolated copies."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "experiments" / "hydrology_v2"
SOURCE = V2 / "ensemble" / "top30_ll_q25_dul_q95p03.apsimx"
GROUNDWATER = V2 / "audit" / "groundwater_observations.csv"
CONFIG_TEMPLATE = ROOT / "configs" / "hydrology_v2_top30_ll_q25_dul_q95p03.yaml"
OUTPUT = V2 / "groundwater_2023"
MONTHS = ("April", "May", "June", "July", "August", "September", "October")


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
        raise RuntimeError(f"Expected exactly one {name!r}; found {len(matches)}")
    return matches[0]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manager_code():
    return """using Models.Core;
using Models.Soils;
using System;

namespace Models
{
    [Serializable]
    public class Script : Model
    {
        [Link] private Clock clock;
        [Link] private Swim3 swim;
        public double ProfileDepthMm { get; set; }
        public double AprilDepthMm { get; set; }
        public double MayDepthMm { get; set; }
        public double JuneDepthMm { get; set; }
        public double JulyDepthMm { get; set; }
        public double AugustDepthMm { get; set; }
        public double SeptemberDepthMm { get; set; }
        public double OctoberDepthMm { get; set; }
        public double WaterTableDepthMm { get; private set; }

        [EventSubscribe("DoDailyInitialisation")]
        private void OnDoDailyInitialisation(object sender, EventArgs e)
        {
            WaterTableDepthMm = InterpolatedDepth(clock.Today);
            swim.SetWaterTable(WaterTableDepthMm);
        }

        private double InterpolatedDepth(DateTime today)
        {
            DateTime[] dates = {
                new DateTime(today.Year, 4, 15), new DateTime(today.Year, 5, 15),
                new DateTime(today.Year, 6, 15), new DateTime(today.Year, 7, 15),
                new DateTime(today.Year, 8, 15), new DateTime(today.Year, 9, 15),
                new DateTime(today.Year, 10, 15)};
            double[] depths = {AprilDepthMm, MayDepthMm, JuneDepthMm, JulyDepthMm,
                               AugustDepthMm, SeptemberDepthMm, OctoberDepthMm};
            if (today <= dates[0]) return depths[0];
            if (today >= dates[6]) return depths[6];
            for (int i = 0; i < dates.Length - 1; i++)
                if (today >= dates[i] && today <= dates[i + 1])
                {
                    double f = (today - dates[i]).TotalDays / (dates[i + 1] - dates[i]).TotalDays;
                    return depths[i] + f * (depths[i + 1] - depths[i]);
                }
            throw new Exception("Unable to interpolate groundwater depth");
        }
    }
}"""


def manager_node(depths):
    parameters = [{"Key": "ProfileDepthMm", "Value": "2100"}]
    parameters.extend(
        {"Key": f"{month}DepthMm", "Value": str(depth)}
        for month, depth in zip(MONTHS, depths)
    )
    return {
        "$type": "Models.Manager, Models",
        "CodeArray": manager_code().splitlines(),
        "Parameters": parameters,
        "Name": "ObservedWaterTable2023",
        "ResourceName": None,
        "Children": [],
        "Enabled": True,
        "ReadOnly": False,
    }


def load_depths():
    by_landscape = {}
    with GROUNDWATER.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["year"] != "2023":
                continue
            by_landscape.setdefault(int(row["landscape"]), []).append(row)
    result = {}
    for landscape, rows in by_landscape.items():
        rows.sort(key=lambda row: row["measurement_date"])
        if len(rows) == 7 and all(row["date_precision"] == "month" for row in rows):
            result[landscape] = [float(row["groundwater_depth_mm"]) for row in rows]
    return result


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    base = json.loads(SOURCE.read_text(encoding="utf-8"))
    config_template = CONFIG_TEMPLATE.read_text(encoding="utf-8")
    variants = []
    for landscape, depths in sorted(load_depths().items()):
        data = json.loads(json.dumps(base))
        field = named(data, "Field")
        field["Children"] = [
            child for child in field["Children"]
            if child.get("Name") not in {"SWIMSetLowerBC", "ObservedWaterTable2023"}
        ]
        field["Children"].append(manager_node(depths))
        report = named(data, "Report")
        expression = "[ObservedWaterTable2023].Script.WaterTableDepthMm as ObservedWaterTableDepthMm"
        if expression not in report["VariableNames"]:
            report["VariableNames"].append(expression)
        soil = named(data, "Soil")
        soil["Comments"] += (
            f" Groundwater scenario uses 2023 landscape {landscape} monthly observations, "
            "assigned to day 15 and linearly interpolated; winter is carried from April/October."
        )
        variant_id = f"ugv2023_landscape_{landscape}"
        apsimx = OUTPUT / f"{variant_id}.apsimx"
        apsimx.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        config = ROOT / "configs" / f"hydrology_v2_{variant_id}.yaml"
        config_text = config_template.replace(
            "experiments/hydrology_v2/ensemble/top30_ll_q25_dul_q95p03.apsimx",
            f"experiments/hydrology_v2/groundwater_2023/{variant_id}.apsimx",
        ).replace("years: [2020, 2021, 2022, 2023]", "years: [2023]")
        config_text = config_text.replace("calibration_years: [2020, 2021, 2022]", "calibration_years: []")
        config_text = config_text.replace(
            "outputs/.hydrology_v2_top30_ll_q25_dul_q95p03_work",
            f"outputs/.hydrology_v2_{variant_id}_work",
        ).replace(
            "outputs/hydrology_v2_top30_ll_q25_dul_q95p03",
            f"outputs/hydrology_v2_{variant_id}",
        )
        config.write_text(config_text, encoding="utf-8")
        variants.append(
            {
                "variant_id": variant_id,
                "landscape": landscape,
                "monthly_depth_mm_april_to_october": depths,
                "apsimx": apsimx.relative_to(ROOT).as_posix(),
                "config": config.relative_to(ROOT).as_posix(),
                "sha256": sha256(apsimx),
            }
        )
    manifest = {
        "source": SOURCE.relative_to(ROOT).as_posix(),
        "source_sha256": sha256(SOURCE),
        "variants": variants,
        "assumptions": [
            "Month-only measurements are assigned to day 15.",
            "Depth is linearly interpolated between monthly observations.",
            "April depth is carried backward and October depth forward because winter observations are absent.",
            "Landscape membership of the requested prediction must be known; variants are not averaged silently.",
        ],
        "status": "2023_groundwater_diagnostic_not_portable_prediction",
    }
    (OUTPUT / "groundwater_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Prepared {len(variants)} landscape groundwater scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
