#!/usr/bin/env python3
"""Verify file sizes and SHA-256 hashes recorded in a result archive manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path


def verify(path: Path) -> dict:
    archive_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    with tarfile.open(path, "r:gz") as archive:
        members = {item.name: item for item in archive.getmembers() if item.isfile()}
        if "manifest.json" not in members:
            raise ValueError(f"{path}: manifest.json is absent")
        manifest = json.load(archive.extractfile("manifest.json"))
        errors = []
        for record in manifest.get("files", []):
            name = record.get("path")
            if name not in members:
                errors.append(f"missing:{name}")
                continue
            payload = archive.extractfile(name).read()
            if len(payload) != int(record["size"]):
                errors.append(f"size:{name}")
            if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                errors.append(f"sha256:{name}")
        declared = {record.get("path") for record in manifest.get("files", [])}
        undeclared = sorted(set(members) - declared - {"manifest.json"})
        errors.extend(f"undeclared:{name}" for name in undeclared)
    return {
        "archive": str(path.resolve()),
        "archive_sha256": archive_sha256,
        "declared_files": len(manifest.get("files", [])),
        "verified": not errors,
        "errors": errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    args = parser.parse_args(argv)
    failed = False
    for path in args.archives:
        result = verify(path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        failed = failed or not result["verified"]
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
