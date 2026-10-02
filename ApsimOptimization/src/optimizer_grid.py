from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Callable

from .apsim_editor import FixedParameters, create_working_copy
from .apsim_runner import runtime_diagnostics
from .config import ConfigError, load_inputs, path_from_config
from .economy import validate_numeric_identity
from .search_space import Technology, build_search_space


EXPECTED_TYPES = {"UreaN", "NH4NO3N", "UAN_N", "NO3N"}
EXPECTED_AMOUNTS = {30, 60, 90, 120, 150, 180, 210}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _scenario_weather_file(value: Any) -> str:
    if isinstance(value, dict):
        try:
            return str(value["weather_file"])
        except KeyError as exc:
            raise ConfigError(f"Weather scenario mapping must contain weather_file: {value}") from exc
    return str(value)


def active_weather_scenarios(config: dict[str, Any]) -> dict[str, Any]:
    """Return configured scenarios which are enabled for optimization."""
    scenarios = config.get("weather_scenarios", {})
    active = {
        scenario_id: value
        for scenario_id, value in scenarios.items()
        if not isinstance(value, dict) or value.get("enabled", True)
    }
    if not active:
        raise ConfigError("At least one weather scenario must be enabled")
    return active


def disabled_weather_scenario_ids(config: dict[str, Any]) -> list[str]:
    return [
        scenario_id
        for scenario_id, value in config.get("weather_scenarios", {}).items()
        if isinstance(value, dict) and not value.get("enabled", True)
    ]


def get_base_template(config: dict[str, Any]) -> Path:
    apsim = config["apsim"]
    if apsim.get("base_template"):
        return config["_root"] / apsim["base_template"]
    templates = apsim.get("scenario_templates")
    if templates:
        first = next(iter(templates.values()))
        return config["_root"] / first
    raise ConfigError("Missing apsim.base_template in optimizer config")


def validate_inputs(config: dict[str, Any], log: Callable[[str], None]) -> dict[str, Any]:
    required = [
        "cultivars_file", "fertilizer_schemes_file", "fertilizer_prices_file",
        "economic_constants_file", "outputs_dir", "work_dir",
    ]
    for key in required:
        path = path_from_config(config, key)
        if key not in {"outputs_dir", "work_dir"} and not path.is_file():
            raise ConfigError(f"Required input not found: {path}")
    inputs = load_inputs(config)
    if not inputs["cultivars"]:
        raise ConfigError("At least one calibrated cultivar is required")
    schemes = inputs["schemes"]
    if len(schemes) != 29 or len({item["id"] for item in schemes}) != 29:
        raise ConfigError(f"Expected 29 unique fertilizer schemes, got {len(schemes)}")
    controls = [item for item in schemes if not item.get("events")]
    events = [event for item in schemes for event in item.get("events", [])]
    if len(controls) != 1 or {e["type_apsimx"] for e in events} != EXPECTED_TYPES or {e["amount"] for e in events} != EXPECTED_AMOUNTS:
        raise ConfigError("Fertilizer schemes do not match control + 4 nitrogen types x 7 doses")
    if EXPECTED_TYPES - inputs["prices"].keys():
        raise ConfigError(f"Missing fertilizer prices: {sorted(EXPECTED_TYPES - inputs['prices'].keys())}")
    scenarios = config["weather_scenarios"]
    if len(scenarios) != 4:
        raise ConfigError("Exactly four weather scenarios must remain configured")
    active_scenarios = active_weather_scenarios(config)
    base_template = get_base_template(config)
    if not base_template.is_file():
        raise ConfigError(f"APSIMX base template not found: {base_template}")
    for scenario_id, value in scenarios.items():
        weather = config["_root"] / _scenario_weather_file(value)
        if not weather.is_file():
            raise ConfigError(f"Weather file not found: {weather}")
    validate_numeric_identity(inputs["constants"])
    space = build_search_space(config, inputs)
    disabled = disabled_weather_scenario_ids(config)
    log(
        f"Validated: {len(inputs['cultivars'])} cultivar(s), {len(schemes)} fertilizer schemes, "
        f"{len(active_scenarios)} active of {len(scenarios)} configured weather scenarios, "
        f"disabled={disabled}, search dimensions={space.dimensions}"
    )
    return inputs


def build_plan(config: dict[str, Any], inputs: dict[str, Any], stage_files: bool, log: Callable[[str], None], stage_name: str = "dry_run", expanded: bool = False) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    work_dir = path_from_config(config, "work_dir") / stage_name
    schemes = {item["id"]: item for item in inputs["schemes"]}
    space = build_search_space(config, inputs, expanded=expanded)
    technologies = list(space.technologies())
    template = get_base_template(config)
    template_sha256 = _file_sha256(template)
    for technology_index, technology in enumerate(technologies, start=1):
        scheme = schemes[technology.fertilizer_scheme_id]
        for scenario_index, (scenario_id, weather_value) in enumerate(active_weather_scenarios(config).items(), start=1):
            weather = config["_root"] / _scenario_weather_file(weather_value)
            fixed = FixedParameters(technology.sowing_rule, technology.population, technology.depth, technology.row, [])
            run_id = f"T{technology_index:05d}_{scenario_index:02d}"
            destination = work_dir / technology.key() / f"{scenario_id}.apsimx"
            if stage_files:
                create_working_copy(template, destination, weather, technology.cultivar, scheme, config, fixed)
            event = scheme.get("events", [{}])[0] if scheme.get("events") else {}
            plans.append({
                "run_id": run_id, "scenario_id": scenario_id, "weather_file": weather.relative_to(config["_root"]).as_posix(),
                "template_file": template.relative_to(config["_root"]).as_posix(),
                "template_sha256": template_sha256, "weather_sha256": _file_sha256(weather),
                "technology_id": technology.key(), "cultivar": technology.cultivar, "fertilizer_scheme_id": scheme["id"],
                "fertilizer_type": event.get("type_apsimx", ""), "fertilizer_amount": event.get("amount", 0),
                "fertilizer_passes": scheme["passes"], "sowing_date": technology.sowing_rule,
                "population": technology.population, "depth": technology.depth, "row": technology.row,
                "working_apsimx": destination.relative_to(config["_root"]).as_posix() if stage_files else "",
            })
    expected = len(technologies) * len(active_weather_scenarios(config))
    if len(plans) != expected:
        raise ConfigError(f"Expected {expected} planned APSIMX runs, got {len(plans)}")
    log(f"Prepared {len(plans)} planned runs from one base template {get_base_template(config).relative_to(config['_root']).as_posix()}; APSIMX execution disabled")
    yield_names = [config.get("results", {}).get("yield_column", "Yield"), *config.get("results", {}).get("yield_fallback_columns", [])]
    log(
        f"Yield rule for real SQLite results: exact Report column from {yield_names}, "
        "latest exact HarvestRipe row, then last nonzero fallback; kg/ha converted to t/ha"
    )
    log("Soil rule: exact N_NO3, P_2O5 and SOC only; otherwise soil_constraints_unchecked")
    log("Runtime diagnostics: " + json.dumps(runtime_diagnostics(config), ensure_ascii=False))
    return plans


def select_plan(plans: list[dict[str, Any]], scenario_id: str, scheme_id: str) -> dict[str, Any]:
    matches = [row for row in plans if row["scenario_id"] == scenario_id and row["fertilizer_scheme_id"] == scheme_id]
    if not matches:
        raise ConfigError(f"No run for scenario={scenario_id!r}, scheme={scheme_id!r}")
    return matches[0]


def build_technology_plan(config: dict[str, Any], inputs: dict[str, Any], technology: Technology, stage_name: str, log: Callable[[str], None]) -> list[dict[str, Any]]:
    schemes = {item["id"]: item for item in inputs["schemes"]}
    scheme = schemes[technology.fertilizer_scheme_id]
    fixed = FixedParameters(technology.sowing_rule, technology.population, technology.depth, technology.row, [])
    rows = []
    template = get_base_template(config)
    template_sha256 = _file_sha256(template)
    for scenario_index, (scenario_id, weather_value) in enumerate(active_weather_scenarios(config).items(), start=1):
        weather = config["_root"] / _scenario_weather_file(weather_value)
        destination = path_from_config(config, "work_dir") / stage_name / technology.key() / f"{scenario_id}.apsimx"
        create_working_copy(template, destination, weather, technology.cultivar, scheme, config, fixed)
        event = scheme.get("events", [{}])[0] if scheme.get("events") else {}
        rows.append({
            "run_id": f"{technology.key()}_{scenario_index:02d}", "technology_id": technology.key(),
            "scenario_id": scenario_id, "weather_file": weather.relative_to(config["_root"]).as_posix(),
            "template_file": template.relative_to(config["_root"]).as_posix(),
            "template_sha256": template_sha256, "weather_sha256": _file_sha256(weather),
            "cultivar": technology.cultivar, "fertilizer_scheme_id": scheme["id"],
            "fertilizer_type": event.get("type_apsimx", ""), "fertilizer_amount": event.get("amount", 0),
            "fertilizer_passes": scheme["passes"], "sowing_date": technology.sowing_rule,
            "population": technology.population, "depth": technology.depth, "row": technology.row,
            "working_apsimx": destination.relative_to(config["_root"]).as_posix(),
        })
    log(f"Prepared technology {technology.key()} for {len(rows)} scenarios")
    return rows
