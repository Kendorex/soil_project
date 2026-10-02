"""Traceable assembly and audit of the calibrated multi-field APSIMX model."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ZLATA_PARAMETER_COMMANDS = {
    "MinLN": "[Phenology].CAMP.FLNparams.MinLN",
    "PpLN": "[Phenology].CAMP.FLNparams.PpLN",
    "BasePhyllochron": "[Phenology].Phyllochron.BasePhyllochron.FixedValue",
    "GrainFillingTarget": "[Phenology].GrainFilling.Target.FixedValue",
    "GrainRipeningTarget": "[Phenology].GrainRipening.Target.FixedValue",
}


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def walk(node: Any, path: str = ""):
    if not isinstance(node, dict):
        return
    current = f"{path}/{node.get('Name', '')}"
    yield current, node
    for child in node.get("Children", []):
        yield from walk(child, current)


def cultivar_nodes(data: dict[str, Any], cultivar: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        (path, node)
        for path, node in walk(data)
        if node.get("Name") == cultivar
        and str(node.get("$type", "")).startswith("Models.PMF.Cultivar")
    ]


def command_map(node: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for command in node.get("Command") or []:
        if "=" not in command:
            raise ValueError(f"Invalid cultivar command: {command}")
        name, value = (part.strip() for part in command.split("=", 1))
        if name in result:
            raise ValueError(f"Duplicate cultivar command: {name}")
        result[name] = value
    return result


def cultivar_inventory(data: dict[str, Any], cultivar: str) -> dict[str, Any]:
    rows = cultivar_nodes(data, cultivar)
    variants: dict[str, dict[str, Any]] = {}
    for path, node in rows:
        commands = command_map(node)
        key = json.dumps(commands, ensure_ascii=False, sort_keys=True)
        variants.setdefault(key, {"commands": commands, "paths": []})["paths"].append(path)
    return {
        "cultivar": cultivar,
        "node_count": len(rows),
        "variant_count": len(variants),
        "variants": list(variants.values()),
    }


def require_single_variant(data: dict[str, Any], cultivar: str) -> dict[str, str]:
    inventory = cultivar_inventory(data, cultivar)
    if inventory["node_count"] == 0:
        raise ValueError(f"Cultivar {cultivar} was not found")
    if inventory["variant_count"] != 1:
        raise ValueError(
            f"Cultivar {cultivar} has {inventory['variant_count']} field-specific variants"
        )
    return inventory["variants"][0]["commands"]


def patch_cultivar(
    data: dict[str, Any], cultivar: str, commands: dict[str, float | int | str],
    *, allow_new_commands: bool = False,
) -> list[dict[str, Any]]:
    nodes = cultivar_nodes(data, cultivar)
    if not nodes:
        raise ValueError(f"Cultivar {cultivar} was not found")
    changes: list[dict[str, Any]] = []
    for path, node in nodes:
        existing = list(node.get("Command") or [])
        for command_path, value in commands.items():
            pattern = re.compile(r"^" + re.escape(command_path) + r"\s*=")
            matches = [i for i, command in enumerate(existing) if pattern.match(command)]
            if len(matches) == 0 and allow_new_commands:
                new = str(value)
                existing.append(f"{command_path} = {new}")
                changes.append(
                    {"path": path, "command": command_path, "old": None, "new": new}
                )
                continue
            if len(matches) != 1:
                raise ValueError(
                    f"Expected one existing {command_path} in {path}, found {len(matches)}"
                )
            index = matches[0]
            old = existing[index].split("=", 1)[1].strip()
            new = str(value)
            try:
                equivalent = float(old) == float(new)
            except ValueError:
                equivalent = old == new
            if equivalent:
                continue
            existing[index] = f"{command_path} = {new}"
            changes.append(
                {"path": path, "command": command_path, "old": old, "new": new}
            )
        node["Command"] = existing
    return changes


def commands_from_calibration(
    result: dict[str, Any], mapping: dict[str, str]
) -> dict[str, float | int | str]:
    missing = [name for name in mapping if name not in result]
    if missing:
        raise ValueError(f"Calibration result is missing parameters: {missing}")
    return {command: result[name] for name, command in mapping.items()}


def require_commands(
    data: dict[str, Any], cultivar: str, expected: dict[str, float | int | str]
) -> None:
    for path, node in cultivar_nodes(data, cultivar):
        actual = command_map(node)
        for command, value in expected.items():
            if command not in actual or float(actual[command]) != float(value):
                raise ValueError(
                    f"{path}: {command} is {actual.get(command)!r}, expected {value!r}"
                )


def build_calibrated_model(
    source_model: str | Path,
    destination: str | Path,
    zlata_result_path: str | Path,
    rye_provenance_path: str | Path,
    irgina_reference_model: str | Path,
    original_user_model: str | Path | None = None,
    weather_source: str | Path | None = None,
    weather_corrected: str | Path | None = None,
) -> dict[str, Any]:
    """Assemble one model from explicit accepted calibration artefacts.

    The source and all evidence files are read-only.  Only the destination and
    its provenance file are written.
    """
    source_model = Path(source_model)
    destination = Path(destination)
    data = copy.deepcopy(read_json(source_model))
    zlata_result = read_json(zlata_result_path)
    rye_provenance = read_json(rye_provenance_path)
    irgina_reference = read_json(irgina_reference_model)

    zlata_commands = commands_from_calibration(zlata_result, ZLATA_PARAMETER_COMMANDS)
    changes = patch_cultivar(data, "Zlata", zlata_commands)

    rye_commands = rye_provenance.get("commands") or {}
    if not rye_commands:
        raise ValueError("Rye provenance contains no commands")
    # The accepted rye diagnostic explicitly introduced three verified winter
    # phenology overrides that were absent from the supplied cultivar.
    rye_changes = patch_cultivar(
        data, "Winter_rye_smoke", rye_commands, allow_new_commands=True
    )
    changes.extend(rye_changes)

    # Irgina's accepted state is the one stored in the single-field calibrated
    # reference. It is preserved, but equality is checked so drift cannot pass.
    irgina_expected = require_single_variant(irgina_reference, "Irgina")
    require_commands(data, "Irgina", irgina_expected)
    require_commands(data, "Zlata", zlata_commands)
    require_commands(data, "Winter_rye_smoke", rye_commands)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )

    source_rye = cultivar_inventory(read_json(source_model), "Winter_rye_smoke")
    output_rye = cultivar_inventory(data, "Winter_rye_smoke")
    provenance: dict[str, Any] = {
        "status": "assembled_from_explicit_calibration_evidence",
        "source_model": str(source_model),
        "source_model_sha256": sha256(source_model),
        "output_model": str(destination),
        "output_model_sha256": sha256(destination),
        "zlata_calibration_result": str(zlata_result_path),
        "zlata_calibration_result_sha256": sha256(zlata_result_path),
        "rye_calibration_provenance": str(rye_provenance_path),
        "rye_calibration_provenance_sha256": sha256(rye_provenance_path),
        "irgina_reference_model": str(irgina_reference_model),
        "irgina_reference_model_sha256": sha256(irgina_reference_model),
        "changes": changes,
        "cultivars": {
            name: cultivar_inventory(data, name)
            for name in ("Zlata", "Irgina", "Winter_rye_smoke", "Yakov")
        },
        "rye_variant_note": {
            "before": source_rye["variant_count"],
            "after": output_rye["variant_count"],
            "explanation": (
                "Accepted rye phenology commands are uniform. Existing grain-filling "
                "and grain-ripening differences are preserved because they were not "
                "part of the rye phenology calibration."
            ),
        },
    }
    for label, path in (
        ("original_user_model", original_user_model),
        ("weather_source", weather_source),
        ("weather_corrected", weather_corrected),
    ):
        if path is not None:
            provenance[label] = str(path)
            provenance[label + "_sha256"] = sha256(path)

    provenance_path = destination.with_suffix(".provenance.json")
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return provenance
