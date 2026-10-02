from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def normalize_report_variable_names(variable_names: list[str]) -> tuple[list[str], list[str]]:
    """Repair known Reporter formatting defects without guessing APSIM variables."""
    normalized: list[str] = []
    issues: list[str] = []
    seen: set[str] = set()
    glued = "[Soil].Swim3.Runoff as Runoff[Soil].Water.Volumetric as SW"
    replacements = {
        "sum([NO3].kgha + [NH4].kgha + [Urea].kgha) as MineralN":
            "sum([NO3].kgha + [NH4].kgha + [Urea].kgha) as MineralNWithUrea",
        "[Denitrification].Value + [Denitrification].Natm":
            "[Denitrification].Value + [Denitrification].Natm as DenitrifiedN",
        "[NFlow].Natm": "[NFlow].Natm as NAtmosphereLoss",
    }

    for raw in variable_names:
        parts = ["[Soil].Swim3.Runoff as Runoff", "[Soil].Water.Volumetric as SW"] if raw == glued else [replacements.get(raw, raw)]
        if raw == glued:
            issues.append("split glued Runoff/SW Reporter expression")
        if raw in replacements:
            issues.append(f"normalized Reporter alias: {raw} -> {replacements[raw]}")
            if raw.endswith(" as MineralN"):
                parts.insert(0, "sum([NO3].kgha + [NH4].kgha) as MineralN")
                parts.extend([
                    "sum([NO3].kgha) as NO3kgHa",
                    "sum([NH4].kgha) as NH4kgHa",
                ])
                issues.append("separated MineralN (NO3+NH4) from MineralNWithUrea")
                issues.append("added separate NO3kgHa and NH4kgHa diagnostic outputs")
        for expression in parts:
            if expression in seen:
                issues.append(f"removed duplicate Reporter expression: {expression}")
                continue
            seen.add(expression)
            normalized.append(expression)
    return normalized, issues


def report_variable_names(data: dict[str, Any]) -> list[str]:
    reports = [
        node for node in _walk(data)
        if node.get("Name") == "Report" and "Models.Report" in str(node.get("$type", ""))
    ]
    if len(reports) != 1:
        raise ValueError(f"Expected exactly one Report node, found {len(reports)}")
    return list(reports[0].get("VariableNames", []))


def normalize_report_in_data(data: dict[str, Any]) -> list[str]:
    reports = [
        node for node in _walk(data)
        if node.get("Name") == "Report" and "Models.Report" in str(node.get("$type", ""))
    ]
    if len(reports) != 1:
        raise ValueError(f"Expected exactly one Report node, found {len(reports)}")
    normalized, issues = normalize_report_variable_names(list(reports[0].get("VariableNames", [])))
    reports[0]["VariableNames"] = normalized
    return issues


def inspect_report_variables(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        original = report_variable_names(data)
    except ValueError as exc:
        # Structural diagnostics must still be useful for partial/minimal APSIMX
        # JSON documents. Runtime staging remains strict via normalize_report_in_data.
        return {
            "variable_names": [],
            "normalized_variable_names": [],
            "issues": [str(exc)],
        }
    normalized, issues = normalize_report_variable_names(original)
    return {"variable_names": original, "normalized_variable_names": normalized, "issues": issues}
