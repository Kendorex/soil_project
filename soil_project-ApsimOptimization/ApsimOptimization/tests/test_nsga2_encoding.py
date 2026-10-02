from src.nsga2_optimizer import decode_indices, encode_technology, objective_vector
from src.search_space import SearchSpace


def test_roundtrip_discrete_encoding():
    space = SearchSpace(("a", "b"), ("c1", "c2"), (100.0, 120.0), (20.0, 30.0), (200.0, 250.0), ("F0", "F1"))
    technology = decode_indices(space, [1, 0, 1, 0, 1, 1])
    assert encode_technology(space, technology) == (1, 0, 1, 0, 1, 1)


def test_objectives_have_correct_minimization_signs():
    assert objective_vector({"profit_avg": 100, "profit_min": 20, "controllable_cost": 30}) == (-100.0, 30.0, -20.0)
