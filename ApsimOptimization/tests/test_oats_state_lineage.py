from src.oats_state_lineage import build_state_lineage


def test_state_lineage_builds_phase_sums_and_pair_contrast():
    daily = [
        {"year": year, "field": field, "date": f"{year}-05-01",
         "OatsZadok": 20, "MineralN": mineral_n, "PAW_total": 1.0,
         "OatsRootFASW": 0.8, "Rain": 2, "Radn": 10,
         "OatsLeafRadiationIntercepted": 3, "OatsLeafPhotosynthesis": 4,
         "OatsRootNUptake_total": -1}
        for year, field, mineral_n in ((2023, "Field2", 30), (2023, "Field7", 60))
    ]
    daily += [
        {"year": 2023, "field": field, "date": "2023-07-01",
         "OatsZadok": 75, "MineralN": 10, "PAW_total": .8,
         "OatsRootFASW": .6, "Rain": 1, "Radn": 8,
         "OatsLeafRadiationIntercepted": 2, "OatsLeafPhotosynthesis": 3,
         "OatsRootNUptake_total": -2}
        for field in ("Field2", "Field7")
    ]
    history = [
        {"year": 2023, "field": field, "observed_cultivar": "Яков",
         "technology": technology, "observed_yield_t_ha": observed,
         "simulated_yield_t_ha": simulated, "error_t_ha": simulated - observed,
         "max_lai": 2, "max_aboveground_biomass_g_m2": 500,
         "final_grain_number_m2": 10000, "final_grain_size_g": .01,
         "grain_fill_nitrogen_stress_days_fn_below_0_8": 1}
        for field, technology, observed, simulated in (
            ("Field2", "N30", 3.4, 1.0), ("Field7", "N60", 3.5, 1.5)
        )
    ]
    lineage, contrasts = build_state_lineage(daily, history)
    assert lineage[0]["photosynthesis_pre_heading"] == 4
    assert lineage[0]["photosynthesis_grain_fill"] == 3
    assert lineage[0]["root_n_uptake_native_sum"] == -3
    assert contrasts[0]["first_active_mineral_n_model_value_b_minus_a"] == 30
    assert contrasts[0]["simulated_yield_t_ha_b_minus_a"] == .5
