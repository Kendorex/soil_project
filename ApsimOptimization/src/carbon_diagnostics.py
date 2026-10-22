"""Shared read-only carbon diagnostics; never runs APSIM or NSGA-II.

The reported quantity is sum([Nutrient].Organic.C) over the whole model profile,
including fresh organic matter. It is NOT measured humus or topsoil SOC.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path


CARBON_EXPRESSION = "sum([Nutrient].Organic.C) as OrganicC"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _kind(node):
    return node.get("$type", "").split(",")[0]


def _children(node, kind):
    return [n for n in node.get("Children", [])
            if _kind(n) == kind and n.get("Enabled", True)]


def _one(nodes, label):
    if len(nodes) != 1:
        raise ValueError(f"{label}: expected one item, got {len(nodes)}")
    return nodes[0]


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: missing numeric value")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label}: expected finite non-negative value")
    return result


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def read_carbon_run(model_path, database_path, weather_path, years, fields):
    """Validate identity/coverage and extract year ends from existing SQLite.

    The period starts at EndOfDay on 31 December before the first evaluated
    year; thus neither an APSIM initial value nor 2019 warm-up is counted as
    carbon change during 2020-2023. No missing date is interpolated.
    """
    years = list(years)
    if (not years or any(type(y) is not int for y in years)
            or years != list(range(years[0], years[-1] + 1))):
        raise ValueError("Evaluation years must be consecutive increasing integers")
    if not fields:
        raise ValueError("No evaluation fields")
    model_path, database_path, weather_path = map(
        lambda p: Path(p).resolve(), (model_path, database_path, weather_path))
    model = load_json(model_path)
    sim = _one(_children(model, "Models.Core.Simulation"), "simulation")
    clock = _one(_children(sim, "Models.Clock"), "clock")
    zones = {z["Name"]: z for z in _children(sim, "Models.Core.Zone")}
    if set(zones) != set(fields):
        raise ValueError("Configured fields differ from enabled model zones")
    begin = dt.date(years[0] - 1, 12, 31)
    end = dt.date(years[-1], 12, 31)
    expected_dates = [(begin + dt.timedelta(days=i)).isoformat()
                      for i in range((end - begin).days + 1)]
    result = {
        "evaluation_years": years,
        "period_start": begin.isoformat(), "period_end": end.isoformat(),
        "source": {"model": str(model_path), "database": str(database_path),
                   "model_sha256": fingerprint(model_path),
                   "database_sha256": fingerprint(database_path),
                   "database_wal_sha256": (fingerprint(str(database_path) + "-wal")
                                           if Path(str(database_path) + "-wal").is_file() else None),
                   "weather_sha256": fingerprint(weather_path)},
        "context": {"simulation": sim["Name"],
                    "clock_start": clock["Start"], "clock_end": clock["End"],
                    "weather_sha256": fingerprint(weather_path)},
        "fields": {},
    }
    # mode=ro deliberately honours an existing WAL; immutable=1 would ignore it.
    with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        sims = db.execute('SELECT ID FROM _Simulations WHERE Name=?',
                          (sim["Name"],)).fetchall()
        sim_id = _one(sims, "database simulation")["ID"]
        checkpoint = _one(db.execute(
            "SELECT ID FROM _Checkpoints WHERE Name='Current'").fetchall(),
            "Current checkpoint")["ID"]
        if db.execute("SELECT 1 FROM _Messages WHERE SimulationID=? "
                      "AND CheckpointID=? AND MessageType=0 LIMIT 1",
                      (sim_id, checkpoint)).fetchone():
            raise ValueError("Database contains APSIM errors")
        initial = {(r["ModelPath"], r["Name"]): r["Value"] for r in db.execute(
            "SELECT ModelPath,Name,Value FROM _InitialConditions "
            "WHERE SimulationID=? AND CheckpointID=?", (sim_id, checkpoint))}
        versions = [value for (_, name), value in initial.items()
                    if name == "APSIM version"]
        result["context"]["apsim_version"] = _one(versions, "APSIM version")
        for field, settings in fields.items():
            zone = zones[field]
            soil = _one(_children(zone, "Models.Soils.Soil"), f"{field}/soil")
            physical = _one(_children(soil, "Models.Soils.Physical"), "physical")
            organic = _one(_children(soil, "Models.Soils.Organic"), "organic")
            thickness = physical["Thickness"]
            if (not thickness or thickness != organic["Thickness"]
                    or any(_number(t, "thickness") <= 0 for t in thickness)):
                raise ValueError(f"{field}: incompatible soil layers")
            for node, names in ((physical, ("BD",)), (organic, ("Carbon",))):
                raw_depth = initial.get((f'{field}.Soil.{node["Name"]}', "Depth"))
                edges = [tuple(float(v) for v in layer.split("-"))
                         for layer in str(raw_depth).split(",")]
                expected_edges = []
                top = 0.0
                for layer in node["Thickness"]:
                    expected_edges.append((top, top + layer))
                    top += layer
                if edges != expected_edges:
                    raise ValueError(f"{field}: database/model mismatch for layer depths")
                for name in names:
                    raw = initial.get((f'{field}.Soil.{node["Name"]}', name))
                    values = [float(v) for v in str(raw).split(",")]
                    if values != node[name]:
                        raise ValueError(f"{field}: database/model mismatch for {name}")
            report = _one([r for r in _children(zone, "Models.Report")
                           if CARBON_EXPRESSION in r.get("VariableNames", [])],
                          f"{field}/OrganicC report")
            if report.get("EventNames") != ["[Clock].EndOfDay"]:
                raise ValueError(f"{field}: carbon report is not daily EndOfDay")
            report_path = f'{field}.{report["Name"]}'
            if CARBON_EXPRESSION not in str(initial.get((report_path, "VariableNames"))):
                raise ValueError(f"{field}: database carbon expression is unverified")
            if initial.get((report_path, "EventNames")) != "[Clock].EndOfDay":
                raise ValueError(f"{field}: database report frequency is unverified")
            table = _quote(report["Name"])
            rows = db.execute(
                f'SELECT "Clock.Today",OrganicC,Zone FROM {table} '
                'WHERE SimulationID=? AND CheckpointID=? '
                'AND "Clock.Today">=? AND "Clock.Today"<? ORDER BY "Clock.Today"',
                (sim_id, checkpoint, begin.isoformat(),
                 (end + dt.timedelta(days=1)).isoformat())).fetchall()
            dates = [dt.datetime.fromisoformat(r["Clock.Today"]).date().isoformat()
                     for r in rows]
            if dates != expected_dates:
                raise ValueError(f"{field}: incomplete or duplicate daily carbon output")
            if any(r["Zone"] != field for r in rows):
                raise ValueError(f"{field}: report belongs to another zone")
            carbon = {day: _number(r["OrganicC"], f"{field}/{day}/OrganicC")
                      for day, r in zip(dates, rows)}
            manager = _one([n for n in zone.get("Children", [])
                            if n.get("Name") == "Sow using a variable rule"],
                           f"{field}/rotation manager")
            params = {p["Key"]: p["Value"] for p in manager["Parameters"]}
            area = _number(settings["area_ha"], f"{field}/area")
            if area <= 0:
                raise ValueError(f"{field}: area must be positive")
            result["fields"][field] = {
                "area_ha": area, "profile_depth_mm": sum(thickness),
                "soil_sha256": hashlib.sha256(json.dumps(
                    soil, sort_keys=True).encode()).hexdigest(),
                "rotation_crops": params["RotationCrops"],
                "carbon_start_kg_ha": carbon[begin.isoformat()],
                "carbon_end_kg_ha": carbon[end.isoformat()],
                "carbon_change_kg_ha": carbon[end.isoformat()] - carbon[begin.isoformat()],
                "annual_end_kg_ha": {str(y): carbon[f"{y}-12-31"] for y in years},
                "report": report["Name"],
            }
            from .carbon_reporting import read_carbon_layers
            try:
                details = read_carbon_layers(
                    db, sim_id, checkpoint, initial, zone, thickness, carbon, years)
            except (ValueError, KeyError, IndexError, sqlite3.Error) as exc:
                details = {"status": "not_assessed", "reason": str(exc)}
            result["fields"][field]["details"] = details
    return result


def read_humic_period(model, database, weather, years, fields):
    """Read verified endpoints used by the real optimizer constraint."""
    try:
        run = read_carbon_run(model, database, weather, years, fields)
        values = {}
        for field, row in run["fields"].items():
            details = row.get("details") or {}
            if details.get("status") != "checked":
                raise ValueError(f'{field}: {details.get("reason", "verified Humic layers are missing")}')
            values[field] = {
                "start_kg_ha": math.fsum(l["start"]["CHumic"] for l in details["layers"]),
                "end_kg_ha": math.fsum(l["end"]["CHumic"] for l in details["layers"]),
            }
        return {"status": "checked", "period_start": run["period_start"],
                "period_end": run["period_end"], "fields": values,
                "source": run["source"]}
    except (ValueError, OSError, KeyError, IndexError, sqlite3.Error) as exc:
        return {"status": "not_assessed", "reason": str(exc), "fields": {}}


def compare_carbon(candidate, baseline):
    """Explicit experimental endpoint rule, with no rounding or tolerance.

    Differences smaller than displayed precision still remain differences.
    A farm-wide gain cannot compensate a deficit on an individual field.
    """
    for key in ("evaluation_years", "period_start", "period_end", "context"):
        if candidate[key] != baseline[key]:
            raise ValueError(f"Incompatible candidate and baseline: {key}")
    if set(candidate["fields"]) != set(baseline["fields"]):
        raise ValueError("Candidate and baseline field sets differ")
    checks = []
    for field, row in candidate["fields"].items():
        ref = baseline["fields"][field]
        for key in ("area_ha", "profile_depth_mm", "soil_sha256", "rotation_crops"):
            if row[key] != ref[key]:
                raise ValueError(f"{field}: incompatible {key}")
        difference = row["carbon_end_kg_ha"] - ref["carbon_end_kg_ha"]
        checks.append({
            "field": field, **row,
            "baseline_start_kg_ha": ref["carbon_start_kg_ha"],
            "baseline_end_kg_ha": ref["carbon_end_kg_ha"],
            "baseline_change_kg_ha": ref["carbon_change_kg_ha"],
            "start_difference_kg_ha": row["carbon_start_kg_ha"] - ref["carbon_start_kg_ha"],
            "end_difference_kg_ha": difference,
            "change_difference_kg_ha": row["carbon_change_kg_ha"] - ref["carbon_change_kg_ha"],
            "annual_difference_kg_ha": {
                y: value - ref["annual_end_kg_ha"][y]
                for y, value in row["annual_end_kg_ha"].items()},
            "passed": difference >= 0,
        })
        from .carbon_reporting import compare_carbon_layers
        checks[-1]["detail_comparison"] = compare_carbon_layers(
            row.get("details") or {}, ref.get("details") or {})
    return {
        "status": "checked", "passes_carbon_rule": all(c["passed"] for c in checks),
        "failed_fields": [c["field"] for c in checks if not c["passed"]],
        "worst_field_difference_kg_ha": min(c["end_difference_kg_ha"] for c in checks),
        "farm_end_difference_kg": sum(c["end_difference_kg_ha"] * c["area_ha"] for c in checks),
        "farm_carbon_change_kg": sum(c["carbon_change_kg_ha"] * c["area_ha"] for c in checks),
        "checks": checks,
    }


def diagnose_carbon(model, database, weather, years, fields, baseline_summary=None):
    """Attach factual carbon diagnostics to a regular manual-run analysis.

    There is no threshold, feasibility decision or F/G modification here.
    Missing C cannot erase otherwise valid N/yield/economic results. A legacy
    baseline without carbon is explicitly unassessed, never reconstructed.
    """
    result = {
        "metric": "sum([Nutrient].Organic.C)", "unit": "kg C/ha",
        "interpretation": "model whole-profile carbon including fresh organic matter; not measured humus",
        "affects_objectives_or_constraints": False,
        "normative_humus_assessment": "not_assessed",
    }
    try:
        run = read_carbon_run(model, database, weather, years, fields)
    except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
        return {**result, "status": "not_assessed", "reason": str(exc)}
    result.update(status="checked", run=run)
    from .carbon_reporting import summarize_carbon_pools
    result["pool_summary"] = summarize_carbon_pools(run["fields"])
    if baseline_summary is None:
        result["comparison"] = {"status": "reference_run"}
        return result
    reference = baseline_summary.get("carbon_diagnostics") or {}
    if reference.get("status") != "checked" or not reference.get("run"):
        result["comparison"] = {
            "status": "not_assessed",
            "reason": "Baseline has no verified carbon diagnostics; analyse baseline with --with-carbon first",
        }
        return result
    try:
        comparison = compare_carbon(run, reference["run"])
    except (ValueError, KeyError) as exc:
        result["comparison"] = {"status": "not_assessed", "reason": str(exc)}
        return result
    # The shared comparison also supports the optional experiment. Strip its
    # policy decisions: regular analysis reports differences, not acceptance.
    result["comparison"] = {
        "status": "checked",
        "worst_field_difference_kg_ha": comparison["worst_field_difference_kg_ha"],
        "farm_end_difference_kg": comparison["farm_end_difference_kg"],
        "fields": [{k: v for k, v in row.items() if k != "passed"}
                   for row in comparison["checks"]],
    }
    result["pool_summary"] = summarize_carbon_pools(run["fields"], reference["run"]["fields"])
    return result


def render_soil_diagnostics(summary):
    """Human-readable assessment with separate model and normative statuses."""
    nitrogen = summary.get("fertility_constraints") or {}
    carbon = summary.get("carbon_diagnostics") or {}
    lines = [
        "# Диагностика почвенного состояния", "",
        f'Азотный критерий: режим `{nitrogen.get("mode", "unknown")}`, '
        f'статус `{nitrogen.get("status", "not_assessed")}`.',
        "Сравнение остаточного NO3 + NH4 с базой не является нормативом общего плодородия.",
        "", f'Модельный углерод: `{carbon.get("status", "not_assessed")}`.',
        "Это сумма C всех органических пулов по всему профилю, включая свежую органику. "
        "Она не равна измеренному гумусу пахотного слоя.",
        "Диагностика C не изменяет цели, ограничения или допустимость решения.",
    ]
    if carbon.get("status") != "checked":
        lines += ["", "Причина: " + carbon.get("reason", "диагностика не запрошена")]
    else:
        run = carbon["run"]
        comparison = carbon["comparison"]
        differences = {r["field"]: r["end_difference_kg_ha"]
                       for r in comparison.get("fields", [])}
        lines += ["", f'Период: конец дня {run["period_start"]} — конец дня {run["period_end"]}.',
                  f'Сравнение с базой: `{comparison["status"]}`.']
        if comparison.get("reason"):
            lines += [comparison["reason"]]
        lines += ["", "| Поле | Глубина, см | C в начале, кг/га | C в конце, кг/га | "
                  "Изменение, кг/га | Конечная разница к базе, кг/га |",
                  "|---|---:|---:|---:|---:|---:|"]
        for field, row in run["fields"].items():
            difference = f'{differences[field]:.3f}' if field in differences else "не оценено"
            if comparison["status"] == "reference_run":
                difference = "базовый расчёт"
            lines += [f'| {field} | {row["profile_depth_mm"] / 10:g} | '
                      f'{row["carbon_start_kg_ha"]:.3f} | {row["carbon_end_kg_ha"]:.3f} | '
                      f'{row["carbon_change_kg_ha"]:.3f} | {difference} |']
        pool_summary = carbon.get("pool_summary") or {}
        if pool_summary.get("status") == "checked":
            labels = {"COrganic": "Всего C", "CHumic": "Humic", "CMicrobial": "Microbial",
                      "CInert": "Inert", "CFresh": "Свежая органика", "CWithoutFresh": "C без свежей органики"}
            lines += ["", "## Раздельная оценка углеродных пулов", "",
                      "Изменение за период и отставание от базы — разные показатели. "
                      "Humic является модельным пулом и не подтверждает норматив гумуса.", "",
                      "| Показатель | Изменение по хозяйству, кг C | Конечная разница к базе, кг C | "
                      "Полей ниже начала периода | Полей ниже базы в конце |",
                      "|---|---:|---:|---:|---:|"]
            for metric, values in pool_summary["farm"].items():
                difference = values.get("end_difference_from_baseline_kg")
                difference_text = f"{difference:.3f}" if difference is not None else "не оценено"
                below_base = values.get("fields_below_baseline_end")
                below_base_text = str(len(below_base)) if below_base is not None else "не оценено"
                lines += [f'| {labels[metric]} | {values["change_kg"]:.3f} | {difference_text} | '
                          f'{len(values["fields_below_period_start"])} | {below_base_text} |']
            if comparison["status"] == "checked":
                lines += ["", "Конечная разница к базе по полям, кг C/га:", "",
                          "| Поле | Всего C | Humic | Microbial | Свежая органика |",
                          "|---|---:|---:|---:|---:|"]
                for field, values in pool_summary["fields"].items():
                    components = [values[m]["end_difference_from_baseline_kg_ha"]
                                  for m in ("COrganic", "CHumic", "CMicrobial", "CFresh")]
                    lines += ["| " + field + " | " + " | ".join(f"{v:.3f}" for v in components) + " |"]
        detailed = [(f, r["details"]) for f, r in run["fields"].items()
                    if (r.get("details") or {}).get("status") == "checked"]
        if detailed:
            lines += ["", "## Углерод по слоям и пулам", "",
                      "Humic — модельный пул, а не лабораторный гумус. "
                      "C без свежей органики = Humic + Microbial + Inert. "
                      "Суммы пулов и слоёв проверены по ежедневным данным.", "",
                      "| Поле | Слой, см | Изменение всего C, кг/га | Изменение C без свежей органики | "
                      "Изменение Humic C | Изменение свежего C |",
                      "|---|---|---:|---:|---:|---:|"]
            for field, details in detailed:
                for layer in details["layers"]:
                    change = layer["change"]
                    lines += [f'| {field} | {layer["top_mm"]/10:g}–{layer["bottom_mm"]/10:g} | '
                              f'{change["COrganic"]:.3f} | {change["CWithoutFresh"]:.3f} | '
                              f'{change["CHumic"]:.3f} | {change["CFresh"]:.3f} |']
        layer_comparisons = [(r["field"], r["detail_comparison"]) for r in comparison.get("fields", [])
                             if (r.get("detail_comparison") or {}).get("status") == "checked"]
        if layer_comparisons:
            lines += ["", "## Конечные разницы по слоям относительно базы", "",
                      "| Поле | Слой, см | Всего C, кг/га | C без свежей органики | Humic C | Свежий C |",
                      "|---|---|---:|---:|---:|---:|"]
            for field, detail_comparison in layer_comparisons:
                for layer in detail_comparison["layers"]:
                    delta = layer["end_difference"]
                    lines += [f'| {field} | {layer["top_mm"]/10:g}–{layer["bottom_mm"]/10:g} | '
                              f'{delta["COrganic"]:.3f} | {delta["CWithoutFresh"]:.3f} | '
                              f'{delta["CHumic"]:.3f} | {delta["CFresh"]:.3f} |']
        missing_details = [(f, (r.get("details") or {}).get("reason", "нет данных"))
                           for f, r in run["fields"].items()
                           if (r.get("details") or {}).get("status") != "checked"]
        if missing_details:
            lines += ["", "Детализация по слоям/пулам не оценена:"]
            lines += [f"- {field}: {reason}" for field, reason in missing_details]
        lines += ["", "Начальные состояния кандидата и базы могут различаться из-за управления "
                  "в период прогрева. Годовые значения и начальные разницы сохранены в JSON."]
    lines += ["", "Нормативы гумуса, P, K, pH, Ca/Mg и структуры почвы здесь не проверены. "
              "Отсутствие оценки не означает выполнение ограничений."]
    return "\n".join(lines) + "\n"

