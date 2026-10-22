"""Verify that the distributed tree can prepare APSIM inputs without executing it."""
import json
from pathlib import Path

from scripts.manual_multiyear_apsim import prepare_package
from src.fertility_constraints import humic_enabled, validate_config


def test_main_package_prepares_all_fields_with_humic_reports(tmp_path):
    root = Path(__file__).resolve().parents[1]
    config = root / "configs/multiyear_rotation_nsga2_2020_2023.yaml"
    package = prepare_package(config, "baseline", None, tmp_path / "baseline")
    snapshot = json.loads((package / "config_snapshot.json").read_text(encoding="utf-8"))
    assert humic_enabled(snapshot["fertility_constraints"])
    assert validate_config(snapshot["fertility_constraints"]) == []
    model = json.loads((package / "prepared/candidate.apsimx").read_text(encoding="utf-8"))
    names = []

    def visit(node):
        if node.get("Name", "").startswith("OptCarbon_"):
            names.append(node["Name"])
        for child in node.get("Children", []):
            visit(child)

    visit(model)
    assert set(names) == {f"OptCarbon_Field{i}" for i in range(1, 11)}
    assert not (package / "prepared/candidate.db").exists()
