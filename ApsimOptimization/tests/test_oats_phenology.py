from src.oats_phenology import pareto_comparison, phenology_candidate_metrics


def row(year, observed, simulated, tillering_error, heading_error, cultivar="Яков"):
    return {
        "year": year, "field": "Field1", "observed_cultivar": cultivar,
        "model_cultivar": "Yakov", "observed_yield_t_ha": observed,
        "simulated_yield_t_ha": simulated,
        "observed_tillering_date": "2022-06-10",
        "simulated_tillering_date": f"2022-06-{10 + tillering_error:02d}",
        "observed_heading_date": "2022-07-10",
        "simulated_heading_date": f"2022-07-{10 + heading_error:02d}",
    }


def test_metrics_keep_argamak_2019_out_of_yakov_development():
    rows = [
        row(2019, 3, 0, 1, 1, cultivar="Аргамак"),
        row(2022, 2, 3, 2, -3),
        row(2023, 3, 1, 4, 0),
    ]
    metrics = phenology_candidate_metrics(rows)
    assert metrics["development_yakov_2020_2022"]["cases"] == 1
    assert metrics["development_yakov_2020_2022"]["yield_rmse_t_ha"] == 1
    assert metrics["validation_yakov_2023"]["heading"]["mae_days"] == 0
    assert metrics["argamak_2019_context_only"]["cases"] == 1


def test_pareto_comparison_does_not_weight_or_normalize_metrics():
    def candidate(name, vector):
        return {"name": name, "metrics": {"development_yakov_2020_2022": {
            "yield_rmse_t_ha": vector[0],
            "tillering": {"mae_days": vector[1]},
            "heading": {"mae_days": vector[2]},
        }}}

    result = pareto_comparison([
        candidate("current_yakov", (2, 5, 5)),
        candidate("better", (1, 4, 5)),
        candidate("tradeoff", (0.5, 6, 4)),
    ])
    assert result["normalization"] == "none"
    assert result["weighting"] == "none"
    assert result["candidates_dominating_current"] == ["better"]
    assert result["equivalent_to_current"] == []
    assert set(result["nondominated_candidates"]) == {"better", "tradeoff"}


def test_pareto_comparison_reports_candidates_with_no_metric_effect():
    def candidate(name):
        return {"name": name, "metrics": {"development_yakov_2020_2022": {
            "yield_rmse_t_ha": 1.0,
            "tillering": {"mae_days": 2.0},
            "heading": {"mae_days": 3.0},
        }}}

    result = pareto_comparison([candidate("current_yakov"), candidate("same")])
    assert result["equivalent_to_current"] == ["same"]
