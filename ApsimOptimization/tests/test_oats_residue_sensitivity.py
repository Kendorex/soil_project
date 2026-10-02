import pytest

from src.oats_residue_sensitivity import (
    CAP_MANAGER_NAME, CAP_REPORT_EXPRESSIONS, CEREAL_HARVEST_OLD,
    CEREAL_REPORT_EXPRESSIONS, MANAGER_NAME, REPORT_EXPRESSIONS,
    add_full_residue_removal, add_pre_sowing_residue_cap, cereal_to_oats_targets,
    defer_wheat_auto_harvest_for_field_years,
    patch_export_new_residue_at_cereal_to_oats_harvest,
    patch_remove_all_residue_at_cereal_to_oats_harvest,
)


def _model():
    return {"Name": "Root", "Children": [{
        "$type": "Models.Core.Simulation, Models", "Name": "Simulation", "Children": [{
            "Name": "Field2", "Children": [{
                "Name": "Report2", "VariableNames": ["[Clock].Today as Date"],
                "Children": [],
            }],
        }],
    }]}


def test_adds_exact_date_full_removal_manager_and_outputs():
    model = _model()
    changes = add_full_residue_removal(model, "Simulation", ("Field2",))
    zone = model["Children"][0]["Children"][0]
    manager = next(node for node in zone["Children"] if node["Name"] == MANAGER_NAME)
    report = next(node for node in zone["Children"] if node["Name"] == "Report2")
    code = "\n".join(manager["CodeArray"])
    assert "new DateTime(2022, 8, 17)" in code
    assert "SurfaceOrganicMatter.Remove(RemovedMassKgHa);" in code
    assert all(expression in report["VariableNames"] for expression in REPORT_EXPRESSIONS)
    assert changes[0]["date"] == "2022-08-17"


def test_refuses_duplicate_manager():
    model = _model()
    add_full_residue_removal(model, "Simulation", ("Field2",))
    with pytest.raises(ValueError, match="already exists"):
        add_full_residue_removal(model, "Simulation", ("Field2",))


def test_adds_observed_pre_sowing_residue_cap_without_inventing_target():
    model = _model()
    changes = add_pre_sowing_residue_cap(
        model, "Simulation", {"Field2": 708.0}, date="2023-05-03"
    )
    zone = model["Children"][0]["Children"][0]
    manager = next(node for node in zone["Children"] if node["Name"] == CAP_MANAGER_NAME)
    report = next(node for node in zone["Children"] if node["Name"] == "Report2")
    code = "\n".join(manager["CodeArray"])
    assert "new DateTime(2023, 5, 3)" in code
    assert "TargetMassKgHa { get; private set; } = 708" in code
    assert "Math.Max(0.0, BeforeMassKgHa - TargetMassKgHa)" in code
    assert all(expression in report["VariableNames"] for expression in CAP_REPORT_EXPRESSIONS)
    assert changes == [{
        "field": "Field2",
        "date": "2023-05-03",
        "action": "cap SurfaceOrganicMatter.Wt at supplied target",
        "target_mass_kg_ha": 708.0,
        "manager": CAP_MANAGER_NAME,
    }]


def test_refuses_negative_residue_cap():
    with pytest.raises(ValueError, match="non-negative"):
        add_pre_sowing_residue_cap(_model(), "Simulation", {"Field2": -1})


def test_finds_and_patches_cereal_to_oats_transition():
    rotation_code = '''using Models.PMF;
        [Link] private Summary Summary;
''' + CEREAL_HARVEST_OLD + '''
        private void SowGroup(int index, string reason)'''
    model = {"Name": "Root", "Children": [{
        "$type": "Models.Core.Simulation, Models", "Name": "Simulation", "Children": [{
            "$type": "Models.Core.Zone, Models", "Name": "Field2", "Children": [
                {"$type": "Models.Manager, Models", "Name": "Sow using a variable rule",
                 "CodeArray": rotation_code.splitlines(), "Parameters": [
                     {"Key": "RotationCrops", "Value": "Wheat, Oats+RedClover+PlantainForage"},
                     {"Key": "RotationHarvestDates", "Value": "17-Aug, 31-Aug"},
                 ], "Children": []},
                {"Name": "Report2", "VariableNames": [], "Children": []},
            ],
        }],
    }]}
    targets = cereal_to_oats_targets(model, 2022)
    assert targets[0]["cereal_harvest_date"] == "2022-08-17"
    changed = patch_remove_all_residue_at_cereal_to_oats_harvest(
        model, ("Field2",)
    )
    manager = model["Children"][0]["Children"][0]["Children"][0]
    report = model["Children"][0]["Children"][0]["Children"][1]
    code = "\n".join(manager["CodeArray"])
    assert changed == ["/Root/Simulation/Field2/Sow using a variable rule"]
    assert "IsCerealToOatsTransition(index)" in code
    assert "SurfaceOrganicMatter.Remove" in code
    assert all(value in report["VariableNames"] for value in CEREAL_REPORT_EXPRESSIONS)


def test_patches_export_of_new_harvest_residue_without_removing_existing_pool():
    rotation_code = '''using Models.PMF;
        [Link] private Summary Summary;
''' + CEREAL_HARVEST_OLD + '''
        private void SowGroup(int index, string reason)'''
    model = {"Name": "Root", "Children": [{
        "$type": "Models.Core.Simulation, Models", "Name": "Simulation", "Children": [{
            "$type": "Models.Core.Zone, Models", "Name": "Field2", "Children": [
                {"$type": "Models.Manager, Models", "Name": "Sow using a variable rule",
                 "CodeArray": rotation_code.splitlines(), "Parameters": [], "Children": []},
                {"Name": "Report2", "VariableNames": [], "Children": []},
            ],
        }],
    }]}
    patch_export_new_residue_at_cereal_to_oats_harvest(model, ("Field2",))
    manager = model["Children"][0]["Children"][0]["Children"][0]
    code = "\n".join(manager["CodeArray"])
    assert "double residueBeforeHarvest = SurfaceOrganicMatter.Wt;" in code
    assert "residueAfterHarvest - residueBeforeHarvest" in code
    assert "SurfaceOrganicMatter.Remove(DiagnosticResidueRemovedMassKgHa);" in code
    assert "DiagnosticResidueRemovedMassKgHa = SurfaceOrganicMatter.Wt;" not in code


def test_limits_new_residue_export_to_documented_field_year():
    rotation_code = '''using Models.PMF;
        [Link] private Summary Summary;
''' + CEREAL_HARVEST_OLD + '''
        private void SowGroup(int index, string reason)'''
    model = {"Name": "Root", "Children": [{
        "$type": "Models.Core.Simulation, Models", "Name": "Simulation", "Children": [{
            "$type": "Models.Core.Zone, Models", "Name": "Field2", "Children": [
                {"$type": "Models.Manager, Models", "Name": "Sow using a variable rule",
                 "CodeArray": rotation_code.splitlines(), "Parameters": [], "Children": []},
                {"Name": "Report2", "VariableNames": [], "Children": []},
            ],
        }],
    }]}
    patch_export_new_residue_at_cereal_to_oats_harvest(
        model, ("Field2",), {"Field2": {2022}}
    )
    code = "\n".join(model["Children"][0]["Children"][0]["Children"][0]["CodeArray"])
    assert (
        "IsCerealToOatsTransition(index) && (Clock.Today.Year == 2022)"
        in code
    )


def test_defers_automatic_wheat_harvest_only_in_documented_year():
    harvest_code = '''using Models.PMF;
using Models.Core;
namespace Models
{
    public class Script : Model
    {
        [Description("Crop")]
        public IPlant Crop { get; set; }
        private void Run()
        {
            if (Crop.IsReadyForHarvesting)
                Crop.Harvest();
        }
    }
}'''
    model = {"Name": "Root", "Children": [{
        "$type": "Models.Core.Simulation, Models", "Name": "Simulation", "Children": [{
            "$type": "Models.Core.Zone, Models", "Name": "Field2", "Children": [{
                "$type": "Models.Manager, Models", "Name": "Harvest",
                "CodeArray": harvest_code.splitlines(),
                "Parameters": [{"Key": "Crop", "Value": "[Wheat]"}],
                "Children": [],
            }],
        }],
    }]}
    changes = defer_wheat_auto_harvest_for_field_years(
        model, {"Field2": {2022}}
    )
    code = "\n".join(model["Children"][0]["Children"][0]["Children"][0]["CodeArray"])
    assert "[Link] private Clock Clock;" in code
    assert "Crop.IsReadyForHarvesting && !(Clock.Today.Year == 2022)" in code
    assert changes[0]["years_deferred_to_rotation_manager"] == [2022]
