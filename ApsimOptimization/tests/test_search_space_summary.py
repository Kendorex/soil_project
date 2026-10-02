from types import SimpleNamespace

from run_optimizer import _select_technology
from src.search_space import build_search_space, search_space_summary
from src.config import load_config


def make_config():
    return {
        "apsim": {"defaults": {"sowing_date": "15-aug..30-oct", "population": 250, "depth": 30, "row": 250}},
        "cultivar": {"default": "TestCultivar"},
        "optimization": {"search_space": {
            "sowing_dates": {"enabled": False, "values": ["10-sep", "20-sep", "30-sep"]},
            "cultivars": {"enabled": False, "values": ["TestCultivar"]},
            "populations": {"enabled": False, "values": [250, 300, 350, 400, 450, 500]},
            "depths": {"enabled": False, "values": [30]},
            "rows": {"enabled": False, "values": [250]},
        }},
    }


INPUTS = {"cultivars": ["TestCultivar"], "schemes": [{"id": "F0"}, {"id": "F1"}]}


def test_normal_space_marks_fixed_parameters():
    summary = search_space_summary(build_search_space(make_config(), INPUTS), 4)
    assert summary["parameters"]["population"]["status"] == "fixed"
    assert summary["technology_count"] == 2
    assert summary["apsim_run_count"] == 8


def test_expanded_space_uses_preview_values():
    summary = search_space_summary(build_search_space(make_config(), INPUTS, expanded=True), 4)
    assert summary["parameters"]["sowing_date"]["count"] == 3
    assert summary["parameters"]["population"]["count"] == 6
    assert summary["technology_count"] == 36
    assert summary["apsim_run_count"] == 144


def test_configured_management_space_varies_population_depth_and_row():
    config = make_config()
    config["optimization"]["search_space"]["populations"]["enabled"] = True
    config["optimization"]["search_space"]["depths"] = {
        "enabled": True, "values": [20, 30, 40, 50, 60],
    }
    config["optimization"]["search_space"]["rows"] = {
        "enabled": True, "values": [125, 150, 250],
    }
    summary = search_space_summary(build_search_space(config, INPUTS), 4)
    assert summary["parameters"]["population"]["count"] == 6
    assert summary["parameters"]["depth"]["count"] == 5
    assert summary["parameters"]["row"]["count"] == 3
    assert summary["technology_count"] == 180
    assert summary["apsim_run_count"] == 720


def test_project_config_expands_to_5220_technologies_and_three_active_scenarios():
    config = load_config()
    inputs = {
        "cultivars": ["Zlata", "Irgina"],
        "schemes": [{"id": f"F{i}"} for i in range(29)],
    }
    summary = search_space_summary(build_search_space(config, inputs), 3)

    assert summary["parameters"]["cultivar"]["values"] == ["Zlata", "Irgina"]
    assert summary["parameters"]["population"]["values"] == [250, 300, 350, 400, 450, 500]
    assert summary["parameters"]["depth"]["values"] == [20, 30, 40, 50, 60]
    assert summary["parameters"]["row"]["values"] == [125, 150, 250]
    assert summary["technology_count"] == 5220
    assert summary["apsim_run_count"] == 15660


def test_single_run_selection_keeps_config_defaults_when_axes_vary():
    config = load_config()
    inputs = {
        "cultivars": ["Zlata"],
        "schemes": [{"id": "F0_no_fertilizer"}],
    }
    args = SimpleNamespace(
        sowing_date=None,
        cultivar=None,
        population=None,
        depth=None,
        row_spacing=None,
    )

    technology = _select_technology(
        build_search_space(config, inputs),
        "F0_no_fertilizer",
        config,
        args,
    )

    assert technology.population == 250
    assert technology.depth == 30
    assert technology.row == 250
