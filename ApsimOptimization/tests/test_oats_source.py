from src.oats_source import source_candidate_comparison


def candidate(name, development, validation):
    return {"name": name, "metrics": {
        "development_yakov_2020_2022": {"yield_rmse_t_ha": development},
        "validation_yakov_2023": {"yield_rmse_t_ha": validation},
    }}


def test_source_comparison_requires_improvement_in_both_periods_to_dominate():
    result = source_candidate_comparison([
        candidate("current_yakov", 2.0, 3.0),
        candidate("better", 1.5, 2.5),
        candidate("tradeoff", 1.0, 4.0),
        candidate("same", 2.0, 3.0),
    ])
    assert result["candidates_dominating_current"] == ["better"]
    assert result["equivalent_to_current"] == ["same"]
    assert result["normalization"] == "none"
    assert result["weighting"] == "none"
    assert set(result["nondominated_candidates"]) == {"better", "tradeoff"}


def test_source_factor_profiles_use_only_reference_values():
    from scripts.screen_oats_source_factors import PROFILES

    assert PROFILES[0]["values"] == {}
    assert len(PROFILES) == 8
    assert {profile["name"] for profile in PROFILES[1:]} == {
        "leaf_area_4000", "senescence_duration_3", "extinction_0_6",
        "drummond_branching", "maximum_grain_size_0_05",
        "grains_per_stem_40", "grains_per_stem_50",
    }
