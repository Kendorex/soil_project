#!/usr/bin/env python3
"""Create a non-destructive APSIMX CLI test copy without Synchroniser nodes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def remove_synchronisers(node) -> int:
    if not isinstance(node, dict):
        return 0
    children = node.get("Children")
    removed = 0
    if isinstance(children, list):
        kept = []
        for child in children:
            if isinstance(child, dict) and child.get("Name") == "Synchroniser":
                removed += 1
            else:
                removed += remove_synchronisers(child)
                kept.append(child)
        node["Children"] = kept
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite: {args.output}")
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    removed = remove_synchronisers(data)
    if removed == 0:
        raise SystemExit("No Synchroniser nodes found")
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared CLI test copy: {args.output}; removed Synchroniser nodes: {removed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
