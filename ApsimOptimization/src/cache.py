from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


SIGNATURE_FIELDS = (
    "technology_id", "scenario_id", "weather_file", "cultivar", "fertilizer_scheme_id",
    "sowing_date", "population", "depth", "row",
    "template_sha256", "weather_sha256",
)
CACHE_SCHEMA_VERSION = 5


def signature(row: dict[str, Any]) -> str:
    payload = {key: row.get(key) for key in SIGNATURE_FIELDS}
    payload["cache_schema_version"] = CACHE_SCHEMA_VERSION
    text = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ResultCache:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def _path(self, row: dict[str, Any]) -> Path:
        return self.directory / f"{signature(row)}.json"

    def load(self, row: dict[str, Any]) -> dict[str, Any] | None:
        path = self._path(row)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            database = Path(payload["database"])
            if payload.get("signature") != signature(row) or not database.is_file() or database.stat().st_size == 0:
                return None
            connection = sqlite3.connect(database)
            try:
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    return None
                report = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='Report'"
                ).fetchone()
                if report is None:
                    return None
            finally:
                connection.close()
            return payload["result"]
        except (OSError, KeyError, ValueError, json.JSONDecodeError, sqlite3.Error):
            return None

    def save(self, row: dict[str, Any], database: Path, result: dict[str, Any]) -> None:
        payload = {"signature": signature(row), "database": str(database.resolve()), "result": result}
        self._path(row).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
