"""Prepare independent fields and explicit AquaCrop input files from source APSIM managers."""

import json
import re
import shutil
from datetime import date, timedelta
from pathlib import Path

from .weather_utils import hargreaves_eto, weather_text

WORKSPACE = Path(__file__).resolve().parents[2]
ROOT = WORKSPACE / "models/aquacrop"
ENGINE_ROOT = WORKSPACE / "engines/aquacrop"
ENGINE_MANIFEST = ENGINE_ROOT / "field-runner/Cargo.toml"
ENGINE_TARGET = ENGINE_ROOT / "field-target"

# Zero-based positions in the sequential 7.3 CRO layout, as decoded by core's crop reader.
CROP_FIELDS = {
    "version": 1, "kind": 3, "planting_method": 4, "cycle_mode": 5,
    "base_temperature": 7, "upper_temperature": 8, "total_gdd": 9,
    "kc_decline": 35, "minimum_root_depth": 36, "maximum_root_depth": 37,
    "seedling_size": 42, "regrowth_size": 43, "population_per_ha": 44,
    "growth_per_day": 45, "maximum_cover": 49, "decline_per_day": 50,
    "emergence": 51, "maximum_rooting": 52, "senescence": 53, "maturity": 54,
    "flowering": 55, "flowering_length": 56, "determinacy": 57, "excess_fruits": 58,
    "harvest_index_days": 59, "water_productivity": 60, "harvest_index_percent": 63,
    "dry_matter_percent": 77, "first_year_root_depth": 78, "sown_in_first_year": 79,
    "assimilate_transfer": 80, "storage_period": 81, "stored_percent": 82, "mobilized_percent": 83,
}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def local_path(text):
    path = (ROOT / text).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"Path leaves AquaCrop model: {text}")
    return path


def walk(node):
    yield node
    for child in node.get("Children", []):
        yield from walk(child)


def unique_node(nodes, *, name=None, kind=None):
    matches = [n for n in nodes if (name is None or n.get("Name") == name)
               and (kind is None or n.get("$type") == kind)]
    if len(matches) != 1:
        raise ValueError(f"Expected one source node: name={name}, kind={kind}; found {len(matches)}")
    return matches[0]


def source_parts(field):
    project = load_json(local_path(field["source_project"]))
    simulation = unique_node(list(walk(project)), name=field["source_simulation"], kind="Models.Core.Simulation, Models")
    zone = unique_node(list(walk(simulation)), name=field["source_zone"], kind="Models.Core.Zone, Models")
    return project, simulation, zone


def registry(path=None):
    project = load_json(path or ROOT / "project.json")
    if project.get('schema_version') == 2 and project.get('engine') == 'aquacrop_standalone':
        from .standalone_inputs import registry as standalone_registry
        return standalone_registry(project)
    if project.get("schema_version") != 1:
        raise ValueError("Unsupported project schema version")
    source_project = project["source_project"]
    if not local_path(source_project).is_file():
        raise FileNotFoundError(f"Missing source APSIM model: {source_project}")
    fields = [load_json(local_path(name)) for name in project["fields"]]
    ids = [f["id"] for f in fields]
    if len(ids) != len(set(ids)):
        raise ValueError("Field identifiers must be unique")
    for field in fields:
        if field.get("source_project", source_project) != source_project:
            raise ValueError("Field source model disagrees with project.json")
        field["source_project"] = source_project
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", field["id"]):
            raise ValueError("Use a safe lower-case field identifier")
        if field.get("schema_version") != 1 or field.get("mixture_mode") != "effective_crop":
            raise ValueError("Only schema 1 with explicit effective_crop mixtures is supported")
    return fields


def replace_values(template, changes):
    lines = template.splitlines()
    for index, value in changes.items():
        comment = lines[index].partition(":")[2]
        lines[index] = f"{str(value):>12} :{comment}" if comment else str(value)
    return "\n".join(lines) + "\n"


def crop_text(template, description, values):
    changes = {CROP_FIELDS[key]: value for key, value in values.items()}
    changes[0] = description
    for index in range(68, 77):
        changes[index] = -9
    text = replace_values(template, changes)
    return text.replace("Determination of crop cycle : by growing degree-days",
                        "Determination of crop cycle : by calendar days (effective proxy)")


def create_catalog():
    """Load the supplied crop catalogue; missing model inputs are errors."""
    path = ROOT / "DATA/crops/transferred/catalog.json"
    catalog = load_json(path)["crops"]
    for profile in catalog.values():
        if not local_path(profile["file"]).is_file():
            raise FileNotFoundError(f"Missing crop profile: {profile['file']}")
    return catalog


def annual_cleanup_rotation(field, simulation, zone):
    """Replay realized sowing dates, not the source's weather/soil sowing rule."""
    clock = unique_node(list(walk(simulation)), kind="Models.Clock, Models")
    start, end = (date.fromisoformat(clock[key][:10]) for key in ["Start", "End"])
    manager = unique_node(list(walk(zone)), name="Sow using a variable rule")
    code = "\n".join(manager.get("CodeArray", []))
    if not all(v in code for v in ["End of year cleanup", "End of season harvest", "CanSow()"]):
        raise ValueError("annual_cleanup adapter does not match the source manager")
    events_path = ROOT / "reference" / field["id"] / "management_events.json"
    if not events_path.exists():
        raise ValueError(f"Run --reference first to obtain actual sowing dates for {field['id']}")
    events = load_json(events_path)
    parameters = {v["Key"]: v["Value"] for v in manager["Parameters"]}
    keys = ["RotationCrops", "RotationCultivars", "RotationSowingDepths", "RotationRowSpacings", "RotationPopulations"]
    arrays = {key: [v.strip() for v in parameters[key].split(",")] for key in keys}
    if any(len(v) != end.year-start.year+1 for v in arrays.values()):
        raise ValueError("Source rotation must have one entry per year")
    rows = []
    for index, year in enumerate(range(start.year, end.year+1)):
        names = [v.strip() for v in arrays["RotationCrops"][index].split("+")]
        if not set(names) <= {"RedClover", "PlantainForage", "Oats", "Wheat", "Rape", "Mustard"}:
            raise ValueError("Unsupported crop in annual cleanup rotation")
        values = {key: [v.strip() for v in arrays[key][index].split(";")] for key in keys[1:]}
        if any(len(v) != len(names) for v in values.values()):
            raise ValueError("Mismatched crop component arrays")
        declared = [dict(crop=n, cultivar=values["RotationCultivars"][j],
                         population_m2=float(values["RotationPopulations"][j]),
                         depth_mm=float(values["RotationSowingDepths"][j]),
                         row_spacing_mm=float(values["RotationRowSpacings"][j])) for j, n in enumerate(names)]
        sown = [e for e in events if e["event"] == "sow" and int(e["date"][:4]) == year]
        if {e["crop"] for e in sown} != set(names) or len({e["date"] for e in sown}) != 1:
            raise ValueError(f"Missing, repeated or partial sowing in {field['id']} {year}; inspect APSIM messages")
        for component in declared:
            actual = next(e for e in sown if e["crop"] == component["crop"])
            if actual["cultivar"] != component["cultivar"] or actual["population_m2"] != component["population_m2"]:
                raise ValueError("Actual source sowing differs from rotation parameters")
        annuals = [n for n in names if n not in ["RedClover", "PlantainForage"]]
        main_crops = {n.lower() for n in annuals} if annuals else {n.lower() for n in names}
        harvest_events = [e for e in events if e["event"] == "harvest" and int(e["date"][:4]) == year and e["crop"].lower() in main_crops]
        harvest_dates = sorted({e["date"] for e in harvest_events})
        if len(harvest_dates) != 1 or {e["crop"].lower() for e in harvest_events} != main_crops:
            raise ValueError(f"Missing or multiple primary harvest dates in {field['id']} {year}")
        harvest_date = harvest_dates[0]
        rows.append(dict(year=year, declared_components=declared, active_perennials=[c for c in declared if c["crop"] in ["RedClover", "PlantainForage"]],
                         preceding_perennials=None, preceding_establishment_year=None, establishment_year=year,
                         sowing_date=sown[0]["date"], harvest_dates=[harvest_date], source_harvest_crops=names,
                         termination_date=f"{year}-12-31", cultivar_application="resown every year; cultivar reapplied"))
    return start, end, rows


def annual_cleanup_segments(field, start, end, rotation):
    segments = []
    for row in rotation:
        year = row["year"]
        annuals = [c for c in row["declared_components"] if c["crop"] not in ["RedClover", "PlantainForage"]]
        if len(annuals) > 1:
            raise ValueError("Multiple grain crops in one effective crop require an explicit yield allocation")
        sow = date.fromisoformat(row["sowing_date"])
        if annuals:
            main = annuals[0]
            profile = "OatsForageMix_Yakov" if main["crop"] == "Oats" and row["active_perennials"] else f"{main['crop']}_{main['cultivar']}"
            kind, column = "grain", f"{main['crop']}Yield"
            crop_start, crop_end = sow, date.fromisoformat(row["harvest_dates"][0])-timedelta(days=1)
        else:
            profile = "ForageMix_Second" if any(c["cultivar"].startswith("Second_") for c in row["declared_components"]) else "ForageMix_First"
            kind, column = "forage", "MixYield"
            crop_start = max(sow, date.fromisoformat(f"{year}-{field['forage_season']['start']}"))
            crop_end = date.fromisoformat(f"{year}-{field['forage_season']['end']}")
        if crop_start >= crop_end:
            raise ValueError("Realized sowing is after the effective growing window")
        segments.append(dict(id=f"{year}_{profile}", year=year, kind=kind, source_yield_column=column,
                             simulation_start=max(start,date(year,1,1)).isoformat(), simulation_end=min(end,date(year,12,31)).isoformat(),
                             crop_start=crop_start.isoformat(), crop_end=crop_end.isoformat(), components=row["declared_components"],
                             cultivation_year=1, catalog_crop=profile, harvest_dates=row["harvest_dates"], reset_assimilates=True,
                             source_sowing_date=sow.isoformat(), source_termination_date=row["termination_date"]))
    return segments


def day_number(value):
    return (value - date(1901, 1, 1)).days + 1


def parse_day_month(text, year):
    months = {name: index + 1 for index, name in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
    day, month = text.strip().split("-")
    return date(year, months[month.title()], int(day))


def rotation_rows(simulation, zone):
    clock = unique_node(list(walk(simulation)), kind="Models.Clock, Models")
    start, end = (date.fromisoformat(clock[key][:10]) for key in ["Start", "End"])
    manager = unique_node(list(walk(zone)), name="Sow using a variable rule")
    parameters = {v["Key"]: v["Value"] for v in manager["Parameters"]}
    if parameters.get("KeepPerennialsUnderCover", "False").lower() != "false":
        raise ValueError("KeepPerennialsUnderCover=True requires a reviewed carry-over transition")
    if parameters.get("KillPerennialsDate", "-") != "-":
        raise ValueError("A separate perennial kill date requires an explicit simulation segment")
    keys = ["RotationCrops", "RotationCultivars", "RotationSowingDepths", "RotationRowSpacings",
            "RotationPopulations", "RotationSowingDates", "RotationHarvestDates", "RotationHarvestCrops"]
    arrays = {key: [v.strip() for v in parameters[key].split(",")] for key in keys}
    years = list(range(start.year, end.year + 1))
    if any(len(values) != len(years) for values in arrays.values()):
        raise ValueError("Rotation arrays must cover every source year")
    rows = []
    active = None
    establishment_year = None
    for index, year in enumerate(years):
        names = arrays["RotationCrops"][index].split("+")
        cultivars = arrays["RotationCultivars"][index].split(";")
        pops = [float(v) for v in arrays["RotationPopulations"][index].split(";")]
        depths = [float(v) for v in arrays["RotationSowingDepths"][index].split(";")]
        spacings = [float(v) for v in arrays["RotationRowSpacings"][index].split(";")]
        if not len(names) == len(cultivars) == len(pops) == len(depths) == len(spacings):
            raise ValueError(f"Crop parameter arrays differ in {year}")
        if not set(names) <= {"RedClover", "PlantainForage", "Oats"}:
            raise ValueError("This catalog supports clover, PlantainForage and oats only; add a reviewed crop profile")
        declared = [dict(crop=n, cultivar=c.strip(), population_m2=p, depth_mm=d, row_spacing_mm=s)
                    for n, c, p, d, s in zip(names, cultivars, pops, depths, spacings)]
        sow_text = arrays["RotationSowingDates"][index]
        has_annual = "Oats" in names
        previous = active
        previous_establishment = establishment_year
        if active is None or has_annual:
            active = [c for c in declared if c["crop"] != "Oats"]
            establishment_year = year
        # The source manager does not reapply cultivar names/populations on perennial carry-over.
        row = dict(year=year, declared_components=declared, active_perennials=active,
                   preceding_perennials=previous, preceding_establishment_year=previous_establishment,
                   establishment_year=establishment_year,
                   sowing_date=parse_day_month(sow_text, year).isoformat() if sow_text != "-" else None,
                   source_harvest_crops=arrays["RotationHarvestCrops"][index].split("+"),
                   harvest_dates=[parse_day_month(v, year).isoformat() for v in arrays["RotationHarvestDates"][index].split(";") if v != "-"],
                   cultivar_application="sown" if has_annual or index == 0 else "carried stand; year label not reapplied")
        rows.append(row)
    return start, end, rows


def build_segments(field, start, end, rotation):
    segments = []
    for row in rotation:
        year = row["year"]
        spring = date.fromisoformat(f"{year}-{field['forage_season']['start']}")
        autumn = date.fromisoformat(f"{year}-{field['forage_season']['end']}")
        first, last = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))

        def forage_segment(suffix, a, b, components, establishment, cuts, reset=False):
            if a > b:
                return
            crop_start, crop_end = max(a, spring), min(b, autumn)
            if crop_start > crop_end:
                raise ValueError("A forage segment must include an active growing window")
            if any(not crop_start <= date.fromisoformat(cut) <= crop_end for cut in cuts):
                raise ValueError("Forage harvest is outside the configured growing window")
            profile = "ForageMix_Second" if any(c["cultivar"].startswith("Second_") for c in components) else "ForageMix_First"
            segments.append(dict(id=f"{year}_{suffix}", year=year, kind="forage",
                                 simulation_start=a.isoformat(), simulation_end=b.isoformat(),
                                 crop_start=crop_start.isoformat(), crop_end=crop_end.isoformat(),
                                 components=components, cultivation_year=year-establishment+1,
                                 catalog_crop=profile, harvest_dates=cuts, reset_assimilates=reset))

        if row["sowing_date"]:
            sow = date.fromisoformat(row["sowing_date"])
            if len(row["harvest_dates"]) != 1 or row["source_harvest_crops"] != ["Oats"]:
                raise ValueError("The reviewed oats-understorey transition requires one oats-only harvest")
            harvest = date.fromisoformat(row["harvest_dates"][0])
            if row["preceding_perennials"]:
                forage_segment("forage_before_sowing", first, sow-timedelta(days=1),
                               row["preceding_perennials"], row["preceding_establishment_year"], [])
            segments.append(dict(id=f"{year}_oats_with_understorey", year=year, kind="grain",
                                 simulation_start=sow.isoformat(), simulation_end=(harvest-timedelta(days=1)).isoformat(),
                                 crop_start=sow.isoformat(), crop_end=(harvest-timedelta(days=1)).isoformat(),
                                 components=row["declared_components"], cultivation_year=1,
                                 catalog_crop="OatsForageMix_Yakov", harvest_dates=[harvest.isoformat()],
                                 reset_assimilates=True))
            forage_segment("understorey_after_oats", harvest, last, row["active_perennials"],
                           row["establishment_year"], [], reset=True)
        else:
            forage_segment("forage", first, last, row["active_perennials"], row["establishment_year"], row["harvest_dates"])
    for a, b in zip(segments, segments[1:]):
        if date.fromisoformat(a["simulation_end"]) + timedelta(days=1) != date.fromisoformat(b["simulation_start"]):
            raise ValueError("Segment coverage has a gap or overlap")
    return segments


def source_weather(field):
    rows, latitude = [], None
    for line in local_path(field["weather_file"]).read_text(encoding="utf-8").splitlines():
        if line.startswith("latitude"):
            latitude = float(line.split("=")[1])
        parts = line.split()
        if len(parts) >= 6 and parts[0].isdigit():
            year, day = map(int, parts[:2])
            when = date(year, 1, 1) + timedelta(days=day-1)
            radn, tmax, tmin, rain = map(float, parts[2:6])
            if when.year != year or tmin > tmax or min(radn, rain) < 0:
                raise ValueError(f"Invalid source weather: {line}")
            rows.append(dict(date=when, radn=radn, tmax=tmax, tmin=tmin, rain=rain))
    if latitude is None or not rows or any(b["date"]-a["date"] != timedelta(days=1) for a, b in zip(rows, rows[1:])):
        raise ValueError("Source weather must be dated, complete and consecutive")
    for row in rows:
        row["eto"] = hargreaves_eto(row, latitude)
    return latitude, rows


def management_text(field, segment):
    template = (ROOT / "DATA/Management.MAN").read_text(encoding="utf-8")
    lines = template.splitlines()[:20]
    cuts = segment["harvest_dates"] if segment["kind"] == "forage" else []
    text = replace_values("\n".join(lines), {
        0: f"{segment['id']} - effective mixture; explicit scheduled cuts",
        2: field["mulch_cover_percent"], 4: field["fertility_stress_percent"],
        12: int(bool(cuts)), 18: 0, 19: 0,
    })
    origin = date.fromisoformat(segment["crop_start"])
    text += f"{day_number(origin):12} : Cutting schedule origin\n\nHarvest Day\n============\n"
    text += "".join(f"{(date.fromisoformat(cut)-origin).days+1}\n" for cut in cuts)
    return text


def project_run(segment, first):
    def reference(title, filename, directory="DATA"):
        return [title, filename, directory if filename != "(None)" else "(None)"]
    s = segment
    lines = [str(s["cultivation_year"]), str(day_number(date.fromisoformat(s["simulation_start"]))),
             str(day_number(date.fromisoformat(s["simulation_end"]))),
             str(day_number(date.fromisoformat(s["crop_start"]))), str(day_number(date.fromisoformat(s["crop_end"])))]
    for title, filename in [
        ("Climate", "Weather.CLI"), ("Temperature", "Weather.Tnx"), ("Reference ET", "Weather.ETo"),
        ("Rain", "Weather.PLU"), ("CO2", "MaunaLoa.CO2"), ("Calendar", "(None)"),
        ("Crop", f"{s['id']}.CRO"), ("Irrigation", "(None)"), ("Management", f"{s['id']}.MAN"),
        ("Soil", "Soil.SOL"), ("Groundwater", "(None)"),
        ("Initial conditions", "(None)" if first else "KeepSWC"),
        ("Off season", "(None)"), ("Observations", "(None)"),
    ]:
        lines += reference(title, filename, "Keep previous soil state" if filename == "KeepSWC" else "DATA")
    if len(lines) != 47:
        raise ValueError("AquaCrop run block must contain 47 lines")
    return lines


def prepare_field(field, catalog):
    _, simulation, zone = source_parts(field)
    if field.get("management_mode") == "planned_rotation":
        from .planned_rotation import planned_rotation
        start, end, rotation, segments = planned_rotation(field, simulation, zone, catalog)
    elif field.get("management_mode") == "fixed_rotation":
        from .fixed_rotation import fixed_rotation, extend_fixed_catalog
        extend_fixed_catalog(catalog)
        start, end, rotation, segments = fixed_rotation(field, simulation, zone)
    elif field.get("management_mode") == "annual_cleanup":
        start, end, rotation = annual_cleanup_rotation(field, simulation, zone)
        segments = annual_cleanup_segments(field, start, end, rotation)
    else:
        start, end, rotation = rotation_rows(simulation, zone)
        segments = build_segments(field, start, end, rotation)
    physical = unique_node(list(walk(zone)), kind="Models.Soils.Physical, Models")
    water = unique_node(list(walk(zone)), kind="Models.Soils.Water, Models")
    swim = unique_node(list(walk(zone)), name="Swim3")
    if physical["Thickness"] != water["Thickness"]:
        raise ValueError("Initial-water and physical layer boundaries differ")
    latitude, weather = source_weather(field)
    if weather[0]["date"] > start or weather[-1]["date"] < end:
        raise ValueError("Weather does not cover the complete source simulation")
    root = ROOT / "generated" / field["id"]
    data = root / "DATA"
    for folder in [data, root / "LIST", root / "PARAM"]:
        folder.mkdir(parents=True, exist_ok=True)
    soil = [f"{field['id']} - physical APSIM source values", "7.3 : AquaCrop version",
            f"{round(swim['CN2Bare'])} : Curve number", "2 : REW, provisional mm", f"{len(physical['Thickness'])} : Layers",
            "-9 : Obsolete", "Thickness SAT FC WP KS Penetrability Gravel CRa CRb Description", "m vol% mm/day %"]
    for i, thickness in enumerate(physical["Thickness"]):
        soil.append(f"{thickness/1000:.8f} {physical['SAT'][i]*100:.8f} {physical['DUL'][i]*100:.8f} "
                    f"{physical['LL15'][i]*100:.8f} {physical['KS'][i]:.8f} 100 0 -0.390600 1.255639 layer_{i+1}")
    (data / "Soil.SOL").write_text("\n".join(soil)+"\n", encoding="utf-8")
    first_weather = weather[0]["date"]
    for filename, title, values in [
        ("Weather.Tnx", "Source Tmin Tmax (degC)", [f"{r['tmin']:.3f} {r['tmax']:.3f}" for r in weather]),
        ("Weather.PLU", "Source precipitation (mm/day)", [f"{r['rain']:.3f}" for r in weather]),
        ("Weather.ETo", "FAO56 Hargreaves reference ET (mm/day)", [f"{r['eto']:.5f}" for r in weather]),
    ]:
        (data / filename).write_text(weather_text(first_weather, title, values), encoding="utf-8")
    shutil.copyfile(local_path(field["co2_file"]), data / "MaunaLoa.CO2")
    (data / "Weather.CLI").write_text("Source weather\n7.3\nWeather.Tnx\nWeather.ETo\nWeather.PLU\nMaunaLoa.CO2\n", encoding="utf-8")
    for segment in segments:
        base = local_path(catalog[segment["catalog_crop"]]["file"]).read_text(encoding="utf-8")
        population = sum(c["population_m2"] for c in segment["components"])
        wp = sum(c["population_m2"] * (18 if c["crop"] == "RedClover" else 15) for c in segment["components"])/population
        nominal = catalog[segment["catalog_crop"]]["values"]["water_productivity"]
        current = float(base.splitlines()[CROP_FIELDS["water_productivity"]].split()[0])
        values = dict(population_per_ha=round(population*10000), water_productivity=current*wp/nominal)
        if field.get("management_mode") in ["annual_cleanup", "fixed_rotation", "planned_rotation"]:
            duration = (date.fromisoformat(segment["crop_end"])-date.fromisoformat(segment["crop_start"])).days+1
            values.update(maturity=duration, maximum_rooting=max(10, round(duration*.75)),
                          senescence=max(10, round(duration*.85)))
            if segment["kind"] == "grain":
                values.update(emergence=min(8, duration//8), flowering=round(duration*.55),
                              harvest_index_days=round(duration*.35), flowering_length=7)
        if segment["id"].endswith("understorey_after_oats"):
            values["sown_in_first_year"] = 0
        text = crop_text(base, f"{field['id']} {segment['id']} - effective mixture, uncalibrated", values)
        (data / f"{segment['id']}.CRO").write_text(text, encoding="utf-8")
        (data / f"{segment['id']}.MAN").write_text(management_text(field, segment), encoding="utf-8")
    prm = [f"{field['id']} - source rotation, effective mixtures", "7.3 : AquaCrop version"]
    for i, segment in enumerate(segments):
        prm += project_run(segment, i == 0)
    (root / "LIST/Field.PRM").write_text("\n".join(prm)+"\n", encoding="utf-8")
    (root / "LIST/ListProjects.txt").write_text("Field.PRM\n", encoding="utf-8")
    shutil.copyfile(ROOT / "DATA/Simulation.PPn", root / "PARAM/Field.PPn")
    plan = dict(workspace_root=str(ROOT), field_id=field["id"],
                aqua_root=f"generated/{field['id']}", project_file="LIST/Field.PRM",
                output_dir=f"outputs/{field['id']}", initial_water=water["InitialValues"],
                maximum_compartment_thickness_m=field["maximum_compartment_thickness_m"],
                runs=[dict(id=s["id"], reset_assimilates=s["reset_assimilates"]) for s in segments])
    save_json(root / "plan.json", plan)
    source_cultivars = {n["Name"]: n["Command"] for n in walk(zone) if n.get("$type") == "Models.PMF.Cultivar, Models"}
    save_json(root / "source_mapping.json", dict(field=field, period_start=start.isoformat(), period_end=end.isoformat(),
               area_ha=zone.get("Area"), latitude=latitude, soil_layer_count=len(physical["Thickness"]),
               source_rotation=rotation, segments=segments,
               source_cultivar_commands=source_cultivars,
               source_soil_crop_parameters=[n for n in walk(physical) if n.get("$type") == "Models.Soils.SoilCrop, Models"],
               source_unmapped_soil_blocks=[{k:v for k,v in n.items() if k != "Children"}
                   for n in walk(zone) if n.get("$type") in ["Models.Soils.Chemical, Models",
                       "Models.Soils.Organic, Models", "Models.Soils.Solute, Models"]],
               source_management=[{k:v for k,v in n.items() if k in ["Name", "Parameters", "Enabled"]}
                                  for n in walk(zone) if n.get("$type") == "Models.Manager, Models"],
               approximation="uncalibrated effective crops; continuous water/salt state; biological stand reconstructed between effective crop windows; fixed source sowing and harvest dates replayed; cultivar vernalization/photoperiod, exact winter dieback and species competition are not reproduced"))
    return root / "plan.json"
