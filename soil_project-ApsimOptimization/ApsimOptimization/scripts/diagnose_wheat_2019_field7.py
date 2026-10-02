#!/usr/bin/env python3
"""Diagnose the failed 2019 Irgina crop on Field7 with one APSIM run."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import sqlite3
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.multifield_model import child, inventory, kind, prepare, read_json, walk, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.met_weather import calculate_tav_amp, read_met


EXPRESSIONS = (
    "[Weather].Rain as Rain",
    "[Weather].MaxT as MaxT",
    "[Weather].MinT as MinT",
    "[Weather].Radn as Radn",
    "[Wheat].Leaf.Fw as WheatLeafFw",
    "[Wheat].Leaf.Fn as WheatLeafFn",
    "[Wheat].Leaf.WaterDemand as WheatLeafWaterDemand",
    "[Wheat].Leaf.WaterAllocation as WheatLeafWaterAllocation",
    "[Wheat].Leaf.DMSupplyPhotosynthesis as WheatLeafPhotosynthesis",
    "[Wheat].Root.Depth as WheatRootDepth",
    "[Wheat].Root.FASW as WheatRootFASW",
    "[Wheat].Root.WaterTensionFactor as WheatRootWaterTensionFactor",
    "[Wheat].Root.WaterUptake as WheatRootWaterUptake",
    "[Wheat].Root.NUptake as WheatRootNUptake",
    "[Wheat].Population as WheatPopulation",
    "[Wheat].Structure.TotalStemPopn as WheatTotalStemPopulation",
    "[Wheat].Grain.Number as WheatGrainNumber",
    "[Wheat].Grain.Size as WheatGrainSize",
)


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def write_warmup_weather(destination):
    sources = (
        ROOT/"data"/"calibration_weather"/"weather_irgina_2018.met",
        ROOT/"data"/"weather"/"weather_2019_2023_units_corrected.met",
    )
    parts = [read_met(path) for path in sources]
    if parts[0]["columns"] != parts[1]["columns"] or parts[0]["units"] != parts[1]["units"]:
        raise ValueError("Warm-up and production weather schemas differ")
    rows = parts[0]["rows"] + parts[1]["rows"]
    tav, amp = calculate_tav_amp(rows)
    columns = parts[0]["columns"]
    with Path(destination).open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("[weather.met.weather]\n")
        stream.write("! diagnostic warm-up: colleague-approved 2018 plus corrected 2019-2023\n")
        stream.write("latitude = 56.836555\nlongitude = 35.924370\n")
        stream.write(f"tav = {tav:.3f}\namp = {amp:.3f}\n\n")
        stream.write(" ".join(columns) + "\n")
        stream.write(" ".join(parts[0]["units"][name] for name in columns) + "\n")
        for row in rows:
            values = []
            for column in columns:
                value = row[column]
                if column in {"year", "day"}:
                    values.append(str(int(value)))
                elif column == "sp":
                    values.append(f"{value:.1f}")
                else:
                    values.append(f"{value:.3f}")
            stream.write(" ".join(values) + "\n")
    return {"sources": [str(path.resolve()) for path in sources],
            "rows": len(rows), "tav": tav, "amp": amp}


def patch_model(data, spring_profile_at_dul=False, warmup_2018=False,
                measured_topsoil=None):
    simulations = [node for _, node in walk(data)
                   if kind(node) == "Models.Core.Simulation" and node.get("Enabled", True)]
    if len(simulations) != 1:
        raise ValueError("Expected one enabled Simulation")
    simulation = simulations[0]
    child(simulation, "Clock")["End"] = "2019-12-31T00:00:00"
    changes = {"added_expressions": [], "soil_water": [], "rotation_anchor": [],
               "measured_topsoil": []}
    if spring_profile_at_dul:
        child(simulation, "Clock")["Start"] = "2019-05-01T00:00:00"
    elif warmup_2018:
        child(simulation, "Clock")["Start"] = "2018-09-01T00:00:00"
    for zone in [node for node in simulation.get("Children", [])
                 if kind(node) == "Models.Core.Zone" and node.get("Enabled", True)]:
        if warmup_2018:
            manager = child(zone, "Sow using a variable rule")
            code = manager.get("CodeArray", [])
            matches = [index for index, line in enumerate(code)
                       if "startYear = Clock.StartDate.Year;" in line]
            if len(matches) != 1:
                raise ValueError(f"{zone['Name']}: rotation start-year assignment not found")
            index = matches[0]
            code[index] = code[index].replace(
                "startYear = Clock.StartDate.Year;", "startYear = 2019;")
            changes["rotation_anchor"].append({"field": zone["Name"], "start_year": 2019})
        reports = [node for node in zone.get("Children", []) if kind(node) == "Models.Report"]
        for report in reports:
            if zone.get("Name") != "Field7":
                report["Enabled"] = False
                continue
            report["Enabled"] = True
            variables = report.setdefault("VariableNames", [])
            for expression in EXPRESSIONS:
                if expression not in variables:
                    variables.append(expression)
                    changes["added_expressions"].append(expression)
        if spring_profile_at_dul and zone.get("Name") == "Field7":
            soil = child(zone, "Soil")
            physical = child(soil, "Physical")
            water = child(soil, "Water")
            for layer in (0, 1):
                before = float(water["InitialValues"][layer])
                after = float(physical["DUL"][layer])
                water["InitialValues"][layer] = after
                changes["soil_water"].append({
                    "layer": layer + 1, "before": before, "after": after,
                    "endpoint": "Physical.DUL",
                })
        if measured_topsoil and zone.get("Name") == "Field7":
            soil = child(zone, "Soil")
            physical = child(soil, "Physical")
            water = child(soil, "Water")
            wheat_soil = child(physical, "WheatSoil")
            for layer in (0, 1):
                assignments = [
                    (physical, "AirDry", measured_topsoil["airdry"]),
                    (physical, "LL15", measured_topsoil["ll15"]),
                    (wheat_soil, "LL", measured_topsoil["ll15"]),
                ]
                if measured_topsoil.get("dul") is not None:
                    assignments.append((physical, "DUL", measured_topsoil["dul"]))
                initial = (measured_topsoil["dul"]
                           if measured_topsoil.get("dul") is not None
                           else float(physical["DUL"][layer]))
                assignments.append((water, "InitialValues", initial))
                for node, parameter, after in assignments:
                    before = float(node[parameter][layer])
                    node[parameter][layer] = after
                    changes["measured_topsoil"].append({
                        "object": node["Name"], "parameter": parameter,
                        "layer": layer + 1, "before": before, "after": after,
                        "source": measured_topsoil["source"],
                    })
    return changes


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        return
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def maximum(rows, column):
    values = [(number(row.get(column)), row.get("date")) for row in rows]
    values = [item for item in values if item[0] is not None]
    value, date = max(values, default=(None, None))
    return {"value": value, "date": date}


def stress_days(rows, column, threshold):
    return sum(1 for row in rows
               if number(row.get(column)) is not None
               and number(row[column]) < threshold)


def archive_result(archive_path, output):
    names = ("summary.json", "daily_field7_2019.csv", "report_patch.json",
             "config_snapshot.json", "model_inventory.json", "preparation.json", "apsim.log")
    names = names + ("weather_2018_2023_warmup.met",)
    files = [output/name for name in names if (output/name).is_file()]
    manifest = {"files": [{"path": file.name, "size": file.stat().st_size,
                            "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
                           for file in files]}
    with tarfile.open(archive_path, "w:gz") as archive:
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        info.mtime = int(dt.datetime.now().timestamp())
        archive.addfile(info, io.BytesIO(payload))
        for file in files:
            archive.add(file, arcname=file.name, recursive=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--config", type=Path,
                        default=ROOT/"configs"/"multifield_historical_replay.yaml")
    parser.add_argument("--spring-profile-at-dul", action="store_true",
                        help="Start on 1 May with Field7 top 300 mm at DUL")
    parser.add_argument("--warmup-2018", action="store_true",
                        help="Use Sep-Dec 2018 as a state warm-up before the 2019 crop")
    parser.add_argument("--measured-topsoil-q25", action="store_true",
                        help="Use the conservative q25 measured topsoil profile on Field7")
    parser.add_argument("--measured-dry-current-wet", action="store_true",
                        help="Use measured dry limits with current DUL on Field7")
    args = parser.parse_args(argv)
    if sum((args.spring_profile_at_dul, args.warmup_2018,
            args.measured_topsoil_q25, args.measured_dry_current_wet)) > 1:
        parser.error("choose only one diagnostic scenario")
    config = load_config(args.config)
    observations = json.loads(Path(config["historical_observations"])
                              .read_text(encoding="utf-8-sig"))
    record = next(row for row in observations["years"]["2019"]
                  if row["field"] == "Field7")
    start = record["observed_sow_date"]
    end = record["observed_harvest_date"]

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    scenario = ("spring_profile_at_dul" if args.spring_profile_at_dul else
                "warmup_2018" if args.warmup_2018 else
                "measured_topsoil_q25" if args.measured_topsoil_q25 else
                "measured_dry_current_wet" if args.measured_dry_current_wet else
                "historical_control")
    output = ROOT/"outputs"/"multifield"/"WHEAT_2019_FIELD7"/stamp
    output.mkdir(parents=True, exist_ok=False)
    data = read_json(config["model"])
    measured_topsoil = None
    if args.measured_topsoil_q25 or args.measured_dry_current_wet:
        manifest_path = (ROOT/"experiments"/"hydrology_v2"/"ensemble"/
                         "ensemble_manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        candidate = next(item for item in manifest["candidates"]
                         if item["candidate_id"] == "top30_ll_q25_dul_q95p03")
        measured_topsoil = {
            "airdry": candidate["top30_airdry_m3_m3"],
            "ll15": candidate["top30_ll15_m3_m3"],
            "dul": (None if args.measured_dry_current_wet
                    else candidate["top30_dul_m3_m3"]),
            "source": ("2023 Field3 topsoil observations; q01 AirDry and q25 LL15; "
                       + ("current model DUL retained"
                          if args.measured_dry_current_wet
                          else "q95+0.03 DUL sensitivity")),
        }
    changes = patch_model(data, args.spring_profile_at_dul, args.warmup_2018,
                          measured_topsoil)
    source = output/"source_candidate.apsimx"
    write_json(source, data)
    case = dict(config)
    case["model"] = str(source.resolve())
    case["model_provenance"] = None
    if args.warmup_2018:
        warmup_weather = output/"weather_2018_2023_warmup.met"
        changes["weather"] = write_warmup_weather(warmup_weather)
        case["weather"] = str(warmup_weather.resolve())
        case["weather_provenance"] = None
        case["historical_replay"] = {"enabled": False}
    else:
        case["historical_replay"] = dict(config["historical_replay"], enabled=True)
    case["scenario"] = {"kind": "diagnostic", "name": scenario,
                        "recommendation_status": "prohibited"}
    write_json(output/"report_patch.json", changes)
    write_json(output/"config_snapshot.json", case)
    write_json(output/"model_inventory.json", inventory(case))
    prepared = prepare(case, output)
    database = run_apsim(case, prepared)

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        simulation_id = connection.execute(
            "SELECT ID FROM _Simulations ORDER BY ID LIMIT 1").fetchone()[0]
        source_rows = connection.execute(
            'SELECT * FROM "Report7" WHERE SimulationID=? '
            'AND substr("Clock.Today",1,10)>=? AND substr("Clock.Today",1,10)<=? '
            'ORDER BY "Clock.Today"', (simulation_id, start, end)).fetchall()
    rows = []
    for source_row in source_rows:
        row = dict(source_row)
        row["date"] = str(row.pop("Clock.Today"))[:10]
        rows.append(row)
    write_csv(output/"daily_field7_2019.csv", rows)
    active = [row for row in rows if (number(row.get("WheatAGWt")) or 0) > 0]
    summary = {
        "case": "2019/Field7/Irgina",
        "scenario": scenario,
        "observed_yield_t_ha": record["observed_yield_t_ha"],
        "period": {"sowing": start, "harvest": end},
        "weather": {
            "rain_total_mm": sum(number(row.get("Rain")) or 0 for row in rows),
            "rain_first_14_days_mm": sum(number(row.get("Rain")) or 0 for row in rows[:14]),
        },
        "growth": {
            "active_days": len(active),
            "first_active_date": active[0]["date"] if active else None,
            "max_lai": maximum(active, "WheatLAI"),
            "max_aboveground_biomass_g_m2": maximum(active, "WheatAGWt"),
            "max_grain_yield_kg_ha": maximum(active, "WheatYield"),
            "max_root_depth_mm": maximum(active, "WheatRootDepth"),
            "max_population_m2": maximum(active, "WheatPopulation"),
        },
        "stress": {
            "water_days_fw_below_0_8": stress_days(active, "WheatLeafFw", 0.8),
            "water_days_fw_below_0_5": stress_days(active, "WheatLeafFw", 0.5),
            "nitrogen_days_fn_below_0_8": stress_days(active, "WheatLeafFn", 0.8),
            "min_root_fasw": min((number(row.get("WheatRootFASW")) for row in active
                                  if number(row.get("WheatRootFASW")) is not None), default=None),
        },
        "interpretation": "Diagnostic evidence only; no soil or cultivar parameter is changed.",
    }
    write_json(output/"summary.json", summary)
    archive_path = ROOT/f"wheat_2019_field7_{scenario}_{stamp}.tar.gz"
    archive_result(archive_path, output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"WHEAT 2019 FIELD7 ARCHIVE: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
