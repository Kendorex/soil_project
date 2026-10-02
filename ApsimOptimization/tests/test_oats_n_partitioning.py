import datetime as dt

from src.oats_n_partitioning import compare_doses, patch_fertilizer_amount_on_date


def test_patch_fertilizer_preserves_type_and_changes_one_date():
    manager = {
        "$type": "Models.Manager, Models", "Name": "Fertilise at sowing",
        "Parameters": [{"Key": "Amount", "Value": "30"},
                       {"Key": "FertiliserType", "Value": "NO3N"}],
        "CodeArray": [
            "class Script {", "        private void OnSowing()", "        {",
            "            Fertiliser.Apply(amount: Amount, type: FertiliserType);",
            "        }", "}",
        ], "Children": [],
    }
    zone = {"$type": "Models.Core.Zone, Models", "Name": "Field2",
            "Enabled": True, "Children": [manager]}
    data = {"Name": "Simulations", "Children": [{
        "$type": "Models.Core.Simulation, Models", "Name": "Simulation",
        "Enabled": True, "Children": [zone],
    }]}
    changes = patch_fertilizer_amount_on_date(
        data, ("Field2",), dt.date(2023, 5, 4), 60
    )
    code = "\n".join(manager["CodeArray"])
    assert "new DateTime(2023, 5, 4)" in code
    assert "diagnosticAmount = 60" in code
    assert "type: FertiliserType" in code
    assert changes[0]["fertilizer_type_preserved"] == "NO3N"


def test_compare_doses_produces_high_minus_low_changes():
    rows = [
        {"diagnostic_n_kg_ha": 30, "field": "Field2", "simulated_yield_t_ha": 1,
         "max_lai": 2},
        {"diagnostic_n_kg_ha": 60, "field": "Field2", "simulated_yield_t_ha": 1.8,
         "max_lai": 3},
    ]
    delta = compare_doses(rows, 30, 60)[0]
    assert delta["simulated_yield_t_ha_change"] == 0.8
    assert delta["max_lai_change"] == 1
    assert delta["final_grain_size_g_change"] is None

