#!/usr/bin/env python3
"""Prepare snow-only 2023 screens; frozen-soil infiltration is not represented."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "experiments" / "hydrology_v2"
SOURCE = V2 / "ensemble" / "top30_ll_q05_dul_s048.apsimx"
CODE = V2 / "components" / "SnowManager.cs"
CONFIG_TEMPLATE = ROOT / "configs" / "hydrology_v2_top30_ll_q05_dul_s048_2023_screen.yaml"
OUTPUT = V2 / "snow_screen_2023"


def walk(node):
    if isinstance(node, dict):
        yield node
        for child in node.get("Children", []):
            yield from walk(child)


def named(data, name):
    matches = [node for node in walk(data) if node.get("Name") == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {name!r}; found {len(matches)}")
    return matches[0]


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    base = json.loads(SOURCE.read_text(encoding="utf-8"))
    code = CODE.read_text(encoding="utf-8")
    template = CONFIG_TEMPLATE.read_text(encoding="utf-8")
    variants = []
    for factor in (2.0, 3.0, 4.0):
        data = json.loads(json.dumps(base))
        field = named(data, "Field")
        field["Children"] = [child for child in field["Children"] if child.get("Name") != "SnowManager"]
        field["Children"].append({
            "$type": "Models.Manager, Models", "CodeArray": code.splitlines(),
            "Parameters": [
                {"Key": "SnowTemperature", "Value": "-1.0"},
                {"Key": "RainTemperature", "Value": "1.0"},
                {"Key": "MeltTemperature", "Value": "0.0"},
                {"Key": "DegreeDayFactor", "Value": str(factor)},
                {"Key": "InitialSWE", "Value": "0.0"}],
            "Name": "SnowManager", "ResourceName": None, "Children": [],
            "Enabled": True, "ReadOnly": False})
        report = named(data, "Report")
        for expression in (
            "[SnowManager].Script.SWE as SnowSWE",
            "[SnowManager].Script.Snowfall as Snowfall",
            "[SnowManager].Script.Melt as SnowMelt",
            "[SnowManager].Script.RainToApsim as RainToApsim"):
            if expression not in report["VariableNames"]:
                report["VariableNames"].append(expression)
        tag = f"snow_ddf_{int(factor)}"
        apsimx = OUTPUT / f"{tag}.apsimx"
        apsimx.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        config = ROOT / "configs" / f"hydrology_v2_{tag}_2023.yaml"
        text = template.replace(
            "experiments/hydrology_v2/ensemble/top30_ll_q05_dul_s048.apsimx",
            f"experiments/hydrology_v2/snow_screen_2023/{tag}.apsimx").replace(
            "outputs/.hydrology_v2_top30_ll_q05_dul_s048_2023_screen_work",
            f"outputs/.hydrology_v2_{tag}_2023_work").replace(
            "outputs/hydrology_v2_top30_ll_q05_dul_s048_2023_screen",
            f"outputs/hydrology_v2_{tag}_2023")
        config.write_text(text, encoding="utf-8")
        variants.append({"id": tag, "degree_day_factor": factor,
                         "apsimx": apsimx.relative_to(ROOT).as_posix(),
                         "config": config.relative_to(ROOT).as_posix()})
    manifest = {"source": SOURCE.relative_to(ROOT).as_posix(), "variants": variants,
                "implemented": "temperature precipitation partition + SWE storage + degree-day melt",
                "not_implemented": "frozen-soil infiltration limitation",
                "status": "snow_only_diagnostic"}
    (OUTPUT / "snow_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(variants)} snow-only screens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
