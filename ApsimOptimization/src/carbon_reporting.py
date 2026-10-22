"""Add and read carbon-pool reports without changing soil or management."""
from __future__ import annotations

import copy
import datetime as dt
import math


DETAIL_EXPRESSIONS = {
    "COrganic": "[Nutrient].Organic.C",
    "CHumic": "[Nutrient].Humic.C",
    "CMicrobial": "[Nutrient].Microbial.C",
    "CInert": "[Nutrient].Inert.C",
    "CFresh": "[Nutrient].FOM.C",
    "N_NO3": "[NO3].kgha",
    "N_NH4": "[NH4].kgha",
}
DETAIL_VARIABLES = ["[Clock].Today as Date"] + [
    f"{expression} as {alias}" for alias, expression in DETAIL_EXPRESSIONS.items()]


def add_carbon_reports(model):
    """Return an instrumented copy. Existing matching reports are idempotent."""
    result = copy.deepcopy(model)
    changes = []
    simulations = [s for s in result.get("Children", [])
                   if s.get("$type", "").split(",")[0] == "Models.Core.Simulation"
                   and s.get("Enabled", True)]
    if len(simulations) != 1:
        raise ValueError("Carbon reports require one enabled simulation")
    zones = [z for z in simulations[0].get("Children", [])
             if z.get("$type", "").split(",")[0] == "Models.Core.Zone"
             and z.get("Enabled", True)]
    if not zones:
        raise ValueError("No enabled fields for carbon reports")
    for zone in zones:
        name = f'OptCarbon_{zone["Name"]}'
        existing = [n for n in zone.get("Children", []) if n.get("Name") == name]
        if existing:
            if (len(existing) != 1 or not existing[0].get("Enabled", True)
                    or existing[0].get("$type", "").split(",")[0] != "Models.Report"
                    or existing[0].get("VariableNames") != DETAIL_VARIABLES
                    or existing[0].get("EventNames") != ["[Clock].EndOfDay"]):
                raise ValueError(f"Conflicting existing report: {name}")
            continue
        zone.setdefault("Children", []).append({
            "$type": "Models.Report, Models", "Name": name,
            "VariableNames": list(DETAIL_VARIABLES),
            "EventNames": ["[Clock].EndOfDay"], "GroupByVariableName": None,
            "ResourceName": None, "Children": [], "Enabled": True, "ReadOnly": False,
        })
        changes.append({"field": zone["Name"], "report": name})
    return result, changes


def read_carbon_layers(db, sim_id, checkpoint, initial, zone, thickness, carbon, years):
    """Check daily coverage and accounting identities before reporting layers.

    Accounting tolerance is numerical only (1e-6 kg/ha, relative 1e-10).
    It never determines agronomic acceptance.
    """
    field = zone["Name"]
    name = f"OptCarbon_{field}"
    report = [n for n in zone.get("Children", []) if n.get("Name") == name]
    if not report:
        return {"status": "not_assessed", "reason": "No detailed report in this model; user APSIM rerun required"}
    if (len(report) != 1 or report[0].get("VariableNames") != DETAIL_VARIABLES
            or report[0].get("EventNames") != ["[Clock].EndOfDay"]):
        raise ValueError(f"{field}: unverified detailed report definition")
    recorded = str(initial.get((f"{field}.{name}", "VariableNames")))
    if (any(v not in recorded for v in DETAIL_VARIABLES)
            or initial.get((f"{field}.{name}", "EventNames")) != "[Clock].EndOfDay"):
        raise ValueError(f"{field}: detailed database/report mismatch")
    quote = '"' + name.replace('"', '""') + '"'
    dates = list(carbon)
    rows = db.execute(
        f'SELECT * FROM {quote} WHERE SimulationID=? AND CheckpointID=? '
        'AND Date>=? AND Date<? ORDER BY Date',
        (sim_id, checkpoint, dates[0],
         (dt.date.fromisoformat(dates[-1]) + dt.timedelta(days=1)).isoformat())).fetchall()
    observed_dates = [dt.datetime.fromisoformat(r["Date"]).date().isoformat() for r in rows]
    if observed_dates != dates:
        raise ValueError(f"{field}: incomplete or duplicate detailed daily output")
    snapshots = {}
    relevant = {dates[0], *(f"{y}-12-31" for y in years)}
    for date, row in zip(dates, rows):
        if row["Zone"] != field:
            raise ValueError(f"{field}: detailed report belongs to another zone")
        layers = []
        for i in range(1, len(thickness) + 1):
            values = {}
            for alias in DETAIL_EXPRESSIONS:
                raw = row[f"{alias}({i})"]
                if raw is None or isinstance(raw, (str, bool)):
                    raise ValueError(f"{field}/{date}: missing numeric {alias}, layer {i}")
                value = float(raw)
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"{field}/{date}: invalid {alias}, layer {i}")
                values[alias] = value
            pool_sum = math.fsum(values[k] for k in ("CHumic", "CMicrobial", "CInert", "CFresh"))
            if not math.isclose(pool_sum, values["COrganic"], rel_tol=1e-10, abs_tol=1e-6):
                raise ValueError(f"{field}/{date}: pool sum differs from Organic.C, layer {i}")
            values["CWithoutFresh"] = math.fsum(values[k] for k in ("CHumic", "CMicrobial", "CInert"))
            layers.append(values)
        if not math.isclose(math.fsum(v["COrganic"] for v in layers), carbon[date],
                            rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError(f"{field}/{date}: layer sum differs from whole-profile OrganicC")
        if date in relevant:
            snapshots[date] = layers
    output = []
    top = 0.0
    for i, width in enumerate(thickness):
        start, end = snapshots[dates[0]][i], snapshots[dates[-1]][i]
        output.append({
            "top_mm": top, "bottom_mm": top + width, "start": start, "end": end,
            "change": {key: end[key] - start[key] for key in start},
            "annual_end": {str(y): snapshots[f"{y}-12-31"][i] for y in years},
        })
        top += width
    return {"status": "checked", "report": name, "unit": "kg element/ha per layer",
            "pool_sum_identity_checked": True, "profile_sum_identity_checked": True,
            "layers": output}


def compare_carbon_layers(candidate, baseline):
    if candidate.get("status") != "checked" or baseline.get("status") != "checked":
        return {"status": "not_assessed", "reason": "Detailed carbon required in both runs"}
    left, right = candidate["layers"], baseline["layers"]
    if (len(left) != len(right) or any((a["top_mm"], a["bottom_mm"]) !=
                                     (b["top_mm"], b["bottom_mm"]) for a, b in zip(left, right))):
        raise ValueError("Incompatible detailed carbon layers")
    return {"status": "checked", "layers": [{
        "top_mm": a["top_mm"], "bottom_mm": a["bottom_mm"],
        "end_difference": {key: a["end"][key] - b["end"][key] for key in a["end"]},
        "change_difference": {key: a["change"][key] - b["change"][key] for key in a["change"]},
    } for a, b in zip(left, right)]}


def summarize_carbon_pools(fields, baseline_fields=None):
    """Separate absolute period changes from reference differences, with no G.

    Never aggregate a partially assessed farm as though missing fields were 0.
    Field stocks are kg C/ha; farm totals use configured field areas (kg C).
    """
    metrics = ("COrganic", "CHumic", "CMicrobial", "CInert", "CFresh", "CWithoutFresh")
    if not fields or any((r.get("details") or {}).get("status") != "checked" for r in fields.values()):
        return {"status": "not_assessed", "reason": "Verified pool data required on every field"}
    if baseline_fields is not None:
        if (set(fields) != set(baseline_fields) or any(
                (r.get("details") or {}).get("status") != "checked" for r in baseline_fields.values())):
            return {"status": "not_assessed", "reason": "Complete compatible baseline pool data required"}
    by_field = {}
    for field, row in fields.items():
        layers = row["details"]["layers"]
        if baseline_fields is not None:
            base = baseline_fields[field]
            if (base["area_ha"] != row["area_ha"] or
                    [(l["top_mm"], l["bottom_mm"]) for l in base["details"]["layers"]] !=
                    [(l["top_mm"], l["bottom_mm"]) for l in layers]):
                raise ValueError(f"{field}: incompatible pool summary geometry/area")
        values = {}
        for metric in metrics:
            start = math.fsum(l["start"][metric] for l in layers)
            end = math.fsum(l["end"][metric] for l in layers)
            value = {"start_kg_ha": start, "end_kg_ha": end, "change_kg_ha": end - start}
            if baseline_fields is not None:
                reference = baseline_fields[field]["details"]["layers"]
                ref_start = math.fsum(l["start"][metric] for l in reference)
                ref_end = math.fsum(l["end"][metric] for l in reference)
                value.update(end_difference_from_baseline_kg_ha=end - ref_end,
                             change_difference_from_baseline_kg_ha=(end - start) - (ref_end - ref_start))
            values[metric] = value
        by_field[field] = values
    farm = {}
    for metric in metrics:
        totals = {key.replace("kg_ha", "kg"): math.fsum(
            fields[field]["area_ha"] * row[metric][key] for field, row in by_field.items())
                  for key in next(iter(by_field.values()))[metric]}
        totals["fields_below_period_start"] = [field for field, row in by_field.items()
                                               if row[metric]["change_kg_ha"] < 0]
        if baseline_fields is not None:
            totals["fields_below_baseline_end"] = [field for field, row in by_field.items()
                if row[metric]["end_difference_from_baseline_kg_ha"] < 0]
        farm[metric] = totals
    return {"status": "checked", "normative_humus_assessment": "not_assessed",
            "affects_objectives_or_constraints": False, "farm": farm, "fields": by_field}
