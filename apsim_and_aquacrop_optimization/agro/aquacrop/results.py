"""Extract native daily, harvest and annual data; no plotting or model mutation."""
import csv
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from .field_config import ROOT, load_json, save_json


def write_csv(path, rows):
    if not rows:
        raise ValueError(f"No rows for {path.name}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize(field):
    output = ROOT / "outputs" / field["id"]
    mapping = load_json(ROOT / "generated" / field["id"] / "source_mapping.json")
    segments = {s["id"]: s for s in mapping["segments"]}
    daily, harvests = [], []
    annual = defaultdict(lambda: dict(days=0, rain_mm=0.0, eto_mm=0.0, transpiration_mm=0.0,
                                      evaporation_mm=0.0, drainage_mm=0.0, runoff_mm=0.0,
                                      grain_harvest_t_ha=0.0, forage_harvest_t_ha=0.0))
    previous_profile = None
    with (output / "daily.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            entry = json.loads(line)
            result = entry["result"]
            segment = segments[entry["segment_id"]]
            when = date(1901, 1, 1) + timedelta(days=result["dayNumber"]-1)
            compartments = result["compartments"]
            row = dict(date=when.isoformat(), segment=entry["segment_id"], effective_crop=segment["catalog_crop"],
                       canopy_cover_percent=result["canopyCover"]*100, biomass_t_ha=result["biomass"],
                       grain_mass_t_ha=result["dryYield"] if segment["kind"] == "grain" else None,
                       dry_yield_t_ha=result["dryYield"],
                       root_depth_m=result["rootingDepth"], profile_water_mm=result["profileWater"],
                       surface_water_mm=result["surfaceWater"], rain_mm=result["rain"], eto_mm=result["referenceEt"],
                       transpiration_mm=result["transpiration"], evaporation_mm=result["evaporation"],
                       drainage_mm=result["drainage"], runoff_mm=result["runoff"],
                       water_balance_error_mm=result["waterBalanceError"], salt_balance_error_g_m2=result["saltBalanceError"])
            for index in range(mapping["soil_layer_count"]):
                represented = [c for c in compartments if c["layerIndex"] == index]
                depth = sum(c["thickness"] for c in represented)
                row[f"theta_layer_{index+1}"] = sum(c["theta"]*c["thickness"] for c in represented)/depth if depth else None
            if previous_profile is not None and not math.isfinite(row["profile_water_mm"]):
                raise ValueError("Invalid daily profile water")
            previous_profile = row["profile_water_mm"]
            daily.append(row)
            stats = annual[when.year]
            stats["days"] += 1
            for key in ["rain_mm", "eto_mm", "transpiration_mm", "evaporation_mm", "drainage_mm", "runoff_mm"]:
                stats[key] += row[key]
            if result["cutting"] is not None:
                mass = result["cutting"]["dryYield"]
                harvests.append(dict(date=when.isoformat(), segment=segment["id"], kind="forage", dry_mass_t_ha=mass,
                                     quantity="AquaCrop effective-forage dry yield at scheduled cut",
                                     source_yield_column=segment.get("source_yield_column", "MixYield")))
                stats["forage_harvest_t_ha"] += mass
            if segment["kind"] == "grain" and when.isoformat() == segment["crop_end"]:
                harvest_date = segment["harvest_dates"][0]
                harvests.append(dict(date=harvest_date, segment=segment["id"], kind="grain", dry_mass_t_ha=result["dryYield"],
                                     quantity="effective crop dry yield at end of grain growing window",
                                     source_yield_column=segment.get("source_yield_column", "OatsYield")))
                annual[int(harvest_date[:4])]["grain_harvest_t_ha"] += result["dryYield"]
    expected = (date.fromisoformat(mapping["period_end"])-date.fromisoformat(mapping["period_start"])).days+1
    if len(daily) != expected:
        raise ValueError(f"Expected {expected} days, received {len(daily)}")
    area = mapping["area_ha"]
    annual_rows = [dict(field_id=field["id"], year=year, area_ha=area, **values,
                        grain_harvest_t=values["grain_harvest_t_ha"]*area,
                        forage_harvest_t=values["forage_harvest_t_ha"]*area)
                   for year, values in sorted(annual.items())]
    write_csv(output / "daily.csv", daily)
    write_csv(output / "harvests.csv", harvests)
    write_csv(output / "annual.csv", annual_rows)
    save_json(output / "summary.json", dict(field_id=field["id"], period_start=mapping["period_start"],
              period_end=mapping["period_end"], days=len(daily), segments=len(segments),
              maximum_water_balance_error_mm=max(abs(r["water_balance_error_mm"]) for r in daily),
              maximum_salt_balance_error_g_m2=max(abs(r["salt_balance_error_g_m2"]) for r in daily),
              status="uncalibrated_effective_mixtures", annual=annual_rows,
              warning=("Standalone native AquaCrop harvests; uncalibrated effective crop; no external soil model." if field.get("management_mode")=="standalone" else
                       "Native HarvestReport is the harvest comparison; standing daily Yield is retained separately. Uncalibrated effective crop." if field.get("management_mode") in ("fixed_rotation","planned_rotation") else "APSIM comparison uses original species Yield expressions; MixYield is standing mass, not removed mass. Uncalibrated effective crop.")))
    print(f"{field['id']}: {len(daily)} days, {len(segments)} segments; reports: {output}", flush=True)
    return annual_rows
