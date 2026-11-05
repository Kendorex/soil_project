"""Adapt the reviewed fixed-date RotationManager without changing its source project."""

from datetime import date, timedelta
from .field_config import ROOT, CROP_FIELDS, crop_text, load_json, local_path, parse_day_month, save_json, unique_node, walk

PERENNIALS = {"RedClover", "PlantainForage"}


def extend_fixed_catalog(catalog):
    """Validate fixed-rotation aliases without inventing missing crop inputs."""
    required = {"ForageMix_Local", "RedClover_LocalClover", "PlantainForage_TimothyProxy",
                "Oats_Argamak", "OatsForageMix_Argamak", "Wheat_Irgina", "OatsClover_Green"}
    missing = required - set(catalog)
    if missing:
        raise ValueError(f"Missing fixed-rotation crop profiles: {sorted(missing)}")
    return catalog


def fixed_rotation(field, simulation, zone):
    clock = unique_node(list(walk(simulation)),kind="Models.Clock, Models")
    start,end = (date.fromisoformat(clock[k][:10]) for k in ["Start","End"])
    manager = unique_node(list(walk(zone)),name="RotationManager")
    parameters = {p["Key"]:p["Value"] for p in manager["Parameters"]}
    keys = ["RotationCrops","RotationCultivars","RotationSowingDepths","RotationRowSpacings","RotationPopulations",
            "RotationSowingDates","RotationHarvestDates","RotationHarvestCrops","RotationUses","PastureAges"]
    arrays = {k:[v.strip() for v in parameters[k].split(",")] for k in keys}
    if any(len(v)!=end.year-start.year+1 for v in arrays.values()):
        raise ValueError("Fixed rotation must cover all harvest years")
    schedule = load_json(ROOT / "management" / f"{field['id']}.json")
    events = schedule["events"]
    source_harvests = schedule["harvests"]
    rows, active, intervals = [], {}, []
    for i,year in enumerate(range(start.year,end.year+1)):
        names = [v.strip() for v in arrays["RotationCrops"][i].split("+")]
        specs = {k:[v.strip() for v in arrays[k][i].split(";")] for k in keys[1:5]}
        if any(len(v)!=len(names) for v in specs.values()):
            raise ValueError("Fixed rotation mixture component counts differ")
        declared = [dict(crop=n,cultivar=specs["RotationCultivars"][j],population_m2=float(specs["RotationPopulations"][j]),
                         depth_mm=float(specs["RotationSowingDepths"][j]),row_spacing_mm=float(specs["RotationRowSpacings"][j])) for j,n in enumerate(names)]
        use = arrays["RotationUses"][i]
        harvest = parse_day_month(arrays["RotationHarvestDates"][i],year)
        native = [h for h in source_harvests if h["Date"][:10]==harvest.isoformat()]
        if len(native)!=1 or native[0]["Use"]!=use or native[0]["Crop"]!=arrays["RotationHarvestCrops"][i]:
            raise ValueError("Native harvest event disagrees with source schedule")
        annual = next((c for c in declared if c["crop"] not in PERENNIALS),None)
        if use=="hay":
            # Carry the actual sown cultivar and population, not the year-label declaration.
            for component in declared:
                previous = [e for e in events if e["event"]=="sow" and e["crop"]==component["crop"] and e["date"]<=harvest.isoformat()]
                if not previous:
                    raise ValueError("Hay has no actual source establishment")
                establishment = max(previous,key=lambda e:e["date"])
                component = next((c for c in active.values() if c["crop"]==component["crop"] and c["cultivar"]==establishment["cultivar"]),component)
                active[component["crop"]] = component
            components = [active[n] for n in names]
            sow = min(max((e["date"] for e in events if e["event"]=="sow" and e["crop"]==n and e["date"]<=harvest.isoformat())) for n in names)
            crop_start = max(date.fromisoformat(sow),date.fromisoformat(f"{year}-{field['forage_season']['start']}"))
            crop_end = date.fromisoformat(f"{year}-{field['forage_season']['end']}")
            terminations = [date.fromisoformat(e["date"]) for e in events if e["event"]=="end" and e["crop"] in PERENNIALS and harvest.isoformat()<e["date"]<=f"{year}-12-31"]
            if terminations:crop_end=min(crop_end,min(terminations)-timedelta(days=1))
            profile,kind,column = "ForageMix_Local","forage","MixYield"
        else:
            expected = start if i==0 and "Winter_rye" in annual["cultivar"] else parse_day_month(arrays["RotationSowingDates"][i],year-("Winter_rye" in annual["cultivar"]))
            matches = [e for e in events if e["event"]=="sow" and e["crop"]==annual["crop"] and e["date"]==expected.isoformat() and e["cultivar"]==annual["cultivar"]]
            if len(matches)!=1:raise ValueError(f"Missing actual sowing for {field['id']} {year}")
            sow = expected.isoformat()
            components = declared
            crop_start = expected
            crop_end = harvest-timedelta(days=1) if use=="grain" else harvest
            if use=="green":profile,kind,column="OatsClover_Green","forage","GreenYield"
            else:
                profile = f"OatsForageMix_{annual['cultivar']}" if annual["crop"]=="Oats" and len(names)>1 else f"{annual['crop']}_{annual['cultivar']}"
                kind,column = "grain",f"{annual['crop']}Yield"
            active = {c["crop"]:c for c in declared if c["crop"] in PERENNIALS} if use!="green" else {}
            if active:
                for n in active:
                    if not any(e["event"]=="sow" and e["crop"]==n and e["date"]==(expected+timedelta(days=1)).isoformat() for e in events):
                        raise ValueError("Undersowing must occur one day after the cover crop")
        rows.append(dict(year=year,declared_components=declared,active_perennials=list(active.values()),sowing_date=sow,
                         harvest_dates=[harvest.isoformat()],source_harvest_crops=arrays["RotationHarvestCrops"][i].split("+"),
                         harvest_use=use,pasture_age=int(arrays["PastureAges"][i]),
                         cultivar_application="source sowing only; actual stand retained across hay years"))
        establishment_year = int(sow[:4])
        intervals.append(dict(id=f"{year}_{profile}",year=year,kind=kind,source_yield_column=column,harvest_use=use,
                              crop_start=crop_start.isoformat(),crop_end=crop_end.isoformat(),components=components,
                              cultivation_year=1 if kind=="grain" or use=="green" else max(1,year-establishment_year+1),
                              catalog_crop=profile,harvest_dates=[harvest.isoformat()],source_sowing_date=sow,
                              reset_assimilates=use!="hay" or i==0 or arrays["RotationUses"][i-1]!="hay"))
        if use=="grain" and active:
            # The effective grain stand conceals understory competition until harvest.
            a,b=harvest,date.fromisoformat(f"{year}-{field['forage_season']['end']}")
            if a<=b:
                intervals.append(dict(id=f"{year}_understorey",year=year,kind="forage",source_yield_column="MixYield",harvest_use="understorey",
                                      crop_start=a.isoformat(),crop_end=b.isoformat(),components=list(active.values()),cultivation_year=1,
                                      catalog_crop="ForageMix_Local",harvest_dates=[],source_sowing_date=(date.fromisoformat(sow)+timedelta(days=1)).isoformat(),reset_assimilates=True))
    intervals.sort(key=lambda s:s["crop_start"])
    for i,s in enumerate(intervals):
        if i and intervals[i-1]["crop_end"]>=s["crop_start"]:
            raise ValueError("Effective crop intervals overlap")
        s["simulation_start"] = start.isoformat() if i==0 else s["crop_start"]
        s["simulation_end"] = (date.fromisoformat(intervals[i+1]["crop_start"])-timedelta(days=1)).isoformat() if i+1<len(intervals) else end.isoformat()
    return start,end,rows,intervals
