"""Transparent empirical yield corrections with blocked-year validation."""
from __future__ import annotations

import math


def error_metrics(rows, prediction_key='prediction'):
    pairs = [(float(row['observed']), float(row[prediction_key])) for row in rows]
    errors = [predicted-observed for observed, predicted in pairs]
    observed = [value for value, _ in pairs]
    predicted = [value for _, value in pairs]
    mean_observed = sum(observed)/len(observed)
    mean_predicted = sum(predicted)/len(predicted)
    spread = math.sqrt(sum((v-mean_observed)**2 for v in observed) *
                       sum((v-mean_predicted)**2 for v in predicted))
    covariance = sum((a-mean_observed)*(b-mean_predicted)
                     for a, b in pairs)
    return {
        'n': len(rows),
        'mae_t_ha': sum(abs(value) for value in errors)/len(errors),
        'rmse_t_ha': math.sqrt(sum(value*value for value in errors)/len(errors)),
        'bias_t_ha': sum(errors)/len(errors),
        'pearson_r': covariance/spread if spread else None,
    }


def _mean(values):
    return sum(values)/len(values)


def fit_group_correction(rows, group_keys=(), shrinkage=0.0):
    residuals = [float(row['observed'])-float(row['simulated']) for row in rows]
    global_adjustment = _mean(residuals)
    adjustments = {}
    if group_keys:
        grouped = {}
        for row, residual in zip(rows, residuals):
            key = tuple(row[name] for name in group_keys)
            grouped.setdefault(key, []).append(residual)
        for key, values in grouped.items():
            adjustments[key] = ((sum(values)+shrinkage*global_adjustment) /
                                (len(values)+shrinkage))
    return {'family': 'group_additive', 'group_keys': list(group_keys),
            'shrinkage': float(shrinkage), 'global_adjustment': global_adjustment,
            'adjustments': {'\u241f'.join(key): value for key, value in adjustments.items()}}


def predict_group(model, row):
    keys = model['group_keys']
    key = '\u241f'.join(str(row[name]) for name in keys)
    adjustment = model['adjustments'].get(key, model['global_adjustment'])
    return max(0.0, float(row['simulated'])+adjustment)


def fit_ridge_correction(rows, penalty):
    import numpy as np
    crops = sorted({str(row['crop_group']) for row in rows})
    fields = sorted({str(row['field']) for row in rows})
    # Intercept + APSIM yield + one-hot effects. Ridge makes the full one-hot
    # representation identifiable while keeping every coefficient auditable.
    def vector(row):
        return ([1.0, float(row['simulated'])] +
                [float(row['crop_group'] == value) for value in crops] +
                [float(row['field'] == value) for value in fields])
    x = np.asarray([vector(row) for row in rows], dtype=float)
    y = np.asarray([float(row['observed']) for row in rows], dtype=float)
    regularizer = np.eye(x.shape[1])*float(penalty)
    regularizer[0, 0] = 0.0
    coefficients = np.linalg.solve(x.T@x+regularizer, x.T@y)
    return {'family': 'ridge', 'penalty': float(penalty), 'crops': crops,
            'fields': fields, 'coefficients': coefficients.tolist()}


def predict_ridge(model, row):
    values = ([1.0, float(row['simulated'])] +
              [float(row['crop_group'] == value) for value in model['crops']] +
              [float(row['field'] == value) for value in model['fields']])
    return max(0.0, sum(a*b for a, b in zip(model['coefficients'], values)))


def fit_model(rows, specification):
    family = specification['family']
    if family == 'raw':
        return {'family': 'raw'}
    if family == 'training_mean':
        return {'family': 'training_mean',
                'mean_observed_t_ha': _mean([float(row['observed']) for row in rows])}
    if family == 'group_additive':
        return fit_group_correction(rows, specification.get('group_keys', ()),
                                    specification.get('shrinkage', 0.0))
    if family == 'ridge':
        return fit_ridge_correction(rows, specification['penalty'])
    raise ValueError(f'Unknown correction family: {family}')


def predict(model, row):
    if model['family'] == 'raw':
        return float(row['simulated'])
    if model['family'] == 'training_mean':
        return model['mean_observed_t_ha']
    if model['family'] == 'group_additive':
        return predict_group(model, row)
    if model['family'] == 'ridge':
        return predict_ridge(model, row)
    raise ValueError(f'Unknown correction family: {model["family"]}')


def candidate_specifications():
    specs = [{'name': 'raw_apsim', 'family': 'raw'},
             {'name': 'training_mean', 'family': 'training_mean'},
             {'name': 'global_additive', 'family': 'group_additive',
              'group_keys': [], 'shrinkage': 0.0}]
    for key in ('crop_group', 'field'):
        for shrinkage in (0.0, 1.0, 3.0, 10.0):
            specs.append({'name': f'{key}_additive_s{shrinkage:g}',
                          'family': 'group_additive', 'group_keys': [key],
                          'shrinkage': shrinkage})
    for penalty in (0.1, 1.0, 3.0, 10.0, 30.0, 100.0):
        specs.append({'name': f'ridge_crop_field_l{penalty:g}',
                      'family': 'ridge', 'penalty': penalty})
    return specs


def blocked_year_selection(calibration_rows, specifications=None):
    """Select a correction without looking at the final holdout year."""
    specifications = specifications or candidate_specifications()
    years = sorted({int(row['year']) for row in calibration_rows})
    evaluated = []
    for spec in specifications:
        predictions = []
        for year in years:
            train = [row for row in calibration_rows if int(row['year']) != year]
            held_out = [row for row in calibration_rows if int(row['year']) == year]
            model = fit_model(train, spec)
            predictions.extend({**row, 'prediction': predict(model, row)}
                               for row in held_out)
        evaluated.append({'name': spec['name'], 'specification': spec,
                          **error_metrics(predictions)})
    evaluated.sort(key=lambda row: (row['rmse_t_ha'], row['mae_t_ha'], row['name']))
    selected_spec = evaluated[0]['specification']
    return fit_model(calibration_rows, selected_spec), evaluated
