"""Decision-readiness gate for historical multi-field yield simulations.

The gate deliberately avoids an invented absolute MAE/RMSE threshold.  It uses
only internal validation evidence: complete held-out coverage, harvest events,
improvement over a training-period mean, and positive field ranking skill.
"""
from __future__ import annotations

import math

from .multifield_quality import finite, metrics


def _average_ranks(values):
    """Return one-based average ranks, including deterministic tie handling."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = ((start + 1) + end) / 2.0
        for position in range(start, end):
            ranks[order[position]] = rank
        start = end
    return ranks


def spearman_r(observed, predicted):
    """Spearman rank correlation without adding a SciPy dependency."""
    if len(observed) != len(predicted) or len(observed) < 2:
        return None
    ranked_observed = _average_ranks(observed)
    ranked_predicted = _average_ranks(predicted)
    mean_observed = sum(ranked_observed) / len(ranked_observed)
    mean_predicted = sum(ranked_predicted) / len(ranked_predicted)
    covariance = sum(
        (left - mean_observed) * (right - mean_predicted)
        for left, right in zip(ranked_observed, ranked_predicted)
    )
    spread = math.sqrt(
        sum((value - mean_observed) ** 2 for value in ranked_observed)
        * sum((value - mean_predicted) ** 2 for value in ranked_predicted)
    )
    return covariance / spread if spread else None


def _pairs(rows):
    pairs = []
    for row in rows:
        try:
            observed = finite(row.get('observed_yield_t_ha'))
            simulated = finite(row.get('simulated_yield_t_ha'))
        except (TypeError, ValueError):
            continue
        if observed is not None and simulated is not None:
            pairs.append((observed, simulated))
    return pairs


def evaluate_historical_readiness(rows, validation_year=2023):
    """Evaluate whether a model can support field-level optimization decisions.

    All years before ``validation_year`` form the calibration period.  The
    constant benchmark is the mean observed calibration yield, evaluated on the
    untouched validation year.
    """
    rows = list(rows)
    calibration_rows = [row for row in rows if int(row['year']) < validation_year]
    validation_rows = [row for row in rows if int(row['year']) == validation_year]
    calibration_pairs = _pairs(calibration_rows)
    validation_pairs = _pairs(validation_rows)
    training_mean = (
        sum(observed for observed, _ in calibration_pairs) / len(calibration_pairs)
        if calibration_pairs else None
    )
    baseline_rows = [
        {'observed_yield_t_ha': observed, 'simulated_yield_t_ha': training_mean}
        for observed, _ in validation_pairs
    ] if training_mean is not None else []
    validation_metrics = metrics(validation_rows)
    observed = [left for left, _ in validation_pairs]
    simulated = [right for _, right in validation_pairs]
    validation_metrics['spearman_r'] = spearman_r(observed, simulated)
    baseline_metrics = metrics(baseline_rows)

    missing_harvest = [
        {'year': int(row['year']), 'field': row.get('field')}
        for row in validation_rows
        if (finite(row.get('simulated_yield_t_ha')) is None or
            'missing_harvest_event' in str(row.get('diagnostic_flags') or '').split(';'))
    ]
    complete_coverage = bool(validation_rows) and len(validation_pairs) == len(validation_rows)
    harvest_complete = not missing_harvest
    beats_baseline = bool(
        validation_metrics['rmse_t_ha'] is not None
        and baseline_metrics['rmse_t_ha'] is not None
        and validation_metrics['rmse_t_ha'] < baseline_metrics['rmse_t_ha']
    )
    positive_ranking = bool(
        validation_metrics['spearman_r'] is not None
        and validation_metrics['spearman_r'] > 0
    )
    checks = {
        'complete_validation_coverage': {
            'passed': complete_coverage,
            'actual': len(validation_pairs),
            'required': len(validation_rows),
        },
        'all_validation_harvest_events_present': {
            'passed': harvest_complete,
            'missing': missing_harvest,
        },
        'validation_rmse_better_than_training_mean': {
            'passed': beats_baseline,
            'model_rmse_t_ha': validation_metrics['rmse_t_ha'],
            'training_mean_rmse_t_ha': baseline_metrics['rmse_t_ha'],
        },
        'positive_validation_field_ranking': {
            'passed': positive_ranking,
            'spearman_r': validation_metrics['spearman_r'],
        },
    }
    failed = [name for name, check in checks.items() if not check['passed']]
    ready = not failed
    return {
        'status': ('ready_for_decision_optimization' if ready
                   else 'historical_validation_not_ready_for_decision_optimization'),
        'recommendation_allowed': ready,
        'validation_year': validation_year,
        'calibration_years': sorted({int(row['year']) for row in calibration_rows}),
        'training_observed_mean_t_ha': training_mean,
        'validation_model_metrics': validation_metrics,
        'validation_training_mean_metrics': baseline_metrics,
        'checks': checks,
        'failed_checks': failed,
        'threshold_policy': (
            'No absolute agronomic error threshold was assumed. Readiness requires complete '
            'held-out coverage, harvest events, lower RMSE than the calibration-period mean, '
            'and positive Spearman field ranking.'
        ),
    }
