from src.oats_component_validation import (
    add_observed_grain_components,
    compare_candidate,
    evaluate_candidate,
)


def case(year, field, yield_error, tkw_g, grain_number, heading_error=0):
    return {
        "year": year,
        "field": field,
        "observed_cultivar": "Яков",
        "error_t_ha": yield_error,
        "observed_yield_t_ha": 3.0,
        "final_grain_size_g": tkw_g / 1000,
        "final_grain_number_m2": grain_number,
        "observed_tillering_date": f"{year}-06-01",
        "simulated_tillering_date": f"{year}-06-02",
        "observed_heading_date": f"{year}-07-01",
        "simulated_heading_date": f"{year}-07-{1 + heading_error:02d}",
    }


def test_component_metrics_keep_development_and_validation_separate():
    rows = [case(2021, "Field6", -1.0, 20, 15000),
            case(2023, "Field2", -0.2, 35, 9000)]
    source = {
        (2021, "Field6"): {"observed_thousand_grain_mass_g": 40},
        (2023, "Field2"): {"observed_thousand_grain_mass_g": 40},
    }
    annotated = add_observed_grain_components(rows, source)
    metrics = evaluate_candidate(annotated, [])
    development = metrics["development_yakov_2020_2022"]
    validation = metrics["validation_yakov_2023"]
    assert development["yield"]["rmse_t_ha"] == 1.0
    assert validation["yield"]["rmse_t_ha"] == 0.2
    assert development["thousand_grain_mass"]["mae_g"] == 20.0
    assert annotated[0]["observed_yield_implied_grain_number_m2"] == 7500.0
    assert development["grain_number"]["mean_absolute_relative_error"] == 1.0


def test_pareto_comparison_rejects_hidden_tradeoff():
    reference_rows = [case(2021, "Field6", -1.0, 30, 10000),
                      case(2023, "Field2", -1.0, 30, 10000)]
    candidate_rows = [case(2021, "Field6", -2.0, 35, 9000),
                      case(2023, "Field2", -0.2, 35, 9000)]
    source = {
        (2021, "Field6"): {"observed_thousand_grain_mass_g": 40},
        (2023, "Field2"): {"observed_thousand_grain_mass_g": 40},
    }
    reference = evaluate_candidate(
        add_observed_grain_components(reference_rows, source), []
    )
    candidate = evaluate_candidate(
        add_observed_grain_components(candidate_rows, source), []
    )
    comparison = compare_candidate(candidate, reference)
    assert comparison["overall_status"] == "tradeoff_between_metrics_or_time_blocks"
    assert comparison["normalization"] == "none"
    assert comparison["weights"] == "none"
