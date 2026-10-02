from src.nsga2_optimizer import ApsimTechnologyProblem, LARGE_PENALTY, control_anchor_vectors
from src.search_space import SearchSpace


def make_space():
    return SearchSpace(
        ("15-aug..30-oct",),
        ("TestCultivar",),
        (120.0,),
        (30.0,),
        (250.0,),
        ("F0_no_fertilizer", "F1"),
    )


def test_candidate_indices_decode_to_technology():
    problem = ApsimTechnologyProblem(make_space(), lambda technology: {})
    technology = problem.decode_candidate([0, 0, 0, 0, 0, 1])
    assert technology.fertilizer_scheme_id == "F1"
    assert technology.cultivar == "TestCultivar"


def test_initial_control_anchors_cover_every_cultivar_and_f0():
    space = SearchSpace(
        ("1-may..30-may",),
        ("Zlata", "Irgina"),
        (250.0, 300.0),
        (20.0, 30.0),
        (125.0, 250.0),
        ("F0_no_fertilizer", "F1_UreaN_30_sowing"),
    )

    anchors = control_anchor_vectors(space)

    assert anchors.tolist() == [
        [0, 0, 0, 0, 0, 0],
        [0, 1, 0, 0, 0, 0],
    ]
    assert [space.decode(vector).cultivar for vector in anchors] == ["Zlata", "Irgina"]


def test_evaluate_calls_evaluator_and_returns_objectives():
    called = []

    def evaluator(technology):
        called.append(technology.key())
        return {
            "technology_id": technology.key(),
            "profit_mean": 1000.0,
            "profit_avg": 1000.0,
            "profit_min": -200.0,
            "controllable_cost": 300.0,
            "fertilizer_passes": 2,
            "is_feasible": None,
            "run_status": "completed",
            "scenario_count": 4,
            "cache_hit_count": 1,
            "actual_apsim_runs": 3,
        }

    problem = ApsimTechnologyProblem(make_space(), evaluator)
    out = {}
    problem._evaluate([0, 0, 0, 0, 0, 1], out)
    assert called
    assert tuple(out["F"]) == (-1000.0, 300.0, 200.0)
    assert tuple(out["G"]) == (0.0,)
    row = problem.evaluation_rows[0]
    assert row["evaluation_id"] == 1
    assert row["scenario_count"] == 4
    assert row["cache_hit_count"] == 1
    assert row["actual_apsim_runs"] == 3


def test_repeated_candidate_is_visible_as_cache_reuse():
    calls = []

    def evaluator(technology):
        calls.append(technology.key())
        return {
            "technology_id": technology.key(), "profit_avg": 10, "profit_mean": 10,
            "profit_min": 1, "controllable_cost": 2, "fertilizer_passes": 0,
            "scenario_count": 4, "cache_hit_count": 0, "actual_apsim_runs": 4,
            "is_feasible": None, "run_status": "completed",
        }

    problem = ApsimTechnologyProblem(make_space(), evaluator)
    problem.evaluate_candidate([0, 0, 0, 0, 0, 0])
    problem.evaluate_candidate([0, 0, 0, 0, 0, 0])
    assert len(calls) == 1
    assert problem.evaluation_rows[1]["cache_hit_count"] == 4
    assert problem.evaluation_rows[1]["actual_apsim_runs"] == 0


def test_evaluator_error_returns_large_penalty():
    def evaluator(_technology):
        raise RuntimeError("APSIMX failed")

    problem = ApsimTechnologyProblem(make_space(), evaluator)
    out = {}
    problem._evaluate([0, 0, 0, 0, 0, 0], out)
    assert tuple(out["F"]) == (LARGE_PENALTY, LARGE_PENALTY, LARGE_PENALTY)
    assert problem.evaluation_rows[0]["run_status"] == "failed"
    assert "APSIMX failed" in problem.evaluation_rows[0]["error_message"]
