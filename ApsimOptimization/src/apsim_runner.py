from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .run_preflight import validate_prepared_apsimx


class ApsimUnavailableError(RuntimeError):
    """Raised when the configured APSIMX runtime cannot be used."""


def _resolve_path(root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else root / path


def _find_models_dll(config: dict[str, Any]) -> Path | None:
    root = config["_root"]
    apsim = config["apsim"]

    configured = _resolve_path(root, apsim.get("models_dll"))
    if configured and configured.is_file():
        return configured

    zmq_dll = _resolve_path(root, apsim.get("zmq_server_dll"))
    if zmq_dll:
        candidate = zmq_dll.with_name("Models.dll")
        if candidate.is_file():
            return candidate

    home = Path.home()
    candidates = [
        home / "ApsimX/bin/Release/net8.0/Models.dll",
        home / "ApsimX/bin/Debug/net8.0/Models.dll",
    ]

    return next((path for path in candidates if path.is_file()), None)


def runtime_diagnostics(config: dict[str, Any]) -> dict[str, Any]:
    apsim = config["apsim"]
    root = config["_root"]

    dotnet_value = apsim.get("dotnet")
    dotnet = shutil.which(dotnet_value or "dotnet")

    models_dll = _find_models_dll(config)

    base_value = apsim.get("base_template")
    base_template = _resolve_path(root, base_value)
    templates = {}
    for scenario, value in apsim.get("scenario_templates", {}).items():
        path = Path(value)
        path = path if path.is_absolute() else root / path
        templates[scenario] = {"path": str(path), "exists": path.is_file()}

    weather_files = {}
    for scenario, value in config.get("weather_scenarios", {}).items():
        weather_value = value.get("weather_file") if isinstance(value, dict) else value
        weather_path = _resolve_path(root, str(weather_value))
        weather_files[scenario] = {
            "path": str(weather_path),
            "exists": bool(weather_path and weather_path.is_file()),
            "enabled": value.get("enabled", True) if isinstance(value, dict) else True,
        }

    work_dir = root / config["paths"]["work_dir"]
    work_create = work_delete_db = sqlite_read = False
    work_error = None

    try:
        probe_dir = work_dir / ".env_probe"
        probe_dir.mkdir(parents=True, exist_ok=True)

        apsim_probe = probe_dir / "probe.apsimx"
        if base_template and base_template.is_file():
            shutil.copy2(base_template, apsim_probe)
        else:
            apsim_probe.write_text("{}", encoding="utf-8")
        work_create = apsim_probe.is_file()
        apsim_probe.unlink()

        db_probe = probe_dir / "probe.db"
        connection = sqlite3.connect(db_probe)
        connection.execute("CREATE TABLE probe(value INTEGER)")
        connection.execute("INSERT INTO probe VALUES (1)")
        connection.commit()
        sqlite_read = connection.execute("SELECT value FROM probe").fetchone()[0] == 1
        connection.close()
        db_probe.unlink()

        work_delete_db = not db_probe.exists()
        probe_dir.rmdir()

    except OSError as exc:
        work_error = str(exc)

    missing = []
    if not dotnet:
        missing.append("dotnet")
    if not models_dll:
        missing.append("Models.dll")
    if base_value:
        if not base_template or not base_template.is_file():
            missing.append("base_template")
    elif not templates or not all(item["exists"] for item in templates.values()):
        missing.append("APSIMX template")
    if not weather_files or not all(item["exists"] for item in weather_files.values()):
        missing.append("weather file")
    if not work_create:
        missing.append("writable work_dir")

    return {
        "dotnet": dotnet,
        "dotnet_configured": dotnet_value,
        "models_dll": str(models_dll) if models_dll else None,
        "models_dll_configured": apsim.get("models_dll"),
        "base_template": {"path": str(base_template) if base_template else None, "exists": bool(base_template and base_template.is_file())},
        "apsimx_templates": templates,
        "weather_files": weather_files,
        "can_create_temporary_apsimx": work_create,
        "can_delete_old_db": work_delete_db,
        "can_read_sqlite_db": sqlite_read,
        "work_error": work_error,
        "missing": missing,
        "available": not missing,
    }


def ensure_runtime(config: dict[str, Any]) -> dict[str, Any]:
    diagnosis = runtime_diagnostics(config)

    if not diagnosis["available"]:
        raise ApsimUnavailableError(
            "APSIMX runtime unavailable: "
            f"dotnet={diagnosis['dotnet']!r}, Models.dll={diagnosis['models_dll']!r}"
        )

    return diagnosis


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _remove_manager_by_name(obj, name: str) -> int:
    removed = 0

    if isinstance(obj, dict):
        children = obj.get("Children")

        if isinstance(children, list):
            before = len(children)
            children[:] = [
                child for child in children
                if not (isinstance(child, dict) and child.get("Name") == name)
            ]
            removed += before - len(children)

        for value in obj.values():
            removed += _remove_manager_by_name(value, name)

    elif isinstance(obj, list):
        for item in obj:
            removed += _remove_manager_by_name(item, name)

    return removed


def _prepare_for_batch_run(path: Path, config: dict[str, Any], logger) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))

    removed = _remove_manager_by_name(data, "Synchroniser")
    if removed:
        logger.info("Removed Synchroniser from %s for Models.dll batch run", path)

    fixed_reports = 0
    for node in _walk(data):
        if node.get("Name") == "Report" and "Models.Report" in str(node.get("$type", "")):
            node["EventNames"] = ["[Clock].EndOfDay"]
            node["Enabled"] = True
            fixed_reports += 1

    if fixed_reports:
        logger.info("Fixed Report EventNames in %s: %s", path, fixed_reports)

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    preflight = validate_prepared_apsimx(path, config.get("run_quality", {}))
    logger.info("APSIMX preflight passed for %s", path)
    for warning in preflight["warnings"]:
        logger.warning("APSIMX preflight warning for %s: %s", path, warning)


def find_db_for_apsimx(apsimx_file: Path) -> Path | None:
    candidates = [
        apsimx_file.with_suffix(".db"),
        apsimx_file.parent / "DataStore.db",
        apsimx_file.parent / f"{apsimx_file.stem}.db",
    ]

    for path in candidates:
        if path.exists():
            return path

    dbs = sorted(
        apsimx_file.parent.glob("*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    return dbs[0] if dbs else None


def _delete_old_databases(apsimx_file: Path) -> None:
    candidates = [
        apsimx_file.with_suffix(".db"),
        apsimx_file.parent / "DataStore.db",
        apsimx_file.parent / f"{apsimx_file.stem}.db",
    ]

    for path in candidates:
        if path.exists():
            path.unlink()


def run_apsim_files(files: list[Path], config: dict[str, Any], logger) -> int:
    runtime = ensure_runtime(config)

    for path in files:
        if not path.is_file():
            raise FileNotFoundError(f"Prepared APSIMX file not found: {path}")

        _delete_old_databases(path)
        _prepare_for_batch_run(path, config, logger)

    days = 0

    for path in files:
        command = [
            runtime["dotnet"],
            runtime["models_dll"],
            str(path),
        ]

        logger.info("Running APSIMX via Models.dll: %s", path)

        result = subprocess.run(
            command,
            cwd=str(config["_root"]),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        if result.stdout.strip():
            logger.info(result.stdout)

        if result.returncode != 0:
            output = result.stdout.strip()
            output_tail = output[-4000:] if output else "APSIMX produced no diagnostic output"
            raise RuntimeError(
                f"APSIMX batch run failed for {path} with code {result.returncode}. "
                f"Diagnostic output (tail):\n{output_tail}"
            )

        db_path = find_db_for_apsimx(path)

        if not db_path:
            raise RuntimeError(f"APSIMX finished but no SQLite database was found for {path}")

        logger.info("APSIMX database found: %s", db_path)

        days += 1

    return days
