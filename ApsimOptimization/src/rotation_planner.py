"""Source-backed crop-transition rules and multi-year sequence planning."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RotationOption:
    crop_id: str
    crop_name: str
    score: float
    code: str


class RotationRules:
    def __init__(self, payload: dict):
        if payload.get("schema_version") != 1:
            raise ValueError("Unsupported rotation-rule schema")
        if payload.get("orientation", {}).get("directional") is not True:
            raise ValueError("Rotation matrix must be directional")
        self.payload = payload
        self.catalog = {item["id"]: item for item in payload["crop_catalog"]}
        if len(self.catalog) != len(payload["crop_catalog"]):
            raise ValueError("Duplicate crop id in rotation catalog")
        self.predecessor_catalog = {
            item["id"]: item for item in payload["predecessor_catalog"]
        }
        if len(self.predecessor_catalog) != len(payload["predecessor_catalog"]):
            raise ValueError("Duplicate predecessor id in rotation catalog")
        self.scores = payload["transition_scores"]
        self.codes = payload["transition_codes"]
        self._successor_aliases = {}
        for crop_id, item in self.catalog.items():
            for alias in (crop_id, item["name"]):
                key = self._normalise(alias)
                if (
                    key in self._successor_aliases
                    and self._successor_aliases[key] != crop_id
                ):
                    raise ValueError(f"Ambiguous crop alias: {alias}")
                self._successor_aliases[key] = crop_id
        self._predecessor_aliases = {}
        for crop_id, item in self.predecessor_catalog.items():
            for alias in (crop_id, item["name"]):
                key = self._normalise(alias)
                if (
                    key in self._predecessor_aliases
                    and self._predecessor_aliases[key] != crop_id
                ):
                    raise ValueError(f"Ambiguous predecessor alias: {alias}")
                self._predecessor_aliases[key] = crop_id

    @classmethod
    def from_file(cls, path: str | Path) -> "RotationRules":
        return cls(json.loads(Path(path).read_text(encoding="utf-8-sig")))

    @staticmethod
    def _normalise(value: str) -> str:
        return " ".join(str(value).replace("\u00ad", "-").strip().casefold().split())

    def resolve(self, crop: str) -> str:
        """Resolve a crop which is being considered as the next crop."""
        key = self._normalise(crop)
        if key not in self._successor_aliases:
            raise ValueError(f"Crop is absent from the soil-scientist matrix: {crop}")
        return self._successor_aliases[key]

    def resolve_predecessor(self, crop: str) -> str:
        """Resolve either a matrix row or a previous successor to its row id."""
        key = self._normalise(crop)
        if key in self._predecessor_aliases:
            return self._predecessor_aliases[key]
        if key in self._successor_aliases:
            successor_id = self._successor_aliases[key]
            return self.catalog[successor_id]["predecessor_id"]
        raise ValueError(f"Predecessor is absent from the soil-scientist matrix: {crop}")

    def crop_name(self, crop: str) -> str:
        return self.catalog[self.resolve(crop)]["name"]

    def predecessor_name(self, crop: str) -> str:
        return self.predecessor_catalog[self.resolve_predecessor(crop)]["name"]

    def transition(self, predecessor: str, successor: str) -> RotationOption:
        predecessor_id = self.resolve_predecessor(predecessor)
        next_id = self.resolve(successor)
        try:
            score = float(self.scores[predecessor_id][next_id])
            code = self.codes[predecessor_id][next_id]
        except KeyError as exc:
            raise ValueError(
                f"Transition is missing from the matrix: {predecessor_id} -> {next_id}"
            ) from exc
        return RotationOption(next_id, self.catalog[next_id]["name"], score, code)

    def rank_successors(
        self, predecessor: str, candidates: list[str] | tuple[str, ...] | None = None,
        minimum_score: float = 0.5,
    ) -> list[RotationOption]:
        candidate_ids = (
            [self.resolve(crop) for crop in candidates]
            if candidates is not None else list(self.catalog)
        )
        options = [self.transition(predecessor, crop) for crop in candidate_ids]
        options = [option for option in options if option.score >= minimum_score]
        return sorted(options, key=lambda option: (-option.score, option.crop_name, option.crop_id))

    def validate_sequence(
        self, predecessor: str, sequence: list[str] | tuple[str, ...],
        minimum_score: float = 0.5,
    ) -> dict:
        previous = self.resolve_predecessor(predecessor)
        steps = []
        for year_index, crop in enumerate(sequence, start=1):
            option = self.transition(previous, crop)
            steps.append({
                "step": year_index,
                "predecessor_id": previous,
                "predecessor_name": self.predecessor_name(previous),
                "crop_id": option.crop_id,
                "crop_name": option.crop_name,
                "code": option.code,
                "score": option.score,
                "allowed": option.score >= minimum_score,
            })
            previous = self.catalog[option.crop_id]["predecessor_id"]
        total = sum(step["score"] for step in steps)
        return {
            "allowed": all(step["allowed"] for step in steps),
            "minimum_score": minimum_score,
            "total_score": total,
            "mean_score": total / len(steps) if steps else None,
            "steps": steps,
        }

    def top_sequences(
        self, predecessor: str, candidates: list[str] | tuple[str, ...],
        years: int, top_k: int = 5, minimum_score: float = 0.5,
    ) -> list[dict]:
        if years <= 0:
            raise ValueError("years must be positive")
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        initial = self.resolve_predecessor(predecessor)
        candidate_ids = tuple(dict.fromkeys(self.resolve(crop) for crop in candidates))
        if not candidate_ids:
            raise ValueError("At least one candidate crop is required")

        states: dict[str, list[tuple[float, tuple[str, ...], tuple[str, ...]]]] = {
            initial: [(0.0, (), ())]
        }
        for _ in range(years):
            next_states: dict[str, list[tuple[float, tuple[str, ...], tuple[str, ...]]]] = {}
            for previous_id, paths in states.items():
                for score_so_far, sequence, codes in paths:
                    for next_id in candidate_ids:
                        option = self.transition(previous_id, next_id)
                        if option.score < minimum_score:
                            continue
                        next_predecessor = self.catalog[next_id]["predecessor_id"]
                        next_states.setdefault(next_predecessor, []).append((
                            score_so_far + option.score,
                            sequence + (next_id,),
                            codes + (option.code,),
                        ))
            if not next_states:
                raise ValueError("No feasible rotation sequence for the supplied candidates")
            states = {
                crop_id: sorted(paths, key=lambda item: (-item[0], item[1]))[:top_k]
                for crop_id, paths in next_states.items()
            }

        paths = sorted(
            (path for paths in states.values() for path in paths),
            key=lambda item: (-item[0], item[1]),
        )[:top_k]
        return [
            {
                "rank": rank,
                "initial_crop_id": initial,
                "initial_crop_name": self.predecessor_name(initial),
                "sequence_ids": list(sequence),
                "sequence_names": [self.crop_name(crop) for crop in sequence],
                "transition_codes": list(codes),
                "total_score": score,
                "mean_score": score / years,
            }
            for rank, (score, sequence, codes) in enumerate(paths, start=1)
        ]


def plan_fields(
    rules: RotationRules, initial_crops: dict[str, str], candidates: list[str],
    years: int, top_k: int = 5, minimum_score: float = 0.5,
) -> dict:
    if not initial_crops:
        raise ValueError("At least one field is required")
    fields = {}
    for field, predecessor in initial_crops.items():
        alternatives = rules.top_sequences(
            predecessor, candidates, years, top_k=top_k,
            minimum_score=minimum_score,
        )
        best_score = alternatives[0]["total_score"]
        fields[field] = {
            "initial_crop_id": rules.resolve_predecessor(predecessor),
            "alternatives": alternatives,
            "retained_best_ties": sum(
                alternative["total_score"] == best_score for alternative in alternatives
            ),
        }
    return {
        "years": years,
        "minimum_transition_score": minimum_score,
        "candidate_crop_ids": [rules.resolve(crop) for crop in candidates],
        "fields": fields,
    }


def _validate_state_model(state_model: dict) -> tuple[dict, tuple[str, ...]]:
    states = state_model.get("states", {})
    candidates = tuple(state_model.get("candidate_states", ()))
    if not states or not candidates:
        raise ValueError("State model requires states and candidate_states")
    unknown_candidates = sorted(set(candidates) - set(states))
    if unknown_candidates:
        raise ValueError(f"Unknown candidate states: {unknown_candidates}")
    for state_id, state in states.items():
        if not state.get("name"):
            raise ValueError(f"Incomplete rotation state: {state_id}")
        forced = state.get("forced_next")
        source_allowed = tuple(state.get("source_allowed_next", ()))
        source_predecessors = tuple(state.get("source_allowed_predecessors", ()))
        if (
            not state.get("matrix_predecessor")
            and not forced
            and not source_allowed
            and not source_predecessors
        ):
            raise ValueError(f"Rotation state lacks matrix_predecessor: {state_id}")
        optional = state.get("optional_continuations", [])
        referenced = ([forced] if forced else []) + list(optional) + list(source_allowed)
        unknown = sorted(set(referenced) - set(states))
        if unknown:
            raise ValueError(f"{state_id}: unknown continuation states {unknown}")
        unknown_predecessors = sorted(set(source_predecessors) - set(states))
        if unknown_predecessors:
            raise ValueError(
                f"{state_id}: unknown source-backed predecessors "
                f"{unknown_predecessors}"
            )
    for state_id in candidates:
        state = states[state_id]
        if not state.get("matrix_successor") and not state.get(
            "source_allowed_predecessors"
        ):
            raise ValueError(
                f"Candidate state lacks matrix_successor or "
                f"source_allowed_predecessors: {state_id}"
            )
    return states, candidates


def top_stateful_sequences(
    rules: RotationRules, initial_state: str, state_model: dict, years: int,
    top_k: int | None = 5, minimum_score: float = 0.5,
) -> list[dict]:
    """Plan annual states while distinguishing stand continuation from reseeding."""
    if years <= 0:
        raise ValueError("years must be positive")
    if top_k is not None and top_k <= 0:
        raise ValueError("top_k must be positive")
    states, candidates = _validate_state_model(state_model)
    if initial_state not in states:
        raise ValueError(f"Unknown initial rotation state: {initial_state}")

    # score sum, scored transition count, continuation count, sequence, codes
    paths = [(initial_state, 0.0, 0, 0, (), ())]
    for _ in range(years):
        next_paths = []
        for current_id, score_sum, score_count, continuations, sequence, codes in paths:
            current = states[current_id]
            forced = current.get("forced_next")
            optional = tuple(current.get("optional_continuations", ()))
            source_allowed = tuple(current.get("source_allowed_next", ()))
            next_ids = (
                (forced,) if forced else
                source_allowed if source_allowed else
                candidates + optional
            )
            for next_id in next_ids:
                if next_id == forced:
                    code = current.get("forced_next_code", "П")
                    next_paths.append((
                        next_id, score_sum, score_count,
                        continuations + (1 if code == "П" else 0),
                        sequence + (next_id,), codes + (code,),
                    ))
                    continue
                if next_id in optional:
                    next_paths.append((
                        next_id, score_sum, score_count, continuations + 1,
                        sequence + (next_id,), codes + ("П",),
                    ))
                    continue
                if next_id in source_allowed:
                    next_paths.append((
                        next_id, score_sum, score_count, continuations,
                        sequence + (next_id,), codes + ("И",),
                    ))
                    continue
                next_state = states[next_id]
                if current_id in next_state.get("source_allowed_predecessors", ()):
                    next_paths.append((
                        next_id, score_sum, score_count, continuations,
                        sequence + (next_id,), codes + ("И",),
                    ))
                    continue
                if not current.get("matrix_predecessor") or not next_state.get(
                    "matrix_successor"
                ):
                    continue
                option = rules.transition(
                    current["matrix_predecessor"], next_state["matrix_successor"]
                )
                if option.score < minimum_score:
                    continue
                next_paths.append((
                    next_id, score_sum + option.score, score_count + 1,
                    continuations, sequence + (next_id,), codes + (option.code,),
                ))
        if not next_paths:
            raise ValueError("No feasible stateful rotation sequence")
        paths = next_paths

    def ranking(path) -> tuple:
        _, score_sum, score_count, _, sequence, _ = path
        # A sequence made only from source-backed transitions has no matrix
        # quality score. Keep it feasible, but do not rank it as a perfect
        # matrix-scored sequence.
        mean = score_sum / score_count if score_count else -1.0
        return (-mean, sequence)

    selected = sorted(paths, key=ranking)
    if top_k is not None:
        selected = selected[:top_k]
    alternatives = []
    for rank, (last_id, score_sum, score_count, continuations, sequence, codes) in enumerate(
        selected, start=1
    ):
        mean = score_sum / score_count if score_count else None
        alternatives.append({
            "rank": rank,
            "initial_crop_id": initial_state,
            "initial_crop_name": states[initial_state]["name"],
            "sequence_ids": list(sequence),
            "sequence_names": [states[state_id]["name"] for state_id in sequence],
            "transition_codes": list(codes),
            "total_score": score_sum,
            "mean_score": mean,
            "matrix_transition_count": score_count,
            "continuation_years": continuations,
            "terminal_state_id": last_id,
        })
    return alternatives


def plan_fields_stateful(
    rules: RotationRules, initial_states: dict[str, str], state_model: dict,
    years: int, top_k: int | None = 5, minimum_score: float = 0.5,
) -> dict:
    if not initial_states:
        raise ValueError("At least one field is required")
    states, candidates = _validate_state_model(state_model)
    fields = {}
    for field, initial_state in initial_states.items():
        alternatives = top_stateful_sequences(
            rules, initial_state, state_model, years, top_k=top_k,
            minimum_score=minimum_score,
        )
        best_mean = alternatives[0]["mean_score"]
        fields[field] = {
            "initial_crop_id": initial_state,
            "alternatives": alternatives,
            "retained_best_ties": sum(
                alternative["mean_score"] == best_mean for alternative in alternatives
            ),
        }
    return {
        "years": years,
        "minimum_transition_score": minimum_score,
        "candidate_crop_ids": list(candidates),
        "rotation_states": states,
        "continuation_code": "П",
        "continuation_meaning": "Продолжение существующего травостоя; не матричный переход",
        "fixed_code": "Ф",
        "fixed_meaning": "Переход зафиксирован по фактической книге; не матричная оценка",
        "source_history_code": "И",
        "source_history_meaning": "Переход подтверждён историей хозяйства; не матричная оценка",
        "fields": fields,
    }


def top_stateful_sequences_open_start(
    rules: RotationRules, state_model: dict, years: int,
    start_states: list[str] | tuple[str, ...] | None = None,
    top_k: int | None = 5, minimum_score: float = 0.5,
) -> list[dict]:
    """Plan a bounded historical period when its preceding crop is unknown.

    The first state is deliberately unscored. All later sowing transitions are
    checked against the supplied matrix, while forced stand continuations keep
    the usual ``П`` code.
    """
    if years <= 0:
        raise ValueError("years must be positive")
    if top_k is not None and top_k <= 0:
        raise ValueError("top_k must be positive")
    states, candidates = _validate_state_model(state_model)
    first_states = tuple(dict.fromkeys(start_states or candidates))
    if not first_states:
        raise ValueError("At least one first-year state is required")
    unknown = sorted(set(first_states) - set(states))
    if unknown:
        raise ValueError(f"Unknown first-year states: {unknown}")

    alternatives = []
    for first_state in first_states:
        if years == 1:
            tails = [{
                "sequence_ids": [],
                "sequence_names": [],
                "transition_codes": [],
                "total_score": 0.0,
                "mean_score": None,
                "matrix_transition_count": 0,
                "continuation_years": 0,
                "terminal_state_id": first_state,
            }]
        else:
            tails = top_stateful_sequences(
                rules, first_state, state_model, years - 1,
                top_k=None, minimum_score=minimum_score,
            )
        for tail in tails:
            alternatives.append({
                "initial_crop_id": None,
                "initial_crop_name": "Предшественник до начала периода не задан",
                "sequence_ids": [first_state, *tail["sequence_ids"]],
                "sequence_names": [states[first_state]["name"], *tail["sequence_names"]],
                "transition_codes": ["С", *tail["transition_codes"]],
                "total_score": tail["total_score"],
                "mean_score": tail["mean_score"],
                "matrix_transition_count": tail["matrix_transition_count"],
                "continuation_years": tail["continuation_years"],
                "terminal_state_id": tail["terminal_state_id"],
            })

    def ranking(alternative: dict) -> tuple:
        mean = alternative["mean_score"]
        # A one-year open-start plan contains no transition to score.
        quality = mean if mean is not None else 1.0
        return (-quality, tuple(alternative["sequence_ids"]))

    selected = sorted(alternatives, key=ranking)
    if top_k is not None:
        selected = selected[:top_k]
    for rank, alternative in enumerate(selected, start=1):
        alternative["rank"] = rank
    return selected


def plan_fields_stateful_open_start(
    rules: RotationRules, fields: list[str] | tuple[str, ...], state_model: dict,
    years: int, start_states: list[str] | tuple[str, ...] | None = None,
    top_k: int | None = 5, minimum_score: float = 0.5,
) -> dict:
    """Create field alternatives without inventing a pre-period crop."""
    field_ids = tuple(dict.fromkeys(fields))
    if not field_ids:
        raise ValueError("At least one field is required")
    states, candidates = _validate_state_model(state_model)
    alternatives = top_stateful_sequences_open_start(
        rules, state_model, years, start_states=start_states,
        top_k=top_k, minimum_score=minimum_score,
    )
    best_mean = alternatives[0]["mean_score"]
    result_fields = {}
    for field in field_ids:
        field_alternatives = [dict(item) for item in alternatives]
        result_fields[field] = {
            "initial_crop_id": None,
            "alternatives": field_alternatives,
            "retained_best_ties": sum(
                item["mean_score"] == best_mean for item in field_alternatives
            ),
        }
    return {
        "years": years,
        "minimum_transition_score": minimum_score,
        "candidate_crop_ids": list(candidates),
        "first_year_state_ids": list(start_states or candidates),
        "first_year_transition_scored": False,
        "first_year_code": "С",
        "first_year_meaning": "Начальное состояние; предшественник до периода неизвестен",
        "rotation_states": states,
        "continuation_code": "П",
        "continuation_meaning": "Продолжение существующего травостоя; не матричный переход",
        "fields": result_fields,
    }


def select_farm_plan(
    fields: dict, crop_mix: dict, years: int, beam_width: int = 5000,
) -> dict:
    """Select one field sequence using source-backed whole-farm crop ranges."""
    if beam_width <= 0:
        raise ValueError("beam_width must be positive")
    category_specs = crop_mix.get("categories", {})
    if not category_specs:
        raise ValueError("farm_crop_mix.categories is required")
    categories = tuple(category_specs)
    state_to_category = {}
    for category, spec in category_specs.items():
        for state_id in spec.get("states", ()):
            if state_id in state_to_category:
                raise ValueError(f"Rotation state assigned twice: {state_id}")
            state_to_category[state_id] = category

    minimum = tuple(int(category_specs[c]["annual_min"]) for c in categories)
    maximum = tuple(int(category_specs[c]["annual_max"]) for c in categories)
    target = tuple(float(category_specs[c]["annual_target"]) for c in categories)
    if any(low < 0 or high < low for low, high in zip(minimum, maximum)):
        raise ValueError("Invalid annual crop-mix bounds")

    def field_number(field: str) -> tuple[int, str]:
        suffix = field.removeprefix("Field")
        return (int(suffix), field) if suffix.isdigit() else (10**9, field)

    field_ids = sorted(fields, key=field_number)
    horizon_minimum = tuple(
        int(category_specs[c].get("horizon_min", 0)) for c in categories
    )
    horizon_maximum = tuple(
        int(category_specs[c].get("horizon_max", len(field_ids) * years))
        for c in categories
    )
    if any(
        low < 0 or high < low
        for low, high in zip(horizon_minimum, horizon_maximum)
    ):
        raise ValueError("Invalid horizon crop-mix bounds")
    zero_counts = (0,) * (years * len(categories))
    # counts -> tuple of (field, alternative rank)
    beam = {zero_counts: ()}
    best_mean_by_field = {}
    excluded_lower_score_alternatives = {}

    for position, field in enumerate(field_ids):
        alternatives = fields[field]["alternatives"]
        quality = lambda item: (
            float(item["mean_score"])
            if item.get("mean_score") is not None
            else -1.0
        )
        best_quality = max(quality(item) for item in alternatives)
        eligible = [item for item in alternatives if quality(item) == best_quality]
        best_mean_by_field[field] = (
            eligible[0].get("mean_score") if eligible else None
        )
        excluded_lower_score_alternatives[field] = len(alternatives) - len(eligible)
        patterns = {}
        for alternative in eligible:
            try:
                pattern = tuple(
                    categories.index(state_to_category[state_id])
                    for state_id in alternative["sequence_ids"]
                )
            except KeyError as exc:
                raise ValueError(f"State absent from farm crop mix: {exc.args[0]}") from exc
            patterns.setdefault(pattern, alternative)

        remaining = len(field_ids) - position - 1
        candidates = {}
        for counts, selection in beam.items():
            for pattern, alternative in patterns.items():
                updated = list(counts)
                for year_index, category_index in enumerate(pattern):
                    updated[year_index * len(categories) + category_index] += 1
                updated = tuple(updated)
                candidates.setdefault(
                    updated, selection + ((field, alternative["rank"]),)
                )

        def lower_bound(item) -> tuple:
            counts, selection = item
            violation = 0
            distance = 0.0
            for year_index in range(years):
                for category_index in range(len(categories)):
                    value = counts[year_index * len(categories) + category_index]
                    low = minimum[category_index]
                    high = maximum[category_index]
                    wanted = target[category_index]
                    violation += max(low - (value + remaining), 0) + max(value - high, 0)
                    if wanted < value:
                        distance += (value - wanted) ** 2
                    elif wanted > value + remaining:
                        distance += (wanted - value - remaining) ** 2
            for category_index in range(len(categories)):
                total = sum(
                    counts[year_index * len(categories) + category_index]
                    for year_index in range(years)
                )
                low = horizon_minimum[category_index]
                high = horizon_maximum[category_index]
                remaining_slots = remaining * years
                violation += max(low - (total + remaining_slots), 0)
                violation += max(total - high, 0)
            return violation, distance, selection

        beam = dict(sorted(candidates.items(), key=lower_bound)[:beam_width])

    def final_score(item) -> tuple:
        counts, selection = item
        violation = 0
        distance = 0.0
        for year_index in range(years):
            for category_index in range(len(categories)):
                value = counts[year_index * len(categories) + category_index]
                low = minimum[category_index]
                high = maximum[category_index]
                wanted = target[category_index]
                violation += max(low - value, 0) + max(value - high, 0)
                distance += (value - wanted) ** 2
        for category_index in range(len(categories)):
            total = sum(
                counts[year_index * len(categories) + category_index]
                for year_index in range(years)
            )
            violation += max(horizon_minimum[category_index] - total, 0)
            violation += max(total - horizon_maximum[category_index], 0)
        return violation, distance, selection

    best_counts, best_selection = min(beam.items(), key=final_score)
    violation, distance, _ = final_score((best_counts, best_selection))
    annual_counts = []
    for year_index in range(years):
        annual_counts.append({
            category: best_counts[year_index * len(categories) + category_index]
            for category_index, category in enumerate(categories)
        })
    return {
        "selected_ranks": dict(best_selection),
        "within_historical_ranges": violation == 0,
        "range_violation_units": violation,
        "squared_target_deviation": distance,
        "annual_counts": annual_counts,
        "categories": category_specs,
        "source": crop_mix.get("source"),
        "beam_width": beam_width,
        "selection_priority": [
            "maximum_mean_transition_score_per_field",
            "minimum_historical_range_violation",
            "minimum_squared_deviation_from_historical_mean",
        ],
        "best_mean_transition_score_by_field": best_mean_by_field,
        "excluded_lower_score_alternatives": excluded_lower_score_alternatives,
    }
