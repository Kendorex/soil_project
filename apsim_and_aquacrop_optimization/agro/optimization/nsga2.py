"""Reusable pymoo searches; no engine, model, filesystem or economics imports."""
from __future__ import annotations

import math


def checked(result, n_objectives, n_constraints):
    objectives, constraints = result['F'], result['G']
    if len(objectives) != n_objectives or len(constraints) != n_constraints:
        raise ValueError('Invalid objective/constraint dimensions')
    if not all(math.isfinite(float(v)) for v in [*objectives, *constraints]):
        raise ValueError('Non-finite objective/constraint')
    return objectives, constraints


def nondominated(candidates):
    """Return all successful feasible nondominated candidates in input order."""
    import numpy as np
    from pymoo.util.nds.non_dominated_sorting import NonDominatedSorting

    valid = [row for row in candidates if row['status'] == 'ok'
             and all(float(g) <= 0 for g in row['G'])]
    if not valid:
        return []
    front = NonDominatedSorting().do(np.asarray([row['F'] for row in valid]),
                                    only_non_dominated_front=True)
    return [valid[int(index)] for index in front]


def integer_search_settings(settings, n_objectives):
    """Resolve algorithm and reference-direction budget before any expensive evaluation."""
    resolved = dict(settings)
    if n_objectives < 1:
        raise ValueError('At least one objective is required')
    for key in ('population', 'generations'):
        value = resolved[key]
        if isinstance(value, bool) or int(value) != value or int(value) < 1:
            raise ValueError(f'{key} must be a positive integer')
        resolved[key] = int(value)
    algorithm = str(resolved.get('algorithm', 'nsga2')).lower()
    if algorithm == 'auto':
        algorithm = 'nsga2' if n_objectives <= 3 else 'nsga3'
    if algorithm not in ('nsga2', 'nsga3'):
        raise ValueError('algorithm must be auto, nsga2 or nsga3')
    resolved['algorithm'] = algorithm
    if algorithm == 'nsga3':
        if n_objectives < 2:
            raise ValueError('NSGA-III requires at least two objectives; use NSGA-II for one')
        partitions = resolved.get('reference_partitions')
        if partitions is None:
            partitions = 1
            while math.comb(n_objectives + partitions, n_objectives - 1) <= resolved['population']:
                partitions += 1
        if isinstance(partitions, bool) or int(partitions) != partitions or int(partitions) < 1:
            raise ValueError('reference_partitions must be a positive integer')
        count = math.comb(n_objectives + int(partitions) - 1, n_objectives - 1)
        if count > resolved['population']:
            raise ValueError(f'NSGA-III needs population >= {count} for {partitions} reference partitions')
        resolved.update(reference_partitions=int(partitions), reference_direction_count=count)
    return resolved


def search_integer(options, evaluate, settings, n_objectives, n_constraints, anchored=True, decision_repair=None):
    import numpy as np
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.core.problem import ElementwiseProblem
    from pymoo.core.sampling import Sampling
    from pymoo.operators.crossover.sbx import SBX
    from pymoo.operators.mutation.pm import PM
    from pymoo.operators.repair.rounding import RoundingRepair
    from pymoo.operators.sampling.rnd import IntegerRandomSampling
    from pymoo.optimize import minimize

    settings = integer_search_settings(settings, n_objectives)
    if not options or any(len(values) == 0 for values in options):
        raise ValueError('Every integer variable needs at least one option')

    class Problem(ElementwiseProblem):
        def __init__(self):
            super().__init__(n_var=len(options), n_obj=n_objectives,
                             n_ieq_constr=n_constraints, xl=np.zeros(len(options), dtype=int),
                             xu=np.asarray([len(values)-1 for values in options]), vtype=int)

        def _evaluate(self, x, out, *args, **kwargs):
            out['F'], out['G'] = checked(evaluate(x), n_objectives, n_constraints)

    class SamplingWithAnchor(Sampling):
        def _do(self, problem, n_samples, **kwargs):
            samples = IntegerRandomSampling()._do(problem, n_samples, **kwargs)
            if anchored and n_samples:
                samples[0] = np.zeros(len(options), dtype=int)
            return samples

    operators = dict(pop_size=settings['population'], sampling=SamplingWithAnchor(),
                     crossover=SBX(prob=0.9, eta=15, vtype=float, repair=RoundingRepair()),
                     mutation=PM(eta=20, vtype=float, repair=RoundingRepair()),
                     eliminate_duplicates=True)
    if decision_repair is not None:
        from pymoo.core.repair import Repair
        class DecisionRepair(Repair):
            def _do(self,problem,X,**kwargs):
                return np.asarray([decision_repair(np.rint(row).astype(int)) for row in X],dtype=int)
        operators['repair']=DecisionRepair()
    if settings['algorithm'] == 'nsga3':
        from pymoo.algorithms.moo.nsga3 import NSGA3
        from pymoo.operators.selection.tournament import TournamentSelection
        from pymoo.util.ref_dirs import get_reference_directions

        def seeded_cv_tournament(pop, pairs, random_state=None, **kwargs):
            # pymoo 0.6.1.6's default NSGA-III comparator does not forward its
            # generator when breaking equal constraint-violation ties.
            rng = random_state if random_state is not None else np.random
            winners = []
            for a, b in pairs:
                ca, cb = float(pop[a].CV[0]), float(pop[b].CV[0])
                if (ca > 0 or cb > 0) and ca != cb:
                    winners.append(a if ca < cb else b)
                else:
                    winners.append(rng.choice([a, b]))
            return np.asarray(winners, dtype=int)[:, None]

        directions = get_reference_directions('das-dennis', n_objectives,
                                             n_partitions=settings['reference_partitions'])
        algorithm = NSGA3(ref_dirs=directions,
                          selection=TournamentSelection(func_comp=seeded_cv_tournament), **operators)
    else:
        algorithm = NSGA2(**operators)
    return minimize(Problem(), algorithm, ('n_gen', int(settings['generations'])),
                    seed=int(settings['seed']), verbose=settings.get('verbose', True))


def search_mixed(variables, evaluate, settings, n_objectives, n_constraints):
    from pymoo.algorithms.moo.nsga2 import NSGA2, binary_tournament
    from pymoo.core.mixed import (MixedVariableSampling, MixedVariableMating,
                                 MixedVariableDuplicateElimination)
    from pymoo.core.problem import ElementwiseProblem
    from pymoo.operators.selection.tournament import TournamentSelection
    from pymoo.optimize import minimize

    class Problem(ElementwiseProblem):
        def __init__(self):
            super().__init__(vars=variables, n_obj=n_objectives, n_ieq_constr=n_constraints)

        def _evaluate(self, x, out, *args, **kwargs):
            out['F'], out['G'] = checked(evaluate(x), n_objectives, n_constraints)

    duplicate = MixedVariableDuplicateElimination()
    algorithm = NSGA2(pop_size=int(settings['population']), sampling=MixedVariableSampling(),
                      mating=MixedVariableMating(
                          selection=TournamentSelection(func_comp=binary_tournament),
                          eliminate_duplicates=duplicate), eliminate_duplicates=duplicate)
    return minimize(Problem(), algorithm, ('n_gen', int(settings['generations'])),
                    seed=int(settings['seed']), verbose=settings.get('verbose', True))
