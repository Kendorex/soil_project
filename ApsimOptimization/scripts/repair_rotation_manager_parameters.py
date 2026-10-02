#!/usr/bin/env python3
"""Restore missing rotation-manager parameters from a known-good APSIMX file."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


MANAGER_NAME = "Sow using a variable rule"
REQUIRED_KEYS = {
    "RotationCrops",
    "RotationCultivars",
    "RotationSowingDepths",
    "RotationRowSpacings",
    "RotationPopulations",
    "RotationSowingDates",
    "RotationHarvestDates",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manager_index(data: dict) -> dict[str, dict]:
    result = {}

    def visit(node, path=""):
        if not isinstance(node, dict):
            return
        current = f"{path}/{node.get('Name', '')}"
        if node.get("Name") == MANAGER_NAME:
            result[current] = node
        for child in node.get("Children", []):
            visit(child, current)

    visit(data)
    return result


def parameter_keys(node: dict) -> set[str]:
    return {str(item.get("Key")) for item in (node.get("Parameters") or [])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Known-good APSIMX with rotation arrays")
    parser.add_argument("--target", type=Path, required=True, help="Broken calibrated APSIMX")
    parser.add_argument("--output", type=Path, required=True, help="New repaired APSIMX; target is never overwritten")
    args = parser.parse_args()
    for path in (args.source, args.target):
        if not path.is_file():
            raise SystemExit(f"File not found: {path}")
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite existing output: {args.output}")

    source_data = json.loads(args.source.read_text(encoding="utf-8-sig"))
    target_data = json.loads(args.target.read_text(encoding="utf-8-sig"))
    source = manager_index(source_data)
    target = manager_index(target_data)
    if set(source) != set(target):
        raise SystemExit("Source and target rotation-manager paths differ")

    repaired = []
    for path in sorted(target):
        source_node = source[path]
        target_node = target[path]
        if source_node.get("CodeArray") != target_node.get("CodeArray"):
            raise SystemExit(f"Manager code differs; refusing parameter-only repair: {path}")
        source_keys = parameter_keys(source_node)
        if not REQUIRED_KEYS <= source_keys:
            raise SystemExit(f"Known-good source lacks required rotation arrays: {path}")
        target_keys = parameter_keys(target_node)
        if target_keys:
            raise SystemExit(f"Target manager is not empty; refusing ambiguous merge: {path}")
        target_node["Parameters"] = copy.deepcopy(source_node["Parameters"])
        repaired.append(path)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(target_data, ensure_ascii=False, indent=2), encoding="utf-8")
    verification = manager_index(json.loads(args.output.read_text(encoding="utf-8")))
    for path, node in verification.items():
        if not REQUIRED_KEYS <= parameter_keys(node):
            raise SystemExit(f"Post-write verification failed: {path}")
    print(json.dumps({
        "source": str(args.source),
        "source_sha256": sha256(args.source),
        "target": str(args.target),
        "target_sha256": sha256(args.target),
        "output": str(args.output),
        "output_sha256": sha256(args.output),
        "repaired_manager_count": len(repaired),
        "target_overwritten": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
