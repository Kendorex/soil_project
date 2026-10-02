from src.oats_rotation_transition import (
    HARVEST_OLD,
    SCHEDULED_SOWING_OLD,
    find_oat_after_perennial_targets,
    patch_end_old_perennials_after_final_cut,
    patch_oat_after_perennial_restart,
)


def _manager(field, crops, dates):
    return {
        "$type": "Models.Core.Zone, Models", "Name": field, "Children": [{
            "$type": "Models.Manager, Models", "Name": "Sow using a variable rule",
            "CodeArray": (SCHEDULED_SOWING_OLD + "\n\n" + HARVEST_OLD).splitlines(),
            "Parameters": [
                {"Key": "RotationCrops", "Value": crops},
                {"Key": "RotationSowingDates", "Value": dates},
            ],
            "Children": [],
        }],
    }


def test_finds_only_new_oats_after_pure_perennial():
    model = {"Name": "Root", "Children": [
        _manager(
            "Field1",
            "RedClover+PlantainForage, RedClover+PlantainForage, Oats+RedClover+PlantainForage",
            "-, -, 18-May",
        ),
        _manager("Field2", "Wheat, Oats+RedClover+PlantainForage", "12-May, 4-May"),
    ]}
    assert find_oat_after_perennial_targets(model, 2019) == [{
        "field": "Field1", "year": 2021,
        "previous_rotation": "RedClover+PlantainForage",
        "current_rotation": "Oats+RedClover+PlantainForage",
        "sowing_date": "18-May",
        "reason": "dated oat sowing follows a pure perennial phase",
    }]


def test_patch_ends_old_stand_only_for_target_transition_at_runtime():
    model = {"Name": "Root", "Children": [
        _manager("Field1", "RedClover+PlantainForage, Oats+RedClover+PlantainForage", "-, 18-May")
    ]}
    changed = patch_oat_after_perennial_restart(model)
    code = "\n".join(model["Children"][0]["Children"][0]["CodeArray"])
    assert changed == ["/Root/Field1/Sow using a variable rule"]
    assert "IsPurePerennial(index - 1)" in code
    assert "EndAllPlants();" in code
    assert "else\n                    EndPlantsNotIn(index);" in code


def test_patch_can_end_old_stand_at_existing_final_cut():
    model = {"Name": "Root", "Children": [
        _manager("Field1", "RedClover+PlantainForage, Oats+RedClover+PlantainForage", "-, 18-May")
    ]}
    changed = patch_end_old_perennials_after_final_cut(model)
    code = "\n".join(model["Children"][0]["Children"][0]["CodeArray"])
    assert changed == ["/Root/Field1/Sow using a variable rule"]
    assert "int following = index + 1;" in code
    assert "RotationSowingDates[following] != \"-\"" in code
    assert "EndAllPlants();" in code
