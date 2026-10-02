from __future__ import annotations

from typing import Any, Callable, Sequence

import numpy as np

from .search_space import SearchSpace, Technology

LARGE_PENALTY = 1e12


def control_anchor_vectors(space: SearchSpace) -> np.ndarray:
    """Return one no-fertilizer control for every cultivar.

    Random integer sampling can miss the control scheme in a short NSGA-II
    run. These anchors make the lowest-cost reference observable without
    replacing the remaining random initial population.
    """
    try:
        fertilizer_index = space.fertilizer_scheme_ids.index("F0_no_fertilizer")
    except ValueError:
        fertilizer_index = 0
    return np.asarray(
        [
            [0, cultivar_index, 0, 0, 0, fertilizer_index]
            for cultivar_index in range(len(space.cultivars))
        ],
        dtype=int,
    )


def decode_indices(space: SearchSpace, vector: Sequence[int | float]) -> Technology:
    return space.decode(vector)


def encode_technology(space: SearchSpace, technology: Technology) -> tuple[int, ...]:
    return space.encode(technology)


def objective_vector(summary: dict[str, Any], large_penalty: float = LARGE_PENALTY) -> tuple[float, float, float]:
    profit_avg = summary.get("profit_avg", summary.get("profit_mean"))
    if (
        summary.get("run_status") == "failed"
        or profit_avg is None
        or summary.get("profit_min") is None
        or summary.get("controllable_cost") is None
    ):
        return (large_penalty, large_penalty, large_penalty)
    return (-float(profit_avg), float(summary["controllable_cost"]), -float(summary["profit_min"]))


class ApsimTechnologyProblem:
    """Small adapter used both by pymoo and offline unit tests."""

    def __init__(
        self,
        space: SearchSpace,
        evaluator: Callable[[Technology], dict[str, Any]],
        logger: Any = None,
        large_penalty: float = LARGE_PENALTY,
        fertilizer_passes_by_scheme: dict[str, int | float] | None = None,
    ):
        self.space = space
        self.evaluator = evaluator
        self.logger = logger
        self.large_penalty = large_penalty
        self.fertilizer_passes_by_scheme = fertilizer_passes_by_scheme or {}
        self.evaluated: dict[str, dict[str, Any]] = {}
        self.evaluation_rows: list[dict[str, Any]] = []
        self._evaluation_index = 0

    @property
    def lower_bounds(self) -> np.ndarray:
        return np.zeros(6, dtype=int)

    @property
    def upper_bounds(self) -> np.ndarray:
        return np.asarray(self.space.dimensions, dtype=int) - 1

    def decode_candidate(self, vector: Sequence[int | float]) -> Technology:
        clipped = [
            min(max(int(round(value)), 0), size - 1)
            for value, size in zip(vector, self.space.dimensions)
        ]
        return self.space.decode(clipped)

    def _failed_summary(self, technology: Technology, error: Exception) -> dict[str, Any]:
        return {
            "technology_id": technology.key(),
            "fertilizer_scheme_id": technology.fertilizer_scheme_id,
            "cultivar": technology.cultivar,
            "sowing_date": technology.sowing_rule,
            "population": technology.population,
            "depth": technology.depth,
            "row": technology.row,
            "fertilizer_passes": self.fertilizer_passes_by_scheme.get(technology.fertilizer_scheme_id, 0),
            "profit_mean": None,
            "profit_avg": None,
            "profit_min": None,
            "fertilizer_cost": None,
            "financing_cost": None,
            "application_cost": None,
            "fertilizer_controllable_cost": None,
            "seed_rate_kg_ha": None,
            "seed_cost_total": None,
            "seed_cost_adjustment": None,
            "seed_economics_status": "not_evaluated",
            "controllable_cost": None,
            "constraints_status": "not_evaluated",
            "is_feasible": False,
            "rejection_reason": str(error),
            "run_status": "failed",
            "error_message": str(error),
        }

    def evaluate_candidate(self, vector: Sequence[int | float]) -> tuple[Technology, dict[str, Any], tuple[float, float, float]]:
        technology = self.decode_candidate(vector)
        key = technology.key()
        if self.logger:
            self.logger.info(
                "NSGA2 candidate decoded: technology_id=%s sowing=%s cultivar=%s population=%s depth=%s row=%s fertilizer_scheme_id=%s",
                key, technology.sowing_rule, technology.cultivar, technology.population,
                technology.depth, technology.row, technology.fertilizer_scheme_id,
            )
        summary = self.evaluated.get(key)
        reused_candidate = summary is not None
        if summary is None:
            try:
                summary = self.evaluator(technology)
            except Exception as exc:
                if self.logger:
                    self.logger.exception("NSGA2 evaluator failed: technology_id=%s", key)
                summary = self._failed_summary(technology, exc)
            self.evaluated[key] = summary
        objectives = objective_vector(summary, self.large_penalty)
        self._record_evaluation(vector, technology, summary, objectives, reused_candidate)
        if self.logger:
            self.logger.info(
                "NSGA2 objectives returned: technology_id=%s f1=%s f2=%s f3=%s",
                key, objectives[0], objectives[1], objectives[2],
            )
        return technology, summary, objectives

    def _record_evaluation(
        self,
        vector: Sequence[int | float],
        technology: Technology,
        summary: dict[str, Any],
        objectives: tuple[float, float, float],
        reused_candidate: bool,
    ) -> None:
        self._evaluation_index += 1
        row = {
            "generation": None,
            "generation_individual": None,
            "evaluation_id": self._evaluation_index,
            "evaluation_index": self._evaluation_index,
            "candidate_id": self._evaluation_index,
            "technology_id": technology.key(),
            "d_sow": technology.sowing_rule,
            "cultivar": technology.cultivar,
            "population": technology.population,
            "depth": technology.depth,
            "row": technology.row,
            "fertilizer_scheme_id": technology.fertilizer_scheme_id,
            "profit_s1": summary.get("profit_s1_dojdey"),
            "profit_s2": summary.get("profit_s2_holod"),
            "profit_s3": summary.get("profit_s3_norm"),
            "profit_s4": summary.get("profit_s4_zasuha"),
            "profit_avg": summary.get("profit_avg"),
            "profit_min": summary.get("profit_min"),
            "fertilizer_cost": summary.get("fertilizer_cost"),
            "financing_cost": summary.get("financing_cost", summary.get("finance_cost")),
            "application_cost": summary.get("application_cost"),
            "fertilizer_controllable_cost": summary.get("fertilizer_controllable_cost"),
            "seed_rate_kg_ha": summary.get("seed_rate_kg_ha"),
            "seed_cost_total": summary.get("seed_cost_total"),
            "seed_cost_adjustment": summary.get("seed_cost_adjustment"),
            "seed_economics_status": summary.get("seed_economics_status"),
            "controllable_cost": summary.get("controllable_cost"),
            "fertilizer_passes": summary.get("fertilizer_passes"),
            "scenario_count": summary.get("scenario_count", 0),
            "scenario_ids": summary.get("scenario_ids", ""),
            "cache_hit_count": (
                summary.get("scenario_count", 0) if reused_candidate
                else summary.get("cache_hit_count", 0)
            ),
            "actual_apsim_runs": 0 if reused_candidate else summary.get("actual_apsim_runs", 0),
            "constraints_status": summary.get("constraints_status"),
            "run_status": summary.get("run_status"),
            "error_message": summary.get("error_message", ""),
            "f1": objectives[0],
            "f2": objectives[1],
            "f3": objectives[2],
            "date_idx": int(round(vector[0])),
            "cultivar_idx": int(round(vector[1])),
            "population_idx": int(round(vector[2])),
            "depth_idx": int(round(vector[3])),
            "row_idx": int(round(vector[4])),
            "fertilizer_scheme_idx": int(round(vector[5])),
        }
        self.evaluation_rows.append(row)

    def _evaluate(self, X, out, *args, **kwargs) -> None:
        matrix = np.atleast_2d(X)
        if self.logger:
            self.logger.info("NSGA2 evaluation batch: size=%d", len(matrix))
        objectives = []
        constraints = []
        for vector in matrix:
            _technology, summary, obj = self.evaluate_candidate(vector)
            objectives.append(obj)
            constraints.append([1.0 if summary.get("is_feasible") is False else 0.0])
        out["F"] = np.asarray(objectives)
        out["G"] = np.asarray(constraints)
        if np.asarray(X).ndim == 1:
            out["F"] = out["F"][0]
            out["G"] = out["G"][0]


def run_nsga2(
    space: SearchSpace,
    evaluator: Callable[[Technology], dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    try:
        from pymoo.algorithms.moo.nsga2 import NSGA2
        from pymoo.core.problem import ElementwiseProblem
        from pymoo.core.callback import Callback
        from pymoo.core.sampling import Sampling
        from pymoo.operators.crossover.sbx import SBX
        from pymoo.operators.mutation.pm import PM
        from pymoo.operators.repair.rounding import RoundingRepair
        from pymoo.operators.sampling.rnd import IntegerRandomSampling
        from pymoo.optimize import minimize
    except ImportError as exc:
        raise RuntimeError("pymoo is required for --optimize-nsga2; install requirements.txt in the WSL venv") from exc

    adapter = ApsimTechnologyProblem(
        space,
        evaluator,
        logger=settings.get("logger"),
        large_penalty=float(settings.get("large_penalty", LARGE_PENALTY)),
        fertilizer_passes_by_scheme=settings.get("fertilizer_passes_by_scheme"),
    )

    class TechnologyProblem(ElementwiseProblem):
        def __init__(self):
            super().__init__(n_var=6, n_obj=3, n_ieq_constr=1, xl=adapter.lower_bounds, xu=adapter.upper_bounds, vtype=int)

        def _evaluate(self, x, out, *args, **kwargs):
            adapter._evaluate(x, out, *args, **kwargs)

    class GenerationCallback(Callback):
        def __init__(self):
            super().__init__()
            self.cursor = 0

        def notify(self, algorithm):
            generation = int(algorithm.n_gen)
            new_rows = adapter.evaluation_rows[self.cursor:]
            for individual, row in enumerate(new_rows, start=1):
                row["generation"] = generation
                row["generation_individual"] = individual
                if adapter.logger:
                    adapter.logger.info(
                        "NSGA2 evaluation: generation=%s individual=%s evaluation_id=%s technology_id=%s "
                        "decoded=(d_sow=%s,cultivar=%s,population=%s,depth=%s,row=%s,fertilizer_scheme_id=%s) "
                        "scenario_count=%s cache_hit_count=%s actual_apsim_runs=%s "
                        "profit_avg=%s profit_min=%s controllable_cost=%s f1=%s f2=%s f3=%s",
                        generation, individual, row["evaluation_id"], row["technology_id"],
                        row["d_sow"], row["cultivar"], row["population"], row["depth"], row["row"],
                        row["fertilizer_scheme_id"], row["scenario_count"], row["cache_hit_count"],
                        row["actual_apsim_runs"], row["profit_avg"], row["profit_min"],
                        row["controllable_cost"], row["f1"], row["f2"], row["f3"],
                    )
            self.cursor = len(adapter.evaluation_rows)

    class AnchoredIntegerSampling(Sampling):
        def _do(self, problem, n_samples, **kwargs):
            samples = IntegerRandomSampling()._do(problem, n_samples, **kwargs)
            anchors = control_anchor_vectors(space)
            count = min(len(anchors), n_samples)
            samples[:count] = anchors[:count]
            if adapter.logger:
                adapter.logger.info(
                    "NSGA2 initial control anchors: count=%d vectors=%s",
                    count,
                    anchors[:count].tolist(),
                )
            return samples

    algorithm = NSGA2(
        pop_size=int(settings["population_size"]),
        sampling=AnchoredIntegerSampling(),
        crossover=SBX(prob=0.9, eta=15, vtype=float, repair=RoundingRepair()),
        mutation=PM(eta=20, vtype=float, repair=RoundingRepair()),
        eliminate_duplicates=True,
    )
    result = minimize(
        TechnologyProblem(), algorithm, ("n_gen", int(settings["generations"])),
        seed=int(settings.get("seed", 42)), verbose=True, callback=GenerationCallback(),
    )
    if result.X is None:
        return {"pareto": [], "evaluations": adapter.evaluation_rows}
    vectors = np.atleast_2d(result.X)
    pareto = []
    for vector in vectors:
        technology = adapter.decode_candidate(vector)
        summary = dict(adapter.evaluated[technology.key()])
        objectives = objective_vector(summary, adapter.large_penalty)
        summary.update({
            "date_idx": int(round(vector[0])), "cultivar_idx": int(round(vector[1])),
            "population_idx": int(round(vector[2])), "depth_idx": int(round(vector[3])),
            "row_idx": int(round(vector[4])), "fertilizer_scheme_idx": int(round(vector[5])),
            "f1": objectives[0], "f2": objectives[1], "f3": objectives[2],
        })
        pareto.append(summary)
    return {
        "pareto": sorted(pareto, key=lambda row: (
            row.get("profit_avg") is None,
            -(row.get("profit_avg") or -1e99),
            row.get("controllable_cost") if row.get("controllable_cost") is not None else 1e99,
            -(row.get("profit_min") or -1e99),
            row["technology_id"],
        )),
        "evaluations": adapter.evaluation_rows,
    }
