from src.multiyear_rotation_optimizer import (
    decode_vector, fertilizer_schemes, summarize_period, variable_spec,
)


def sample_config():
    return {
        "fertilizer": {
            "doses_kg_n_ha": [0, 30],
            "forms": {
                "urea": {"apsim_type": "UreaN", "price_per_kg_n": 2.0},
                "nitrate": {"apsim_type": "NO3N", "price_per_kg_n": 3.0},
            },
        },
        "management_policies": {
            "spring_wheat": {
                "cultivar_options": ["historical", "Zlata"],
                "population_options_m2": [250, 300],
                "depth_options_mm": [20],
                "row_spacing_options_mm": [150],
            }
        },
    }


def test_fertilizer_schemes_deduplicate_zero_dose():
    schemes = fertilizer_schemes(sample_config())
    assert [row["name"] for row in schemes] == ["N0", "urea_30", "nitrate_30"]
    assert schemes[0]["apsim_type"] == "UreaN"


def test_decode_vector_uses_named_policy_variables():
    config = sample_config()
    spec = variable_spec(config)
    assert [row[1] for row in spec] == ["cultivar", "population", "fertilizer_scheme"]
    decoded = decode_vector(config, [1, 1, 2])["spring_wheat"]
    assert decoded["cultivar"] == "Zlata"
    assert decoded["population"] == 300.0
    assert decoded["fertilizer"]["name"] == "nitrate_30"


def test_period_summary_exposes_diagnostic_fertility_violation():
    config = {
        "evaluation_years": [2023],
        "economics": {
            "crop_price_per_kg_dm": {"spring_wheat": 1.0},
            "application_cost_per_ha": 0.0,
            "other_cost_per_ha_year": 0.0,
        },
        "fertilizer": {"forms": {"urea": {"apsim_type": "UreaN", "price_per_kg_n": 1.0}}},
        "fertility_constraints": {
            "mode": "objective_only",
            "mineral_n_end": {
                "enabled": True,
                "reference": "fixed_map_baseline",
                "min_fraction_of_reference": 1.0,
            },
            "unassessed_indicators": ["mobile_phosphorus"],
        },
    }
    results = {2023: {"fields": {"Field_1": {
        "area_ha": 2.0,
        "yield_dm_kg_ha": {"Wheat": 1000.0},
        "mineral_n_end_kg_ha": 40.0,
    }}}}
    patch = {"fields": {"Field_1": {"2023": {
        "state_id": "spring_wheat", "primary_yield_components": ["Wheat"],
    }}}}
    policies = {"spring_wheat": {"fertilizer": {
        "name": "N0", "dose_kg_n_ha": 0.0, "price_per_kg_n": 0.0,
    }}}
    summary = summarize_period(
        results, patch, config, policies, {"2023": {"Field_1": 50.0}}
    )
    assert summary["soil_deviation"] == 0.2
    assert summary["G"] == [0.0]
    assert summary["fertility_constraints"]["status"] == "checked_failed"
    assert summary["fertility_constraints"]["is_feasible"] is None
