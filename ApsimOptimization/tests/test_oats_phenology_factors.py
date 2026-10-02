from scripts.screen_oats_phenology_factors import PROFILES


def test_one_factor_profiles_change_one_command_and_have_unique_names():
    assert PROFILES[0]["name"] == "current_yakov"
    assert PROFILES[0]["values"] == {}
    assert all(len(profile["values"]) == 1 for profile in PROFILES[1:])
    assert len({profile["name"] for profile in PROFILES}) == len(PROFILES)


def test_factor_values_are_only_exact_drummond_or_milton_alternatives():
    values = {
        profile["name"]: next(iter(profile["values"].values()))
        for profile in PROFILES[1:]
    }
    assert values == {
        "minimum_leaf_number_8_5": 8.5,
        "vernalisation_sensitivity_7": 7,
        "photoperiod_sensitivity_3": 3,
        "early_reproductive_base_3": 3,
        "early_reproductive_base_2": 2,
        "early_reproductive_pp_2": 2,
        "early_reproductive_pp_0": 0,
        "phyllochron_75": 75,
        "phyllochron_105": 105,
    }
