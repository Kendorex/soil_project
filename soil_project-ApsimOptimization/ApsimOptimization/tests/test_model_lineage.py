import json

import pytest

from src.model_lineage import (command_map, commands_from_calibration,
                               cultivar_inventory, patch_cultivar,
                               require_commands)


def model(commands_a=None, commands_b=None):
    def cultivar(commands):
        return {"$type": "Models.PMF.Cultivar, Models", "Name": "Zlata",
                "Command": list(commands), "Children": []}
    return {"Name": "Simulations", "Children": [
        {"Name": "Field1", "Children": [cultivar(commands_a or ["[P].X = 1"])]},
        {"Name": "Field2", "Children": [cultivar(commands_b or commands_a or ["[P].X = 1"])]},
    ]}


def test_cultivar_inventory_detects_field_specific_variants():
    assert cultivar_inventory(model(), "Zlata")["variant_count"] == 1
    audit = cultivar_inventory(model(["[P].X = 1"], ["[P].X = 2"]), "Zlata")
    assert audit["node_count"] == 2
    assert audit["variant_count"] == 2


def test_patch_cultivar_requires_existing_unambiguous_command():
    data = model()
    changes = patch_cultivar(data, "Zlata", {"[P].X": 2})
    assert len(changes) == 2
    require_commands(data, "Zlata", {"[P].X": 2})
    assert command_map(data["Children"][0]["Children"][0]) == {"[P].X": "2"}
    with pytest.raises(ValueError, match="Expected one existing"):
        patch_cultivar(data, "Zlata", {"[P].Missing": 2})
    added = patch_cultivar(
        data, "Zlata", {"[P].VerifiedNew": 3}, allow_new_commands=True)
    assert len(added) == 2
    require_commands(data, "Zlata", {"[P].VerifiedNew": 3})


def test_calibration_mapping_fails_closed():
    mapping = {"MinLN": "[Phenology].MinLN", "PpLN": "[Phenology].PpLN"}
    assert commands_from_calibration({"MinLN": 5, "PpLN": 2.5}, mapping) == {
        "[Phenology].MinLN": 5, "[Phenology].PpLN": 2.5}
    with pytest.raises(ValueError, match="missing parameters"):
        commands_from_calibration({"MinLN": 5}, mapping)
