import hashlib
import json
from copy import deepcopy
from pathlib import Path

from src.config import load_config
from src.optimizer_grid import (
    active_weather_scenarios,
    build_plan,
    build_technology_plan,
    get_base_template,
    validate_inputs,
)
from src.search_space import Technology, build_search_space


def fixed_baseline_config():
    config = deepcopy(load_config())
    for name in ("cultivars", "populations", "depths", "rows"):
        config["optimization"]["search_space"][name]["enabled"] = False
    for scenario in config["weather_scenarios"].values():
        scenario["enabled"] = True
    return config


def test_config_uses_one_base_template():
    config = load_config()
    template = get_base_template(config)
    assert template.name == "standart.apsimx"
    assert template.as_posix().endswith("simulation/standart.apsimx")
    assert template.is_file()


def test_all_optimizer_configs_inherit_fail_closed_run_quality():
    for name in (
        "configs/optimizer_config.yaml",
        "configs/optimizer_config_demo.yaml",
        "configs/optimizer_config_quick_two_cultivars_no_cold.yaml",
    ):
        quality = load_config(name)["run_quality"]
        assert quality["require_sowing"] is True
        assert quality["require_harvest"] is True
        assert quality["allow_zero_yield"] is False
        assert quality["require_relative_paths"] is True


def test_synchroniser_is_preserved_but_disabled_in_base_template():
    data = json.loads(get_base_template(load_config()).read_text(encoding="utf-8"))

    def walk(node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    synchronisers = [node for node in walk(data) if node.get("Name") == "Synchroniser"]
    assert len(synchronisers) == 1
    assert synchronisers[0]["Enabled"] is False


def test_main_template_and_search_space_contain_both_calibrated_cultivars():
    config = load_config()
    inputs = validate_inputs(config, lambda _message: None)
    space = build_search_space(config, inputs)
    data = json.loads(get_base_template(config).read_text(encoding="utf-8"))

    def walk(node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    wheat = next(
        node for node in walk(data)
        if node.get("Name") == "Wheat" and "Models.PMF.Plant" in node.get("$type", "")
    )
    cultivar_names = {
        child["Name"] for child in wheat.get("Children", [])
        if "Models.PMF.Cultivar" in child.get("$type", "")
    }

    assert inputs["cultivars"] == ["Zlata", "Irgina"]
    assert space.cultivars == ("Zlata", "Irgina")
    assert cultivar_names == {"Zlata", "Irgina"}


def test_irgina_can_be_staged_without_modifying_base_template():
    config = load_config()
    inputs = validate_inputs(config, lambda _message: None)
    template = get_base_template(config)
    before = hashlib.sha256(template.read_bytes()).hexdigest()
    defaults = config["apsim"]["defaults"]
    technology = Technology(
        defaults["sowing_date"], "Irgina", defaults["population"],
        defaults["depth"], defaults["row"], "F0_no_fertilizer",
    )

    rows = build_technology_plan(
        config, inputs, technology, "pytest_irgina", lambda _message: None
    )
    staged = json.loads((config["_root"] / rows[0]["working_apsimx"]).read_text(encoding="utf-8"))

    def walk(node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    sow = next(node for node in walk(staged) if node.get("Name") == "Sow using a variable rule")
    parameters = {item["Key"]: item["Value"] for item in sow["Parameters"]}
    assert parameters["CultivarName"] == "Irgina"
    assert hashlib.sha256(template.read_bytes()).hexdigest() == before


def test_dry_run_plan_uses_one_template_and_four_weather_files():
    config = fixed_baseline_config()
    inputs = validate_inputs(config, lambda _message: None)
    plans = build_plan(config, inputs, stage_files=True, log=lambda _message: None, stage_name="pytest_base_template")

    assert len(plans) == 116
    assert {row["template_file"] for row in plans} == {"simulation/standart.apsimx"}
    assert {row["scenario_id"] for row in plans} == {"s1_dojdey", "s2_holod", "s3_norm", "s4_zasuha"}
    assert {
        (row["scenario_id"], row["weather_file"])
        for row in plans
        if row["fertilizer_scheme_id"] == "F0_no_fertilizer"
    } == {
        ("s1_dojdey", "data/weather/weather_s1_dojdey.met"),
        ("s2_holod", "data/weather/weather_s2_holod.met"),
        ("s3_norm", "data/weather/weather_s3_norm.met"),
        ("s4_zasuha", "data/weather/weather_s4_zasuha.met"),
    }


def test_working_apsimx_and_db_names_are_distinct_by_scenario():
    config = fixed_baseline_config()
    inputs = validate_inputs(config, lambda _message: None)
    plans = build_plan(config, inputs, stage_files=True, log=lambda _message: None, stage_name="pytest_names")
    f0_rows = [row for row in plans if row["fertilizer_scheme_id"] == "F0_no_fertilizer"]

    apsimx_names = {Path(row["working_apsimx"]).name for row in f0_rows}
    db_names = {Path(row["working_apsimx"]).with_suffix(".db").name for row in f0_rows}

    assert apsimx_names == {"s1_dojdey.apsimx", "s2_holod.apsimx", "s3_norm.apsimx", "s4_zasuha.apsimx"}
    assert db_names == {"s1_dojdey.db", "s2_holod.db", "s3_norm.db", "s4_zasuha.db"}


def test_base_template_is_not_modified_by_staging():
    config = fixed_baseline_config()
    template = get_base_template(config)
    before = hashlib.sha256(template.read_bytes()).hexdigest()

    inputs = validate_inputs(config, lambda _message: None)
    build_plan(config, inputs, stage_files=True, log=lambda _message: None, stage_name="pytest_template_unchanged")

    after = hashlib.sha256(template.read_bytes()).hexdigest()
    assert after == before


def test_staged_scenario_copy_has_scenario_weather_and_datastore_name():
    config = fixed_baseline_config()
    inputs = validate_inputs(config, lambda _message: None)
    plans = build_plan(config, inputs, stage_files=True, log=lambda _message: None, stage_name="pytest_patch")
    row = next(row for row in plans if row["scenario_id"] == "s2_holod" and row["fertilizer_scheme_id"] == "F0_no_fertilizer")
    data = json.loads((config["_root"] / row["working_apsimx"]).read_text(encoding="utf-8"))

    def walk(node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    weather = next(node for node in walk(data) if node.get("Name") == "Weather")
    datastore = next(node for node in walk(data) if node.get("Name") == "DataStore")

    assert weather["FileName"].endswith("data/weather/weather_s2_holod.met")
    assert datastore["FileName"] == "s2_holod.db"


def test_disabled_weather_scenario_is_kept_in_config_but_not_planned():
    config = deepcopy(load_config())
    config["weather_scenarios"]["s2_holod"]["enabled"] = False
    for name in ("cultivars", "populations", "depths", "rows"):
        config["optimization"]["search_space"][name]["enabled"] = False
    inputs = validate_inputs(config, lambda _message: None)

    plans = build_plan(
        config,
        inputs,
        stage_files=False,
        log=lambda _message: None,
        stage_name="pytest_disabled_scenario",
    )

    assert set(config["weather_scenarios"]) == {
        "s1_dojdey", "s2_holod", "s3_norm", "s4_zasuha",
    }
    assert list(active_weather_scenarios(config)) == [
        "s1_dojdey", "s3_norm", "s4_zasuha",
    ]
    assert len(plans) == 87
    assert {row["scenario_id"] for row in plans} == {
        "s1_dojdey", "s3_norm", "s4_zasuha",
    }
