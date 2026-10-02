from src.nsga2_optimizer import objective_vector


def test_objectives_are_profit_cost_worst_case():
    summary = {"profit_avg": 1000, "controllable_cost": 250, "profit_min": -100, "run_status": "completed"}
    assert objective_vector(summary) == (-1000.0, 250.0, 100.0)
