from scripts.diagnose_oats_joint_grain_structure import derive_development_grain_number


def test_derivation_uses_development_only_and_excludes_known_failure():
    rows = [
        {
            "year": 2020, "field": "Field5", "observed_cultivar": "Яков",
            "observed_yield_t_ha": 3.0, "final_grain_number_m2": 15000,
        },
        {
            "year": 2021, "field": "Field1", "observed_cultivar": "Яков",
            "observed_yield_t_ha": 3.0, "final_grain_number_m2": 1000,
        },
        {
            "year": 2022, "field": "Field4", "observed_cultivar": "Яков",
            "observed_yield_t_ha": 2.0, "final_grain_number_m2": 10000,
        },
        {
            "year": 2023, "field": "Field2", "observed_cultivar": "Яков",
            "observed_yield_t_ha": 4.0, "final_grain_number_m2": 10000,
        },
    ]
    source = {
        (2020, "Field5"): {"observed_thousand_grain_mass_g": 40.0},
        (2021, "Field1"): {"observed_thousand_grain_mass_g": 40.0},
        (2022, "Field4"): {"observed_thousand_grain_mass_g": 40.0},
        (2023, "Field2"): {"observed_thousand_grain_mass_g": 40.0},
    }
    value, derivation = derive_development_grain_number(rows, source)
    # Both included development cases imply 7,500/5,000 grains m-2 and a value 22.5.
    assert value == 22.5
    assert len(derivation) == 3
    excluded = next(row for row in derivation if row["field"] == "Field1")
    assert excluded["included_in_median"] is False
    assert excluded["exclusion_reason"] == "known_2021_field1_lifecycle_failure"
