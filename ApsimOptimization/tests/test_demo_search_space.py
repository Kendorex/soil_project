from src.config import load_config, load_inputs
from src.optimizer_grid import active_weather_scenarios
from src.search_space import build_search_space, search_space_summary


def test_demo_config_has_small_explicit_search_space():
    config = load_config("configs/optimizer_config_demo.yaml")
    inputs = load_inputs(config)
    space = build_search_space(config, inputs)
    summary = search_space_summary(space, len(active_weather_scenarios(config)))

    assert space.dimensions == (1, 2, 2, 2, 2, 4)
    assert summary["technology_count"] == 64
    assert summary["apsim_run_count"] == 192
    assert space.fertilizer_scheme_ids == (
        "F0_no_fertilizer",
        "F8_NH4NO3N_30_sowing",
        "F12_NH4NO3N_150_sowing",
        "F14_NH4NO3N_210_sowing",
    )


def test_unknown_demo_fertilizer_scheme_is_rejected():
    config = load_config("configs/optimizer_config_demo.yaml")
    inputs = load_inputs(config)
    config["optimization"]["search_space"]["fertilizer_schemes"]["values"] = ["UNKNOWN"]

    try:
        build_search_space(config, inputs)
    except ValueError as exc:
        assert "Unknown fertilizer schemes" in str(exc)
    else:
        raise AssertionError("Unknown fertilizer scheme must be rejected")
