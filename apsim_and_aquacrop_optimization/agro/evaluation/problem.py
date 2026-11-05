"""Bridge simulation results to optimization objectives with explicit capabilities."""
from __future__ import annotations

from pathlib import Path
from dataclasses import asdict
import hashlib
import json

from agro.optimization.nsga2 import checked


class SimulationEvaluator:
    """Bind one model/backend and one objective function for a single search run.

    The cache is local to this instance. Use a new instance when model inputs,
    engine settings or objective definitions change.
    """

    def __init__(self, backend, objective, workdir, n_objectives, n_constraints=0,
                 required_metrics=(), required_decisions=()):
        backend.capabilities.require(required_metrics, required_decisions)
        self.backend = backend
        self.objective = objective
        self.workdir = Path(workdir)
        self.n_objectives = n_objectives
        self.n_constraints = n_constraints
        self.cache = {}

    def __call__(self, decisions):
        key = hashlib.sha256(json.dumps(decisions, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if key not in self.cache:
            result = self.backend.simulate(decisions, self.workdir / key)
            candidate_dir = self.workdir / key
            candidate_dir.mkdir(parents=True, exist_ok=True)
            (candidate_dir / 'simulation.json').write_text(
                json.dumps(asdict(result), allow_nan=False, indent=2), encoding='utf-8')
            summary = dict(self.objective(result, decisions))
            checked(summary, self.n_objectives, self.n_constraints)
            summary.update(status='ok', candidate_id=key, decisions=decisions)
            self.cache[key] = summary
        return self.cache[key]


def harvested_dry_matter(result, decisions):
    """A single objective: maximize total harvested dry matter, in kg.

    Additional economics and soil objectives require explicit model-specific data.
    """
    total = sum(sum(row.harvested_dm_kg_ha.values()) * row.area_ha for row in result.fields)
    return {'F': [-total], 'G': [], 'yield_dm_kg': total}
