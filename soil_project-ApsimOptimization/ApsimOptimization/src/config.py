from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # Allows validation/dry-run in minimal Python environments.
    yaml = None


ROOT = Path(__file__).resolve().parents[1]

RUN_QUALITY_DEFAULTS = {
    "require_sowing": True,
    "require_harvest": True,
    "allow_zero_yield": False,
    "minimum_sowing_events": 1,
    "minimum_harvest_events": 1,
    "require_relative_paths": True,
    "require_weather_clock_coverage": True,
    "warn_missing_weather_boundary_days": True,
    "validate_soil_physical_bounds": True,
}


class ConfigError(ValueError):
    """Raised when optimizer configuration or input files are invalid."""


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def load_config(path: str | Path = "configs/optimizer_config.yaml") -> dict[str, Any]:
    config_path = _resolve(ROOT, str(path))
    if not config_path.is_file():
        raise ConfigError(f"Optimizer config not found: {config_path}")
    text = config_path.read_text(encoding="utf-8")
    config = yaml.safe_load(text) if yaml else _parse_simple_yaml(text)
    configured_quality = dict(config.get("run_quality") or {})
    config["run_quality"] = {**RUN_QUALITY_DEFAULTS, **configured_quality}
    config["_root"] = ROOT
    config["_config_path"] = config_path
    return config


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the mappings/scalars used by optimizer_config.yaml, without YAML extensions."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    def scalar(value: str) -> Any:
        value = value.strip()
        if not value:
            return {}
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return value[1:-1]
        if value in {"null", "~"}:
            return None
        if value.lower() in {"true", "false"}:
            return value.lower() == "true"
        if value.startswith("[") and value.endswith("]"):
            return json.loads(value)
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return value

    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.lstrip().startswith("-"):
            continue  # Existing notes are informational and unused by the optimizer.
        indent = len(raw) - len(raw.lstrip(" "))
        if ":" not in raw:
            raise ConfigError(f"Unsupported YAML syntax at line {number}: {raw}")
        key, value = raw.strip().split(":", 1)
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        parsed = scalar(value)
        parent[key] = parsed
        if isinstance(parsed, dict):
            stack.append((indent, parsed))
    return root


def path_from_config(config: dict[str, Any], key: str) -> Path:
    try:
        value = config["paths"][key]
    except KeyError as exc:
        raise ConfigError(f"Missing paths.{key} in optimizer config") from exc
    return _resolve(config["_root"], value)


def load_inputs(config: dict[str, Any]) -> dict[str, Any]:
    cultivars_path = path_from_config(config, "cultivars_file")
    schemes_path = path_from_config(config, "fertilizer_schemes_file")
    prices_path = path_from_config(config, "fertilizer_prices_file")
    constants_path = path_from_config(config, "economic_constants_file")

    cultivars = [x.strip() for x in cultivars_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    schemes = json.loads(schemes_path.read_text(encoding="utf-8"))
    with prices_path.open(encoding="utf-8-sig", newline="") as stream:
        prices = {row["type_apsimx"]: float(row["price_per_kg_amount"]) for row in csv.DictReader(stream)}
    with constants_path.open(encoding="utf-8-sig", newline="") as stream:
        constants = {row["name"]: float(row["value"]) for row in csv.DictReader(stream)}
    return {"cultivars": cultivars, "schemes": schemes, "prices": prices, "constants": constants}
