from src.search_space import SearchSpace


def make_space():
    return SearchSpace(("a", "b"), ("TestCultivar",), (100.0, 120.0), (30.0,), (250.0,), ("F0", "F1"))


def test_cartesian_size_and_stable_technology_key():
    technologies = list(make_space().technologies())
    assert len(technologies) == 8
    assert technologies[0].key() == technologies[0].key()


def test_same_technology_is_independent_of_weather_scenario():
    technology = next(make_space().technologies())
    assert not hasattr(technology, "scenario_id")
