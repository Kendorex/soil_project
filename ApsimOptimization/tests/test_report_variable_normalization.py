from src.report_variables import normalize_report_variable_names


def test_glued_runoff_sw_is_split_and_duplicate_runoff_removed():
    variables = [
        "[Soil].Swim3.Runoff as Runoff",
        "[Soil].Swim3.Runoff as Runoff[Soil].Water.Volumetric as SW",
    ]
    normalized, issues = normalize_report_variable_names(variables)
    assert normalized == [
        "[Soil].Swim3.Runoff as Runoff",
        "[Soil].Water.Volumetric as SW",
    ]
    assert any("split glued" in issue for issue in issues)
    assert any("duplicate" in issue for issue in issues)


def test_mineral_n_with_urea_is_separated_and_layer_sums_are_preserved():
    variables = [
        "sum([NO3].kgha + [NH4].kgha + [Urea].kgha) as MineralN",
        "sum([NO3].kgha) as NO3kgHa",
    ]

    normalized, issues = normalize_report_variable_names(variables)

    assert normalized == [
        "sum([NO3].kgha + [NH4].kgha) as MineralN",
        "sum([NO3].kgha + [NH4].kgha + [Urea].kgha) as MineralNWithUrea",
        "sum([NO3].kgha) as NO3kgHa",
        "sum([NH4].kgha) as NH4kgHa",
    ]
    assert any("NO3kgHa and NH4kgHa" in issue for issue in issues)
    assert any("duplicate" in issue for issue in issues)
