from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "simulation" / "standart.apsimx"
V2_ROOT = ROOT / "experiments" / "hydrology_v2"
TEMPLATE_COPY = V2_ROOT / "template" / "standart_hydrology_v2_base.apsimx"
MANIFEST = V2_ROOT / "workspace_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)
    source_hash = sha256(SOURCE)
    TEMPLATE_COPY.parent.mkdir(parents=True, exist_ok=True)
    if TEMPLATE_COPY.exists():
        copy_hash = sha256(TEMPLATE_COPY)
        if copy_hash != source_hash:
            raise RuntimeError(
                f"Refusing to overwrite changed hydrology-v2 base copy: {TEMPLATE_COPY}"
            )
    else:
        shutil.copy2(SOURCE, TEMPLATE_COPY)
        copy_hash = sha256(TEMPLATE_COPY)

    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Isolated base copy for hydrology-v2 development",
        "source": SOURCE.relative_to(ROOT).as_posix(),
        "source_sha256": source_hash,
        "base_copy": TEMPLATE_COPY.relative_to(ROOT).as_posix(),
        "base_copy_sha256": copy_hash,
        "byte_identical": source_hash == copy_hash,
        "original_modification_policy": "simulation/standart.apsimx must not be edited by hydrology-v2 tools",
        "status": "baseline_copy_only_no_hydrology_fix_applied",
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"HYDROLOGY V2 WORKSPACE OK: {V2_ROOT}")
    print(f"Original SHA256: {source_hash}")
    print(f"Copy SHA256:     {copy_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
