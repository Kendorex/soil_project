import json
import logging
import sqlite3
from pathlib import Path

import src.cultivar_calibration as calibration_module

from src.cultivar_calibration import (
    CalibrationObservation,
    add_calibration_diagnostic_report_expressions,
    _date_from_excel,
    _calibration_simulation_start,
    _patch_year_specific_initial_water,
    _ensure_calibration_cultivar,
    calibration_candidate_id,
    create_calibration_copy,
    maturity_upper_bound_metrics,
    parameter_combinations,
    read_calibration_daily_report,
    read_phenology_result,
    run_calibration_plan,
)


def test_year_specific_initial_water_changes_only_requested_temporary_layers():
    data = {"Children": [
        {"Name": "Water", "InitialValues": [0.5, 0.5, 0.5]},
        {"Name": "Physical", "AirDry": [0.1, 0.2, 0.3], "DUL": [0.5, 0.6, 0.7]},
    ]}
    values = _patch_year_specific_initial_water(data, {
        "initial_water_airdry_to_dul_fraction_by_year": {2021: 0.25},
        "initial_water_layers_to_adjust": 2,
    }, 2021)
    assert values == [0.2, 0.3, 0.5]


def test_missing_calibration_cultivar_is_cloned_from_configured_source():
    data = {"Children": [{
        "Name": "Wheat",
        "Children": [{
            "Name": "Zlata",
            "$type": "Models.PMF.Cultivar, Models",
            "Command": ["[Phenology].CAMP.FLNparams.MinLN = 5"],
            "Children": [],
        }],
    }]}
    _ensure_calibration_cultivar(data, {
        "cultivar": "Irgina",
        "source_template_cultivar": "Zlata",
    })
    cultivars = data["Children"][0]["Children"]
    assert [item["Name"] for item in cultivars] == ["Zlata", "Irgina"]
    cultivars[1]["Command"][0] = "changed"
    assert cultivars[0]["Command"][0] != "changed"


def test_date_parser_supports_excel_decimal_dates():
    assert _date_from_excel(11.05, 2020).isoformat() == "2020-05-11"
    assert _date_from_excel("3.05", 2023).isoformat() == "2023-05-03"


def test_parameter_grid_is_cartesian_and_candidate_id_is_stable():
    config = {
        "calibration": {
            "parameter_grid": {
                "MinLN": {"values": [6, 7]},
                "PpLN": {"values": [1, 2, 3]},
            }
        }
    }
    combinations = parameter_combinations(config)
    assert len(combinations) == 6
    assert calibration_candidate_id({"MinLN": 7.0, "PpLN": 2.0}) == calibration_candidate_id(
        {"PpLN": 2.0, "MinLN": 7.0}
    )


def test_failed_candidate_is_not_retried_for_remaining_years(tmp_path, monkeypatch):
    calls = []

    def fail_run(files, _config, _logger):
        calls.append(files[0])
        raise RuntimeError("structural APSIM failure")

    monkeypatch.setattr(calibration_module, "run_apsim_files", fail_run)
    plan = [
        {
            "candidate_id": "bad-candidate",
            "year": year,
            "dataset_role": "calibration",
            "working_apsimx": f"work/{year}.apsimx",
            "observed_heading_date": f"{year}-07-01",
            "observed_harvest_date": f"{year}-08-20",
            "MinLN": 3.5,
        }
        for year in (2007, 2008)
    ]
    optimizer = {"_root": tmp_path}
    calibration = {
        "_root": tmp_path,
        "calibration": {
            "parameter_grid": {"MinLN": {"values": [3.5]}},
            "selection": {"mode": "heading_rmse"},
        },
    }

    rows, summaries = run_calibration_plan(
        plan, optimizer, calibration, logging.getLogger("calibration-test")
    )

    assert len(calls) == 1
    assert rows[0]["run_status"] == "failed"
    assert rows[1]["run_status"] == "skipped_after_candidate_failure"
    assert summaries[0]["actual_failed_runs"] == 1
    assert summaries[0]["skipped_runs"] == 1
    assert summaries[0]["failed_runs"] == 2


def test_harvest_date_is_an_upper_bound_for_maturity():
    from datetime import date

    early = maturity_upper_bound_metrics(date(2023, 8, 10), date(2023, 8, 20))
    assert early["maturity_status"] == "on_or_before_harvest"
    assert early["maturity_late_days"] == 0
    assert early["maturity_slack_days"] == 10

    late = maturity_upper_bound_metrics(date(2023, 8, 27), date(2023, 8, 20))
    assert late["maturity_status"] == "late"
    assert late["maturity_late_days"] == 7
    assert late["maturity_error_days"] == 7

    missing = maturity_upper_bound_metrics(None, date(2023, 8, 20))
    assert missing["maturity_status"] == "maturity_not_reached"
    assert missing["maturity_late_days"] is None


def test_calibration_start_can_be_relative_to_sowing_for_water_sensitivity():
    from datetime import date

    observation = CalibrationObservation(
        2023, 1, "Злата", "N30", 2.0, date(2023, 5, 3), date(2023, 7, 3),
        None, date(2023, 8, 21), None, None, 80, 5, 400, "validation",
    )
    assert _calibration_simulation_start({}, observation) == date(2023, 1, 1)
    assert _calibration_simulation_start(
        {"simulation_start_days_before_sowing": 14}, observation
    ) == date(2023, 4, 19)


def test_verified_new_cultivar_command_can_be_added_to_temporary_copy(tmp_path):
    template = tmp_path / "standart.apsimx"
    weather = tmp_path / "weather_2020.met"
    weather.write_text("weather", encoding="utf-8")
    data = {
        "Name": "Simulation",
        "Children": [
            _node("Clock", Start="2021-01-01", End="2022-12-31"),
            _node("Weather", FileName="old.met"),
            _node("DataStore", FileName="old.db"),
            {"Name": "Report", "$type": "Models.Report, Models", "VariableNames": [], "EventNames": []},
            _node("Sow", Parameters=[
                {"Key": "StartDate", "Value": "1-may"}, {"Key": "EndDate", "Value": "30-may"},
                {"Key": "MinESW", "Value": "1"}, {"Key": "MinRain", "Value": "1"},
                {"Key": "CultivarName", "Value": "Zlata"}, {"Key": "Population", "Value": "250"},
                {"Key": "SowingDepth", "Value": "30"}, {"Key": "RowSpacing", "Value": "250"},
            ]),
            _node("Fertilise", Parameters=[
                {"Key": "FertiliserType", "Value": "NO3N"}, {"Key": "Amount", "Value": "0"},
            ]),
            _node("Zlata", Command=[]),
        ],
    }
    template.write_text(json.dumps(data), encoding="utf-8")
    observation = CalibrationObservation(
        2020, 1, "Злата", "N30", 2.0,
        __import__("datetime").date(2020, 5, 1), __import__("datetime").date(2020, 7, 1),
        None, __import__("datetime").date(2020, 8, 20), None, None, 80, 5, 400, "calibration",
    )
    calibration = {
        "_root": tmp_path,
        "calibration": {
            "base_template": "standart.apsimx", "weather_files": {2020: "weather_2020.met"},
            "cultivar": "Zlata", "sowing_depth_mm": 30, "row_spacing_mm": 250,
            "fertilizer": {"type_apsimx": "NH4NO3N", "amount_kg_ha": 30},
            "parameter_grid": {
                "GrainFillingTarget": {
                    "command": "[Phenology].GrainFilling.Target.FixedValue",
                    "values": [450], "allow_new_command": True,
                }
            },
        },
    }
    optimizer = {"apsim": {"manager_names": {"sowing": "Sow", "fertilizer": "Fertilise"}}}
    destination = tmp_path / "work.apsimx"
    create_calibration_copy(optimizer, calibration, observation, {"GrainFillingTarget": 450}, destination)
    staged = json.loads(destination.read_text(encoding="utf-8"))
    cultivar = next(node for node in _walk_for_test(staged) if isinstance(node, dict) and node.get("Name") == "Zlata")
    assert cultivar["Command"] == ["[Phenology].GrainFilling.Target.FixedValue = 450"]


def _node(name, **values):
    return {"Name": name, **values}


def test_calibration_copy_uses_exact_date_and_does_not_modify_template(tmp_path):
    template = tmp_path / "standart.apsimx"
    weather = tmp_path / "weather_2020.met"
    weather.write_text("weather", encoding="utf-8")
    data = {
        "Name": "Simulations",
        "Children": [
            {
                "Name": "Simulation",
                "Children": [
                    _node("Clock", Start="2021-01-01T00:00:00", End="2022-12-31T00:00:00"),
                    _node("Weather", FileName="old.met"),
                    _node("DataStore", FileName="old.db"),
                    {"Name": "Report", "$type": "Models.Report, Models", "VariableNames": [], "EventNames": []},
                    _node(
                        "Sow",
                        Parameters=[
                            {"Key": "StartDate", "Value": "1-may"},
                            {"Key": "EndDate", "Value": "30-may"},
                            {"Key": "MinESW", "Value": "100"},
                            {"Key": "MinRain", "Value": "25"},
                            {"Key": "CultivarName", "Value": "Zlata"},
                            {"Key": "Population", "Value": "250"},
                            {"Key": "SowingDepth", "Value": "30"},
                            {"Key": "RowSpacing", "Value": "250"},
                        ],
                    ),
                    _node(
                        "Fertilise",
                        Parameters=[
                            {"Key": "FertiliserType", "Value": "NO3N"},
                            {"Key": "Amount", "Value": "160"},
                        ],
                    ),
                    _node(
                        "Zlata",
                        Command=[
                            "[Phenology].CAMP.FLNparams.MinLN = 7",
                            "[Phenology].CAMP.FLNparams.PpLN = 2",
                        ],
                    ),
                ],
            }
        ],
    }
    template.write_text(json.dumps(data), encoding="utf-8")
    original = template.read_bytes()
    observation = CalibrationObservation(
        2020, 6, "Злата", "N30", 2.21,
        __import__("datetime").date(2020, 5, 11),
        __import__("datetime").date(2020, 7, 14),
        None, None, None, 240, 81, 6, 486, "calibration",
    )
    calibration = {
        "_root": tmp_path,
        "calibration": {
            "base_template": "standart.apsimx",
            "weather_files": {2020: "weather_2020.met"},
            "cultivar": "Zlata",
            "sowing_depth_mm": 30,
            "row_spacing_mm": 250,
            "fertilizer": {"type_apsimx": "NH4NO3N", "amount_kg_ha": 30},
            "parameter_grid": {
                "MinLN": {"command": "[Phenology].CAMP.FLNparams.MinLN", "values": [6]},
                "PpLN": {"command": "[Phenology].CAMP.FLNparams.PpLN", "values": [1]},
            },
        },
    }
    optimizer = {"apsim": {"manager_names": {"sowing": "Sow", "fertilizer": "Fertilise"}}}
    destination = tmp_path / "work" / "2020.apsimx"
    create_calibration_copy(optimizer, calibration, observation, {"MinLN": 6, "PpLN": 1}, destination)
    assert template.read_bytes() == original
    staged = json.loads(destination.read_text(encoding="utf-8"))
    nodes = {node.get("Name"): node for node in _walk_for_test(staged) if isinstance(node, dict)}
    sow = {item["Key"]: item["Value"] for item in nodes["Sow"]["Parameters"]}
    assert sow["StartDate"] == sow["EndDate"] == "11-may"
    assert sow["MinESW"] == sow["MinRain"] == "-1"
    assert sow["Population"] == "486"
    assert nodes["Clock"]["Start"].startswith("2020-")
    assert nodes["Zlata"]["Command"][-2:] == [
        "[Phenology].CAMP.FLNparams.MinLN = 6",
        "[Phenology].CAMP.FLNparams.PpLN = 1",
    ]


def test_calibration_copy_can_set_weather_instrument_height_only_in_temporary_file(tmp_path):
    template = tmp_path / "standart.apsimx"
    weather = tmp_path / "weather.met"
    weather.write_text("weather", encoding="utf-8")
    data = {
        "Name": "Simulation", "Children": [
            _node("Clock", Start="2021-01-01", End="2021-12-31"),
            _node("Weather", FileName="old.met"), _node("DataStore", FileName="old.db"),
            _node("MicroClimate", ReferenceHeight=2.0),
            {"Name": "Report", "$type": "Models.Report, Models", "VariableNames": [], "EventNames": []},
            _node("Sow", Parameters=[
                {"Key": "StartDate", "Value": "1-may"}, {"Key": "EndDate", "Value": "30-may"},
                {"Key": "MinESW", "Value": "1"}, {"Key": "MinRain", "Value": "1"},
                {"Key": "CultivarName", "Value": "Zlata"}, {"Key": "Population", "Value": "250"},
                {"Key": "SowingDepth", "Value": "30"}, {"Key": "RowSpacing", "Value": "250"},
            ]),
            _node("Fertilise", Parameters=[
                {"Key": "FertiliserType", "Value": "NO3N"}, {"Key": "Amount", "Value": "0"},
            ]),
            _node("Zlata", Command=["[Phenology].CAMP.FLNparams.MinLN = 7"]),
        ]
    }
    template.write_text(json.dumps(data), encoding="utf-8")
    original = template.read_bytes()
    observation = CalibrationObservation(
        2021, 1, "Злата", "N30", 1.5,
        __import__("datetime").date(2021, 5, 18), __import__("datetime").date(2021, 7, 19),
        None, __import__("datetime").date(2021, 8, 14), None, None, 80, 5, 400, "calibration",
    )
    calibration = {"_root": tmp_path, "calibration": {
        "base_template": "standart.apsimx", "weather_files": {2021: "weather.met"},
        "cultivar": "Zlata", "sowing_depth_mm": 30, "row_spacing_mm": 250,
        "microclimate_reference_height_m": 10,
        "fertilizer": {"type_apsimx": "NH4NO3N", "amount_kg_ha": 30},
        "parameter_grid": {"MinLN": {"command": "[Phenology].CAMP.FLNparams.MinLN", "values": [5]}},
    }}
    optimizer = {"apsim": {"manager_names": {"sowing": "Sow", "fertilizer": "Fertilise"}}}
    destination = tmp_path / "temporary.apsimx"
    create_calibration_copy(optimizer, calibration, observation, {"MinLN": 5}, destination)
    staged = json.loads(destination.read_text(encoding="utf-8"))
    nodes = {node.get("Name"): node for node in _walk_for_test(staged) if isinstance(node, dict)}
    assert nodes["MicroClimate"]["ReferenceHeight"] == 10.0
    assert template.read_bytes() == original


def _walk_for_test(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_for_test(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_for_test(child)


def test_phenology_reader_falls_back_to_zadok_50(tmp_path):
    db = tmp_path / "result.db"
    connection = sqlite3.connect(db)
    try:
        connection.execute(
            'CREATE TABLE Report("Clock.Today" TEXT, '
            '"Wheat.Phenology.CurrentStageName" TEXT, '
            '"Wheat.Phenology.Zadok.Stage" REAL, Yield REAL)'
        )
        connection.execute("INSERT INTO Report VALUES ('2020-07-13', 'FlagLeaf', 49, 0)")
        connection.execute("INSERT INTO Report VALUES ('2020-07-14', 'UnknownHeadingLabel', 50, 0)")
        connection.commit()
    finally:
        connection.close()
    config = {
        "calibration": {
            "reporter": {
                "stage_column": "Wheat.Phenology.CurrentStageName",
                "zadok_column": "Wheat.Phenology.Zadok.Stage",
                "heading_zadok_min": 50,
                "date_candidates": ["Clock.Today"],
                "heading_stage_names": ["Heading"],
                "maturity_stage_names": ["HarvestRipe"],
            }
        }
    }
    result = read_phenology_result(db, config)
    assert result["simulated_heading_date"].isoformat() == "2020-07-14"
    assert result["heading_detection_method"] == "zadok_gte_50"


def test_daily_calibration_diagnostics_use_only_existing_report_columns(tmp_path):
    db = tmp_path / "diagnostic.db"
    connection = sqlite3.connect(db)
    try:
        connection.execute(
            'CREATE TABLE Report("Clock.Today" TEXT, '
            '"Wheat.Phenology.CurrentStageName" TEXT, "Wheat.LAI" REAL, '
            'PAW REAL, MineralN REAL, Yield REAL)'
        )
        connection.execute("INSERT INTO Report VALUES ('2021-05-01', 'Sowing', 0, 20, 15, 0)")
        connection.execute("INSERT INTO Report VALUES ('2021-09-01', 'Maturity', 0.2, 5, 3, 900)")
        connection.commit()
    finally:
        connection.close()
    config = {
        "calibration": {"reporter": {
            "stage_column": "Wheat.Phenology.CurrentStageName",
            "date_candidates": ["Clock.Today"],
            "yield_source_unit": "kg_per_ha",
        }}
    }
    plan_row = {"candidate_id": "T1", "year": 2021, "dataset_role": "calibration"}
    daily, summary = read_calibration_daily_report(db, config, plan_row)
    assert len(daily) == 2
    assert summary["PAW_min"] == 5
    assert summary["MineralN_last"] == 3
    assert summary["yield_max_t_ha"] == 0.9
    assert summary["direct_water_stress_index_status"] == "not_reported"
    assert summary["Wheat.AboveGround.Wt_status"] == "missing"


def test_diagnostic_expressions_are_added_only_to_temporary_copy(tmp_path):
    source = tmp_path / "source.apsimx"
    temporary = tmp_path / "temporary.apsimx"
    data = {"Name": "Simulation", "Children": [
        {"Name": "Report", "$type": "Models.Report, Models", "VariableNames": ["[Clock].Today"]}
    ]}
    source.write_text(json.dumps(data), encoding="utf-8")
    temporary.write_bytes(source.read_bytes())
    config = {"calibration": {"diagnostic_report_expressions": ["[Wheat].Leaf.Fw as LeafFw"]}}
    assert add_calibration_diagnostic_report_expressions(temporary, config) == ["[Wheat].Leaf.Fw as LeafFw"]
    assert json.loads(source.read_text(encoding="utf-8"))["Children"][0]["VariableNames"] == ["[Clock].Today"]
    assert json.loads(temporary.read_text(encoding="utf-8"))["Children"][0]["VariableNames"][-1] == "[Wheat].Leaf.Fw as LeafFw"
