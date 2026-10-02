import json

import pytest

from scripts.evaluate_oats_fertilizer_form import (
    HISTORICAL_APPLY, error_metrics, patch_2023_fertilizer_form,
    verify_non_target_yields,
)


def _model():
    manager = {
        "$type": "Models.Manager, Models",
        "Name": "Fertilise at sowing",
        "CodeArray": [HISTORICAL_APPLY],
        "Children": [],
    }
    zone = {
        "$type": "Models.Core.Zone, Models", "Name": "Field2",
        "Children": [manager],
    }
    simulation = {
        "$type": "Models.Core.Simulation, Models", "Name": "Simulation",
        "Enabled": True, "Children": [zone],
    }
    return {"$type": "Models.Core.Simulations, Models", "Name": "Simulations",
            "Children": [simulation]}


def test_patch_changes_only_matching_event_type(tmp_path):
    path = tmp_path / "candidate.apsimx"
    path.write_text(json.dumps(_model()), encoding="utf-8")
    changes = patch_2023_fertilizer_form(path, ["Field2"], "2023-05-04", "UreaN")
    patched = json.loads(path.read_text(encoding="utf-8"))
    code = "\n".join(patched["Children"][0]["Children"][0]["Children"][0]["CodeArray"])
    assert 'new DateTime(2023, 5, 4)' in code
    assert '? "UreaN" : FertiliserType' in code
    assert changes[0]["scope"] == "only the matching 2023 sowing event"


def test_metrics_and_non_target_scope():
    metrics = error_metrics([{"error_t_ha": -1.0}, {"error_t_ha": 0.5}])
    assert metrics["mae_t_ha"] == 0.75
    assert metrics["rmse_t_ha"] == pytest.approx((1.25 / 2) ** 0.5)
    results = {
        "NO3N": {"fields": {
            "Field2": {"yield_dm_kg_ha": {"Oats": 1000}},
            "Field3": {"yield_dm_kg_ha": {"Wheat": 2000}},
        }},
        "UreaN": {"fields": {
            "Field2": {"yield_dm_kg_ha": {"Oats": 1500}},
            "Field3": {"yield_dm_kg_ha": {"Wheat": 2000}},
        }},
    }
    assert verify_non_target_yields(results, {"Field2"})["non_target_fields_unchanged"]


def test_scope_rejects_non_target_change():
    results = {
        "NO3N": {"fields": {"Field3": {"yield_dm_kg_ha": {"Wheat": 2000}}}},
        "UreaN": {"fields": {"Field3": {"yield_dm_kg_ha": {"Wheat": 2001}}}},
    }
    with pytest.raises(ValueError, match="non-target"):
        verify_non_target_yields(results, set())
