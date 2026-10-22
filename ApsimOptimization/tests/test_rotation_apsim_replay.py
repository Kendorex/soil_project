import json

from src.multifield_model import parameters
from src.rotation_apsim_replay import patch_selected_rotation, resolve_profile


def test_resolve_profile_uses_year_specific_values():
    profiles = {
        "wheat": {
            "model_crop": "Wheat",
            "cultivar_by_year": {2019: "Irgina", 2020: "Zlata"},
            "sowing_date_by_year": {2019: "7-May", 2020: "11-May"},
            "harvest_date": "31-Aug",
            "sowing_depth": "20",
            "row_spacing": "150",
            "population": "200",
            "primary_yield_components": ["Wheat"],
            "product": "grain",
        }
    }
    assert resolve_profile(profiles, "wheat", 2019)["cultivar"] == "Irgina"
    assert resolve_profile(profiles, "wheat", 2020)["sowing_date"] == "11-May"


def test_patch_selected_rotation_changes_only_rotation_parameters(tmp_path):
    manager = {
        "Name": "Sow using a variable rule",
        "Parameters": [
            {"Key": "RotationCrops", "Value": "Wheat"},
            {"Key": "RotationCultivars", "Value": "Old"},
            {"Key": "RotationSowingDepths", "Value": "30"},
            {"Key": "RotationRowSpacings", "Value": "200"},
            {"Key": "RotationPopulations", "Value": "300"},
            {"Key": "RotationSowingDates", "Value": "1-May"},
            {"Key": "RotationHarvestDates", "Value": "1-Aug"},
        ],
        "Children": [],
    }
    model = {
        "Name": "Simulations",
        "Children": [{
            "$type": "Models.Core.Simulation, Models", "Name": "Test", "Enabled": True,
            "Children": [
                {"$type": "Models.Clock, Models", "Name": "Clock",
                 "Start": "2019-01-01T00:00:00", "End": "2019-12-31T00:00:00",
                 "Children": []},
                {"$type": "Models.Core.Zone, Models", "Name": "Field1", "Enabled": True,
                 "Children": [manager]},
            ],
        }],
    }
    source = tmp_path / "source.apsimx"
    target = tmp_path / "target.apsimx"
    source.write_text(json.dumps(model), encoding="utf-8")
    rotation = {"start_year": 2019, "end_year": 2019,
                "fields": {"Field1": ["wheat"]}}
    profiles = {"wheat": {
        "model_crop": "Wheat", "cultivar": "Zlata", "sowing_date": "7-May",
        "harvest_date": "9-Aug", "sowing_depth": "20", "row_spacing": "150",
        "population": "200", "primary_yield_components": ["Wheat"],
        "product": "grain",
    }}
    patch_selected_rotation(source, rotation, profiles, target)
    patched = json.loads(target.read_text(encoding="utf-8"))
    patched_manager = patched["Children"][0]["Children"][1]["Children"][0]
    values = parameters(patched_manager)
    assert values["RotationCultivars"] == "Zlata"
    assert values["RotationSowingDates"] == "7-May"


def test_patch_selected_rotation_prepends_warmup_year(tmp_path):
    manager = {
        "Name": "Sow using a variable rule",
        "Parameters": [
            {"Key": "RotationCrops", "Value": "Wheat"},
            {"Key": "RotationCultivars", "Value": "Old"},
            {"Key": "RotationSowingDepths", "Value": "30"},
            {"Key": "RotationRowSpacings", "Value": "200"},
            {"Key": "RotationPopulations", "Value": "300"},
            {"Key": "RotationSowingDates", "Value": "1-May"},
            {"Key": "RotationHarvestDates", "Value": "1-Aug"},
        ],
        "Children": [],
    }
    fertiliser_manager = {
        "Name": "Fertilise at sowing",
        "CodeArray": [
            "            if (Clock.Today.Date == lastApplication.Date)",
            "                return;",
        ],
        "Children": [],
    }
    model = {
        "Name": "Simulations", "Children": [{
            "$type": "Models.Core.Simulation, Models", "Name": "Test", "Enabled": True,
            "Children": [
                {"$type": "Models.Clock, Models", "Name": "Clock",
                 "Start": "2019-01-01T00:00:00", "End": "2019-12-31T00:00:00",
                 "Children": []},
                {"$type": "Models.Core.Zone, Models", "Name": "Field1", "Enabled": True,
                 "Children": [manager, fertiliser_manager]},
            ],
        }],
    }
    source = tmp_path / "source.apsimx"
    target = tmp_path / "target.apsimx"
    source.write_text(json.dumps(model), encoding="utf-8")
    profiles = {
        "none": {"model_crop": "-", "cultivar": "-", "sowing_date": "-",
                 "harvest_date": "-", "sowing_depth": "0", "row_spacing": "0",
                 "population": "0", "primary_yield_components": [], "product": "none"},
        "wheat": {"model_crop": "Wheat", "cultivar": "Zlata", "sowing_date": "7-May",
                  "harvest_date": "9-Aug", "sowing_depth": "20", "row_spacing": "150",
                  "population": "200", "primary_yield_components": ["Wheat"],
                  "product": "grain"},
    }
    rotation = {"start_year": 2019, "end_year": 2019, "fields": {"Field1": ["wheat"]}}
    warmup = {"enabled": True, "start_date": "2018-09-01",
              "field_states": {"Field1": "none"}}
    patch = patch_selected_rotation(source, rotation, profiles, target, warmup=warmup)
    patched = json.loads(target.read_text(encoding="utf-8"))
    assert patched["Children"][0]["Children"][0]["Start"].startswith("2018-09-01")
    values = parameters(patched["Children"][0]["Children"][1]["Children"][0])
    assert values["RotationCrops"] == "-,Wheat"
    assert patch["fields"]["Field1"]["2018"]["state_id"] == "none"
