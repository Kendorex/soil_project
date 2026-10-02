import sqlite3

from src.oats_physiology import collect_oats_diagnostics, patch_oats_diagnostic_reports


def report(name):
    return {
        "$type": "Models.Report, Models",
        "Name": name,
        "Enabled": True,
        "VariableNames": ["[Clock].Today", "[Oats].LAI as OatsLAI"],
        "EventNames": ["[Clock].EndOfDay"],
        "Children": [],
    }


def zone(name, report_name):
    return {
        "$type": "Models.Core.Zone, Models",
        "Name": name,
        "Enabled": True,
        "Children": [report(report_name)],
    }


def test_patch_oats_reports_changes_only_selected_fields():
    data = {
        "$type": "Models.Core.Simulations, Models",
        "Name": "Simulations",
        "Children": [{
            "$type": "Models.Core.Simulation, Models",
            "Name": "Simulation",
            "Enabled": True,
            "Children": [zone("Field1", "Report1"), zone("Field2", "Report2")],
        }],
    }
    changes = patch_oats_diagnostic_reports(data, ("Field2",))
    field1, field2 = data["Children"][0]["Children"]
    assert field1["Children"][0]["Enabled"] is False
    assert field2["Children"][0]["Enabled"] is True
    assert any("OatsLeafFw" in value for value in field2["Children"][0]["VariableNames"])
    assert changes["reports"]["Field2"]["report_name"] == "Report2"


def test_collect_oats_diagnostics_summarises_stress_and_root_depth(tmp_path):
    database = tmp_path / "candidate.db"
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE _Simulations(ID INTEGER, Name TEXT)')
        connection.execute('INSERT INTO _Simulations VALUES (1, "Simulation")')
        connection.execute(
            'CREATE TABLE Report2('
            'SimulationID INTEGER, "Clock.Today" TEXT, OatsStage TEXT, '
            'OatsLAI REAL, OatsZadok REAL, OatsAGWt REAL, OatsAGN REAL, '
            'OatsYield REAL, OatsLeafFw REAL, OatsLeafFn REAL, '
            'OatsLeafWaterDemand REAL, OatsLeafWaterAllocation REAL, '
            'OatsRootDepth REAL, OatsRootFASW REAL, OatsRootWaterTensionFactor REAL, '
            'OatsPopulation REAL, OatsTotalStemPopulation REAL, '
            'OatsGrainNumber REAL, OatsGrainSize REAL, OatsGrainDMDemand REAL, '
            'OatsGrainPotentialDMAllocation REAL, OatsGrainDMAllocation REAL, '
            '"PAW(1)" REAL, "PAW(2)" REAL, "SW(1)" REAL, '
            '"OatsRootWaterUptake(1)" REAL, "OatsRootNUptake(1)" REAL, '
            'MineralN REAL, Rain REAL)'
        )
        connection.executemany(
            'INSERT INTO Report2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [
                (1, "2023-05-04", "Sowing", 0, 0, 0, 0, 0, 1, 1, 2, 2,
                 0, 1, 1, 250, 250, 0, 0, 0, 0, 0, 10, 20, .2, 0, 0, 30, 1),
                (1, "2023-05-10", "Vegetative", 1, 20, 100, 2, 0, .7, .9, 3, 2,
                 200, .6, .8, 250, 500, 0, 0, 0, 0, 0, 8, 18, .18, 1, .1, 25, 2),
                (1, "2023-07-05", "GrainFill", 3, 75, 500, 8, 1200, .4, .4, 4, 2,
                 700, .3, .5, 250, 500, 4000, .03, 5, 4, 3.2, 5, 12, .15, 2, .2, 10, 3),
            ],
        )
        connection.execute(
            'CREATE TABLE OptHarvest_Field2_Oats(SimulationID INTEGER, Date TEXT, YieldDM REAL)'
        )
        connection.execute(
            'INSERT INTO OptHarvest_Field2_Oats VALUES (1, "2023-08-01", 1200)'
        )
    inventory = {
        "simulation": "Simulation",
        "fields": {"Field2": {"reports": {
            "Report2": {"variables": ["[Oats].LAI as OatsLAI"],
                        "events": ["[Clock].EndOfDay"]}
        }}},
    }
    records = [{
        "field": "Field2", "yield_components": ["Oats"],
        "technology": "N30", "observed_yield_t_ha": 3.4,
        "observed_sow_date": "2023-05-04",
        "observed_tillering_date": "2023-06-05",
        "observed_heading_date": "2023-07-04",
    }]
    daily, summaries, schema = collect_oats_diagnostics(database, inventory, records)
    assert len(daily) == 2
    assert summaries[0]["simulated_yield_t_ha"] == 1.2
    assert summaries[0]["water_stress_days_fw_below_0_8"] == 2
    assert summaries[0]["severe_water_stress_days_fw_below_0_5"] == 1
    assert summaries[0]["nitrogen_stress_days_fn_below_0_8"] == 1
    assert summaries[0]["vegetative_nitrogen_stress_days_fn_below_0_8"] == 0
    assert summaries[0]["grain_fill_nitrogen_stress_days_fn_below_0_8"] == 1
    assert summaries[0]["max_root_depth_mm"] == 700
    assert summaries[0]["max_root_depth_date"] == "2023-07-05"
    assert summaries[0]["simulated_harvest_index"] == 0.24
    assert summaries[0]["max_stems_per_plant"] == 2
    assert summaries[0]["final_grains_per_stem"] == 8
    assert summaries[0]["grain_potential_allocation_to_demand_ratio"] == 0.8
    assert summaries[0]["grain_dm_demand_unmet_days"] == 1
    assert summaries[0]["leaf_water_allocation_to_demand_ratio"] == 4 / 7
    assert schema["fields"]["Field2"]["active_row_count"] == 2


def test_collect_oats_diagnostics_keeps_failed_establishment(tmp_path):
    database = tmp_path / "candidate.db"
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE _Simulations(ID INTEGER, Name TEXT)')
        connection.execute('INSERT INTO _Simulations VALUES (1, "Simulation")')
        connection.execute(
            'CREATE TABLE Report3('
            'SimulationID INTEGER, "Clock.Today" TEXT, OatsStage TEXT, '
            'OatsLAI REAL, OatsAGWt REAL)'
        )
        connection.execute(
            'INSERT INTO Report3 VALUES (1, "2019-05-10", "Sowing", 0, 0)'
        )
    inventory = {
        "simulation": "Simulation",
        "fields": {"Field3": {"reports": {
            "Report3": {"variables": ["[Oats].LAI as OatsLAI"], "events": []}
        }}},
    }
    records = [{
        "field": "Field3", "yield_components": ["Oats"],
        "crop": "Oats", "cultivar": "Аргамак", "model_cultivar": "Yakov",
        "technology": "N30", "observed_yield_t_ha": 3.31,
        "observed_sow_date": "2019-05-10",
        "observed_tillering_date": None, "observed_heading_date": None,
    }]
    daily, summaries, schema = collect_oats_diagnostics(
        database, inventory, records, year=2019
    )
    assert daily == []
    assert summaries[0]["simulated_yield_t_ha"] == 0.0
    assert summaries[0]["error_t_ha"] == -3.31
    assert summaries[0]["diagnostic_status"] == "no_active_crop_rows"
    assert schema["status"] == "complete_with_failed_establishment_cases"
