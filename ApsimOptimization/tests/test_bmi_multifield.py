import numpy as np
import pytest

import src.bmi_multifield as bmi_module
from src.bmi_multifield import BmiApsimOptimization


def ready_adapter(monkeypatch, tmp_path, result=None):
    config = {"output_dir": str(tmp_path), "currency": "RUB"}
    monkeypatch.setattr(bmi_module, "load_config", lambda _: config)
    monkeypatch.setattr(bmi_module, "inventory", lambda _: {"fields": {}})
    monkeypatch.setattr(bmi_module, "configuration_errors", lambda *_: [])
    if result is None:
        result = {
            "status": "ok",
            "evaluated": 8,
            "failed": 1,
            "pareto": [
                {"yield_dm_kg": 1200.0, "net_profit": 500.0,
                 "soil_deviation": 0.0, "F": [-1200.0, -500.0, 0.0]},
                {"F": [-1100.0, -650.0, 0.2]},
            ],
        }
    monkeypatch.setattr(bmi_module, "optimize", lambda *_: result)
    model = BmiApsimOptimization()
    model.initialize("test.yaml")
    return model


def test_component_and_variable_metadata_are_available_without_apsim():
    model = BmiApsimOptimization()
    assert model.get_component_name() == "APSIM Multifield NSGA-II Optimization"
    assert model.get_input_var_names() == ()
    assert model.get_output_var_names() == (
        "annual_yield_dm_kg",
        "net_profit",
        "soil_interval_violation",
        "evaluated_candidate_count",
        "failed_candidate_count",
        "pareto_solution_count",
    )


def test_update_exposes_pareto_objectives(monkeypatch, tmp_path):
    model = ready_adapter(monkeypatch, tmp_path)
    assert model.get_status() == "initialized"

    model.update()

    np.testing.assert_allclose(model.get_value("annual_yield_dm_kg"), [1200.0, 1100.0])
    np.testing.assert_allclose(model.get_value("net_profit"), [500.0, 650.0])
    np.testing.assert_allclose(model.get_value("soil_interval_violation"), [0.0, 0.2])
    assert model.get_value("evaluated_candidate_count").tolist() == [8]
    assert model.get_value("failed_candidate_count").tolist() == [1]
    assert model.get_value("pareto_solution_count").tolist() == [2]
    assert model.get_grid_size(0) == 2
    assert model.get_current_time() == model.get_end_time() == 1.0
    assert model.get_status() == "ok"
    assert model.get_var_units("net_profit") == "RUB"


def test_run_returns_defensive_native_result(monkeypatch, tmp_path):
    model = ready_adapter(monkeypatch, tmp_path)
    result = model.run()
    result["pareto"].clear()
    assert len(model.get_result()["pareto"]) == 2


def test_initialize_reports_configuration_errors(monkeypatch):
    monkeypatch.setattr(bmi_module, "load_config", lambda _: {"output_dir": "out"})
    monkeypatch.setattr(bmi_module, "inventory", lambda _: {})
    monkeypatch.setattr(
        bmi_module, "configuration_errors", lambda *_: ["currency is required"])
    model = BmiApsimOptimization()
    with pytest.raises(ValueError, match="currency is required"):
        model.initialize("test.yaml")


def test_finalize_resets_lifecycle(monkeypatch, tmp_path):
    model = ready_adapter(monkeypatch, tmp_path)
    model.update()
    model.finalize()
    assert model.get_status() == "not_initialized"
    assert model.get_grid_size(0) == 0
    with pytest.raises(RuntimeError, match="not initialized"):
        model.update()
