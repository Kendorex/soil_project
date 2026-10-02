from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .apsim_runner import find_db_for_apsimx, run_apsim_files
from .config import ConfigError
from .optimizer_grid import _scenario_weather_file, get_base_template
from .report_variables import inspect_report_variables


SOIL_KEYWORDS = (
    "soil",
    "nitrogen",
    "no3",
    "nitrate",
    "nh4",
    "ammonium",
    "phosphorus",
    "phosph",
    "p2o5",
    "organic",
    "carbon",
    "soc",
    "nutrient",
    "solutes",
    "chemical",
)


@dataclass(frozen=True)
class Candidate:
    target_name: str
    expression: str
    sqlite_column_name: str
    alias: str = ""


def _walk_json(node: Any, path: str = ""):
    if isinstance(node, dict):
        name = str(node.get("Name") or "")
        node_path = f"{path}/{name}" if name else path
        yield node_path or "/", node
        children = node.get("Children")
        if isinstance(children, list):
            for child in children:
                yield from _walk_json(child, node_path)
        for key, value in node.items():
            if key == "Children":
                continue
            if isinstance(value, (dict, list)):
                yield from _walk_json(value, f"{node_path}/{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk_json(item, f"{path}[{index}]")


def scan_soil_related_nodes(apsimx_path: Path, keywords: tuple[str, ...] = SOIL_KEYWORDS) -> dict[str, Any]:
    data = json.loads(apsimx_path.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    lowered_keywords = tuple(keyword.lower() for keyword in keywords)

    for node_path, node in _walk_json(data):
        searchable_parts = [
            str(node.get("Name", "")),
            str(node.get("$type", "")),
            str(node.get("ResourceName", "")),
        ]
        searchable_parts.extend(str(key) for key in node.keys())
        text = " ".join(searchable_parts).lower()
        matched = [keyword for keyword in lowered_keywords if keyword in text]
        if matched:
            results.append(
                {
                    "path": node_path,
                    "name": node.get("Name"),
                    "type": node.get("$type"),
                    "matched_keywords": sorted(set(matched)),
                    "keys": sorted(map(str, node.keys())),
                }
            )

    report = inspect_report_variables(apsimx_path)
    return {
        "apsimx_path": str(apsimx_path),
        "keywords": list(keywords),
        "match_count": len(results),
        "matches": results,
        "report_variable_names": report["variable_names"],
        "report_variable_issues": report["issues"],
    }


def write_soil_diagnosis(outputs: Path, diagnosis: dict[str, Any]) -> tuple[Path, Path]:
    outputs.mkdir(parents=True, exist_ok=True)
    json_path = outputs / "soil_outputs_diagnosis.json"
    text_path = outputs / "soil_outputs_diagnosis.txt"
    json_path.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"APSIMX file: {diagnosis['apsimx_path']}",
        f"Matched nodes: {diagnosis['match_count']}",
        "",
        "Matched soil-related nodes:",
    ]
    for item in diagnosis["matches"]:
        lines.append(f"- path: {item['path']}")
        lines.append(f"  name: {item.get('name')}")
        lines.append(f"  type: {item.get('type')}")
        lines.append(f"  keywords: {', '.join(item['matched_keywords'])}")
    lines.extend(["", "Report.VariableNames:"])
    lines.extend(f"- {value}" for value in diagnosis.get("report_variable_names", []))
    if diagnosis.get("report_variable_issues"):
        lines.extend(["", "Reporter normalization issues:"])
        lines.extend(f"- {value}" for value in diagnosis["report_variable_issues"])
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, text_path


def load_soil_output_candidates(path: Path) -> dict[str, list[str]]:
    try:
        import yaml
    except ImportError:
        yaml = None

    if yaml is not None:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        candidates = payload.get("candidates", {})
        return {str(target): [str(expr) for expr in (expressions or [])] for target, expressions in candidates.items()}

    candidates: dict[str, list[str]] = {}
    current: str | None = None
    in_candidates = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0 and stripped == "candidates:":
            in_candidates = True
            continue
        if not in_candidates:
            continue
        if indent == 2 and stripped.endswith(":"):
            current = stripped[:-1].strip()
            candidates[current] = []
            continue
        if indent >= 4 and stripped.startswith("-") and current:
            value = stripped[1:].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            candidates[current].append(value)
    return candidates


def flatten_candidates(candidate_map: dict[str, list[str]]) -> list[Candidate]:
    flattened: list[Candidate] = []
    for target_name, expressions in candidate_map.items():
        safe_target = re.sub(r"[^A-Za-z0-9_]+", "_", target_name)
        for index, expression in enumerate(expressions, start=1):
            alias_match = re.search(r"\s+as\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", expression, re.IGNORECASE)
            alias = alias_match.group(1) if alias_match else f"candidate__{safe_target}__{index:03d}"
            flattened.append(
                Candidate(
                    target_name=target_name,
                    expression=expression,
                    sqlite_column_name=alias,
                    alias=alias,
                )
            )
    return flattened


def add_candidates_to_report(apsimx_path: Path, candidates: list[Candidate]) -> None:
    data = json.loads(apsimx_path.read_text(encoding="utf-8"))
    reports = [
        node
        for _path, node in _walk_json(data)
        if node.get("Name") == "Report" and "Models.Report" in str(node.get("$type", ""))
    ]
    if len(reports) != 1:
        raise ConfigError(f"Expected exactly one Report node, found {len(reports)}")
    report = reports[0]
    variable_names = report.setdefault("VariableNames", [])
    existing = set(variable_names)
    for candidate in candidates:
        has_alias = re.search(r"\s+as\s+[A-Za-z_][A-Za-z0-9_]*\s*$", candidate.expression, re.IGNORECASE)
        expression = candidate.expression if has_alias else f"{candidate.expression} as {candidate.sqlite_column_name}"
        if expression not in existing:
            variable_names.append(expression)
    apsimx_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _sqlite_report_columns(database: Path, report_table: str = "Report") -> list[str]:
    connection = sqlite3.connect(database)
    try:
        return [row[1] for row in connection.execute(f'PRAGMA table_info("{report_table}")')]
    finally:
        connection.close()


def _column_stats(database: Path, column: str, report_table: str = "Report") -> dict[str, Any]:
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            f'SELECT COUNT("{column}"), MIN("{column}"), MAX("{column}") '
            f'FROM "{report_table}" WHERE "{column}" IS NOT NULL'
        ).fetchone()
    finally:
        connection.close()
    return {
        "sample_non_null_count": int(row[0] or 0),
        "min_value": row[1],
        "max_value": row[2],
    }


def _find_units(database: Path, column: str) -> str:
    connection = sqlite3.connect(database)
    try:
        tables = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if "unit" in row[0].lower()
        ]
        for table in tables:
            cols = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            if column in cols:
                row = connection.execute(f'SELECT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT 1').fetchone()
                if row and row[0] is not None:
                    return str(row[0])
            lower_cols = {col.lower(): col for col in cols}
            if {"name", "units"} <= set(lower_cols):
                row = connection.execute(
                    f'SELECT "{lower_cols["units"]}" FROM "{table}" WHERE "{lower_cols["name"]}" = ? LIMIT 1',
                    (column,),
                ).fetchone()
                if row and row[0] is not None:
                    return str(row[0])
        return ""
    finally:
        connection.close()


def candidate_rows_from_database(database: Path, candidates: list[Candidate], report_table: str = "Report") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        columns = set(_sqlite_report_columns(database, report_table))
    except Exception as exc:
        return [
            {
                "target_name": candidate.target_name,
                "expression": candidate.expression,
                "alias": candidate.alias or candidate.sqlite_column_name,
                "status": "failed",
                "sqlite_column_name": candidate.sqlite_column_name,
                "sample_non_null_count": "",
                "min_value": "",
                "max_value": "",
                "units": "",
                "error_message": str(exc),
            }
            for candidate in candidates
        ]

    for candidate in candidates:
        if candidate.sqlite_column_name not in columns:
            rows.append(
                {
                    "target_name": candidate.target_name,
                    "expression": candidate.expression,
                    "alias": candidate.alias or candidate.sqlite_column_name,
                    "status": "missing_column",
                    "sqlite_column_name": candidate.sqlite_column_name,
                    "sample_non_null_count": 0,
                    "min_value": "",
                    "max_value": "",
                    "units": "",
                    "error_message": "Candidate expression did not appear as a Report column",
                }
            )
            continue
        try:
            stats = _column_stats(database, candidate.sqlite_column_name, report_table)
            rows.append(
                {
                    "target_name": candidate.target_name,
                    "expression": candidate.expression,
                    "alias": candidate.alias or candidate.sqlite_column_name,
                    "status": "works_with_values" if stats["sample_non_null_count"] > 0 else "works_all_null",
                    "sqlite_column_name": candidate.sqlite_column_name,
                    **stats,
                    "units": _find_units(database, candidate.sqlite_column_name),
                    "error_message": "Candidate expression works technically, but agronomic validity must be confirmed.",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "target_name": candidate.target_name,
                    "expression": candidate.expression,
                    "alias": candidate.alias or candidate.sqlite_column_name,
                    "status": "failed",
                    "sqlite_column_name": candidate.sqlite_column_name,
                    "sample_non_null_count": "",
                    "min_value": "",
                    "max_value": "",
                    "units": "",
                    "error_message": str(exc),
                }
            )
    return rows


def write_candidate_test_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "target_name",
        "expression",
        "alias",
        "status",
        "sqlite_column_name",
        "sample_non_null_count",
        "min_value",
        "max_value",
        "units",
        "error_message",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def apsim_error_rows(candidates: list[Candidate], error: Exception) -> list[dict[str, Any]]:
    return [
        {
            "target_name": candidate.target_name,
            "expression": candidate.expression,
            "alias": candidate.alias or candidate.sqlite_column_name,
            "status": "apsim_error",
            "sqlite_column_name": candidate.sqlite_column_name,
            "sample_non_null_count": "",
            "min_value": "",
            "max_value": "",
            "units": "",
            "error_message": str(error),
        }
        for candidate in candidates
    ]


def not_configured_rows(target_names: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "target_name": target_name,
            "expression": "",
            "alias": "",
            "status": "not_configured",
            "sqlite_column_name": "",
            "sample_non_null_count": "",
            "min_value": "",
            "max_value": "",
            "units": "",
            "error_message": "No candidate Reporter expression is configured",
        }
        for target_name in target_names
    ]


def stage_candidate_test_apsimx(
    config: dict[str, Any],
    scenario_id: str,
    candidates: list[Candidate],
    suffix: str = "",
) -> Path:
    scenario = config.get("weather_scenarios", {}).get(scenario_id)
    if scenario is None:
        raise ConfigError(f"Unknown weather scenario {scenario_id!r}")
    template = get_base_template(config)
    weather = config["_root"] / _scenario_weather_file(scenario)
    safe_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", suffix).strip("_")
    filename = f"{scenario_id}__{safe_suffix}.apsimx" if safe_suffix else f"{scenario_id}.apsimx"
    destination = config["_root"] / config["paths"]["work_dir"] / "soil_candidate_test" / filename

    # Reuse the normal editor with a minimal no-fertilizer scheme so the candidate test
    # runs through the same safe temporary-copy path as optimizer runs.
    from .apsim_editor import FixedParameters, create_working_copy

    defaults = config["apsim"]["defaults"]
    fixed = FixedParameters(
        str(defaults["sowing_date"]),
        float(defaults["population"]),
        float(defaults["depth"]),
        float(defaults["row"]),
        [],
    )
    scheme = {"id": "soil_candidate_test_no_fertilizer", "passes": 0, "events": []}
    cultivar = config.get("cultivar", {}).get("default")
    if not cultivar:
        raise ConfigError("cultivar.default is required for soil candidate tests")
    cultivar = str(cultivar)
    create_working_copy(template, destination, weather, cultivar, scheme, config, fixed)
    add_candidates_to_report(destination, candidates)
    return destination


def run_soil_candidate_test(
    config: dict[str, Any],
    scenario_id: str,
    candidates_path: Path,
    logger,
) -> list[dict[str, Any]]:
    candidate_map = load_soil_output_candidates(candidates_path)
    candidates = flatten_candidates(candidate_map)
    rows: list[dict[str, Any]] = not_configured_rows(
        [target for target, expressions in candidate_map.items() if not expressions]
    )
    report_table = config.get("results", {}).get("report_table", "Report")
    for index, candidate in enumerate(candidates, start=1):
        logger.info("Testing soil output candidate separately: target=%s expression=%s", candidate.target_name, candidate.expression)
        try:
            suffix = f"{index:03d}_{candidate.target_name}_{candidate.sqlite_column_name}"
            apsimx = stage_candidate_test_apsimx(config, scenario_id, [candidate], suffix=suffix)
            run_apsim_files([apsimx], config, logger)
            database = find_db_for_apsimx(apsimx)
            if database is None:
                raise RuntimeError(f"APSIMX finished but no SQLite database was found for {apsimx}")
            rows.extend(candidate_rows_from_database(database, [candidate], report_table))
        except Exception as exc:
            logger.warning("Candidate failed without stopping remaining tests: %s", exc)
            rows.extend(apsim_error_rows([candidate], exc))
    return rows
