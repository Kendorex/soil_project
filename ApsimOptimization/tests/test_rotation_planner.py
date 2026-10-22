from pathlib import Path

import yaml

from src.rotation_planner import (
    RotationRules,
    plan_fields,
    plan_fields_stateful,
    plan_fields_stateful_open_start,
    select_farm_plan,
    top_stateful_sequences,
    top_stateful_sequences_open_start,
)


ROOT = Path(__file__).resolve().parents[1]


def payload() -> dict:
    crops = [
        {"id": "oats", "name": "Овес", "predecessor_id": "oats"},
        {"id": "clover", "name": "Клевер", "predecessor_id": "clover"},
        {"id": "spring_wheat", "name": "Яровая пшеница", "predecessor_id": "spring_wheat"},
    ]
    scores = {
        "oats": {"oats": 0.0, "clover": 1.0, "spring_wheat": 0.0},
        "clover": {"oats": 1.0, "clover": 0.0, "spring_wheat": 1.0},
        "spring_wheat": {"oats": 0.0, "clover": 1.0, "spring_wheat": 0.0},
    }
    codes = {
        predecessor: {
            successor: ("Ц" if score == 1.0 else "Нд")
            for successor, score in row.items()
        }
        for predecessor, row in scores.items()
    }
    return {
        "schema_version": 1,
        "orientation": {"directional": True},
        "crop_catalog": crops,
        "predecessor_catalog": [
            {"id": item["id"], "name": item["name"]} for item in crops
        ],
        "transition_scores": scores,
        "transition_codes": codes,
    }


def test_all_crops_rotation_config_is_complete():
    path = ROOT / "configs" / "rotation_planning_2019_2023_all_crops.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    states = config["state_model"]["states"]
    candidates = set(config["state_model"]["candidate_states"])
    categories = config["farm_crop_mix"]["categories"]
    assert len(config["initial_states"]) == 10
    assert {"mustard", "rape", "legume_cereal_mixture"} <= candidates
    assert {"mustard", "rape", "legume_cereal_mixture"} <= set(categories)
    assert states["mustard"]["source_allowed_predecessors"]
    assert states["rape"]["source_allowed_predecessors"]


def test_directional_transition_and_sequence_validation():
    rules = RotationRules(payload())
    assert rules.transition("Овес", "Клевер").score == 1.0
    result = rules.validate_sequence("oats", ["clover", "spring_wheat"])
    assert result["allowed"] is True
    assert result["total_score"] == 2.0
    rejected = rules.validate_sequence("oats", ["spring_wheat"])
    assert rejected["allowed"] is False


def test_general_predecessor_name_can_differ_from_successor_name():
    data = payload()
    data["predecessor_catalog"].append({"id": "corn", "name": "Кукуруза"})
    data["transition_scores"]["corn"] = {
        "oats": 1.0, "clover": 0.5, "spring_wheat": 0.0
    }
    data["transition_codes"]["corn"] = {
        "oats": "Ц", "clover": "Д", "spring_wheat": "Нд"
    }
    rules = RotationRules(data)
    assert rules.transition("Кукуруза", "Овес").code == "Ц"
    assert rules.top_sequences("corn", ["oats", "clover"], years=1)[0][
        "initial_crop_name"
    ] == "Кукуруза"


def test_planner_never_uses_prohibited_transition():
    rules = RotationRules(payload())
    alternatives = rules.top_sequences(
        "oats", ["oats", "clover", "spring_wheat"], years=3, top_k=5
    )
    assert alternatives
    for alternative in alternatives:
        checked = rules.validate_sequence("oats", alternative["sequence_ids"])
        assert checked["allowed"] is True
        assert "Нд" not in alternative["transition_codes"]


def test_field_plan_keeps_alternatives_for_each_field():
    rules = RotationRules(payload())
    result = plan_fields(
        rules,
        {"Field1": "oats", "Field2": "spring_wheat"},
        ["oats", "clover", "spring_wheat"],
        years=2,
        top_k=3,
    )
    assert set(result["fields"]) == {"Field1", "Field2"}
    assert all(item["alternatives"] for item in result["fields"].values())


def state_model() -> dict:
    return {
        "candidate_states": ["oats_underseed", "spring_wheat"],
        "states": {
            "oats_underseed": {
                "name": "Овес с подсевом",
                "matrix_predecessor": "oats",
                # The compact fixture has no legume-cereal-mixture column.
                # Clover is its supported stand-establishment analogue here.
                "matrix_successor": "clover",
                "forced_next": "grass_year1",
            },
            "grass_year1": {
                "name": "Травы 1-го года",
                "matrix_predecessor": "clover",
                "forced_next": "grass_year2",
            },
            "grass_year2": {
                "name": "Травы 2-го года",
                "matrix_predecessor": "clover",
            },
            "spring_wheat": {
                "name": "Яровая пшеница",
                "matrix_predecessor": "spring_wheat",
                "matrix_successor": "spring_wheat",
            },
        },
    }


def test_stateful_plan_does_not_treat_stand_continuation_as_reseeding():
    rules = RotationRules(payload())
    alternatives = top_stateful_sequences(
        rules, "oats_underseed", state_model(), years=3, top_k=5
    )
    assert alternatives
    for alternative in alternatives:
        assert alternative["sequence_ids"][:2] == ["grass_year1", "grass_year2"]
        assert alternative["transition_codes"][:2] == ["П", "П"]
        assert alternative["continuation_years"] == 2


def test_fixed_source_transition_is_not_counted_as_matrix_or_continuation():
    rules = RotationRules(payload())
    model = state_model()
    model["states"]["unsupported_history"] = {
        "name": "Фактическая культура вне матрицы",
        "forced_next": "spring_wheat",
        "forced_next_code": "Ф",
    }
    alternative = top_stateful_sequences(
        rules, "unsupported_history", model, years=1, top_k=1
    )[0]
    assert alternative["sequence_ids"] == ["spring_wheat"]
    assert alternative["transition_codes"] == ["Ф"]
    assert alternative["matrix_transition_count"] == 0
    assert alternative["continuation_years"] == 0


def test_source_history_can_allow_multiple_unscored_successors():
    rules = RotationRules(payload())
    model = state_model()
    model["states"]["unsupported_history"] = {
        "name": "Фактическая культура вне матрицы",
        "source_allowed_next": ["spring_wheat", "oats_underseed"],
    }
    alternatives = top_stateful_sequences(
        rules, "unsupported_history", model, years=1, top_k=None
    )
    assert {item["sequence_ids"][0] for item in alternatives} == {
        "spring_wheat", "oats_underseed"
    }
    assert all(item["transition_codes"] == ["И"] for item in alternatives)
    assert all(item["matrix_transition_count"] == 0 for item in alternatives)


def test_source_history_can_allow_candidate_without_matrix_column():
    rules = RotationRules(payload())
    model = state_model()
    model["candidate_states"].append("mustard")
    model["states"]["spring_wheat"]["source_allowed_next"] = ["mustard"]
    model["states"]["mustard"] = {
        "name": "Горчица",
        "source_allowed_predecessors": ["spring_wheat"],
        "source_allowed_next": ["spring_wheat"],
    }
    alternatives = top_stateful_sequences(
        rules, "spring_wheat", model, years=2, top_k=None
    )
    mustard = next(
        item for item in alternatives
        if item["sequence_ids"] == ["mustard", "spring_wheat"]
    )
    assert mustard["transition_codes"] == ["И", "И"]
    assert mustard["mean_score"] is None
    assert mustard["matrix_transition_count"] == 0


def test_source_backed_candidate_rejects_unobserved_predecessor():
    rules = RotationRules(payload())
    model = state_model()
    model["candidate_states"].append("mustard")
    model["states"]["mustard"] = {
        "name": "Горчица",
        "source_allowed_predecessors": ["spring_wheat"],
        "source_allowed_next": ["spring_wheat"],
    }
    alternatives = top_stateful_sequences(
        rules, "oats_underseed", model, years=1, top_k=None
    )
    assert all(item["sequence_ids"] != ["mustard"] for item in alternatives)


def test_stateful_field_plan_preserves_initial_grass_age():
    rules = RotationRules(payload())
    result = plan_fields_stateful(
        rules,
        {"Field1": "grass_year1", "Field2": "oats_underseed"},
        state_model(),
        years=2,
        top_k=3,
    )
    assert result["fields"]["Field1"]["alternatives"][0]["sequence_ids"][0] == "grass_year2"
    assert result["fields"]["Field2"]["alternatives"][0]["sequence_ids"] == [
        "grass_year1", "grass_year2"
    ]


def test_open_start_leaves_first_year_unscored_and_checks_later_transitions():
    rules = RotationRules(payload())
    alternatives = top_stateful_sequences_open_start(
        rules, state_model(), years=3,
        start_states=["oats_underseed", "spring_wheat"], top_k=None,
    )
    assert alternatives
    assert all(item["transition_codes"][0] == "С" for item in alternatives)
    assert all(
        item["matrix_transition_count"] == sum(
            code not in {"С", "П"} for code in item["transition_codes"]
        )
        for item in alternatives
    )
    oats = next(
        item for item in alternatives
        if item["sequence_ids"][0] == "oats_underseed"
    )
    assert oats["sequence_ids"] == ["oats_underseed", "grass_year1", "grass_year2"]
    assert oats["transition_codes"] == ["С", "П", "П"]


def test_open_start_field_plan_does_not_require_invented_predecessors():
    rules = RotationRules(payload())
    result = plan_fields_stateful_open_start(
        rules, ["Field1", "Field2"], state_model(), years=3,
        start_states=["oats_underseed", "spring_wheat"], top_k=10,
    )
    assert set(result["fields"]) == {"Field1", "Field2"}
    assert result["first_year_transition_scored"] is False
    assert all(
        field["initial_crop_id"] is None for field in result["fields"].values()
    )


def test_farm_selection_uses_crop_mix_ranges_to_break_ties():
    fields = {
        "Field1": {"alternatives": [
            {"rank": 1, "sequence_ids": ["oats_underseed"], "mean_score": 1.0},
            {"rank": 2, "sequence_ids": ["spring_wheat"], "mean_score": 1.0},
        ]},
        "Field2": {"alternatives": [
            {"rank": 1, "sequence_ids": ["oats_underseed"], "mean_score": 1.0},
            {"rank": 2, "sequence_ids": ["spring_wheat"], "mean_score": 1.0},
        ]},
    }
    crop_mix = {"categories": {
        "oats": {
            "states": ["oats_underseed"], "annual_min": 1,
            "annual_max": 1, "annual_target": 1,
        },
        "wheat": {
            "states": ["spring_wheat"], "annual_min": 1,
            "annual_max": 1, "annual_target": 1,
        },
    }}
    selected = select_farm_plan(fields, crop_mix, years=1, beam_width=20)
    assert selected["within_historical_ranges"] is True
    assert selected["annual_counts"] == [{"oats": 1, "wheat": 1}]
    assert set(selected["selected_ranks"].values()) == {1, 2}


def test_farm_selection_never_sacrifices_transition_quality_for_crop_mix():
    fields = {
        "Field1": {"alternatives": [
            {"rank": 1, "sequence_ids": ["oats"], "mean_score": 1.0},
            {"rank": 2, "sequence_ids": ["wheat"], "mean_score": 0.5},
        ]},
    }
    crop_mix = {"categories": {
        "oats": {
            "states": ["oats"], "annual_min": 0,
            "annual_max": 0, "annual_target": 0,
        },
        "wheat": {
            "states": ["wheat"], "annual_min": 1,
            "annual_max": 1, "annual_target": 1,
        },
    }}
    selected = select_farm_plan(fields, crop_mix, years=1, beam_width=20)
    assert selected["selected_ranks"] == {"Field1": 1}
    assert selected["within_historical_ranges"] is False
    assert selected["excluded_lower_score_alternatives"] == {"Field1": 1}


def test_farm_selection_can_require_a_crop_once_over_the_horizon():
    fields = {
        "Field1": {"alternatives": [
            {
                "rank": 1,
                "sequence_ids": ["wheat", "wheat"],
                "mean_score": 1.0,
            },
            {
                "rank": 2,
                "sequence_ids": ["wheat", "mustard"],
                "mean_score": 1.0,
            },
        ]},
    }
    crop_mix = {"categories": {
        "wheat": {
            "states": ["wheat"], "annual_min": 0,
            "annual_max": 1, "annual_target": 1,
        },
        "mustard": {
            "states": ["mustard"], "annual_min": 0,
            "annual_max": 1, "annual_target": 0,
            "horizon_min": 1,
        },
    }}
    selected = select_farm_plan(fields, crop_mix, years=2, beam_width=20)
    assert selected["selected_ranks"] == {"Field1": 2}
    assert selected["within_historical_ranges"] is True
