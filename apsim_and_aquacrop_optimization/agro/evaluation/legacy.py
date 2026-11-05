"""Existing multi-field scoring formulas, preserved for compatibility."""
import copy
import math

def score(results, config, decisions):
    total_yield = total_profit = 0.0
    soil_g = []
    for field, row in results['fields'].items():
        fs = config['fields'][field]
        option = config['fertilizer_options'][decisions[field]['option']]
        area = row['area_ha']
        revenue = 0.0
        for crop in fs['objective_yield_components']:
            mass = row['yield_dm_kg_ha'][crop]
            econ = ((fs.get('product_economics') or {}).get(crop) or
                    config['crop_economics'][crop])
            total_yield += mass * area
            revenue += mass * econ['marketable_kg_per_kg_dm'] * econ['price_per_kg']
        dose = decisions[field]['dose_kg_product_ha']
        passes = len(fs['application_dates']) if dose > 0 else 0
        if not math.isclose(row['applied_product_kg_ha'], dose * passes, rel_tol=1e-8, abs_tol=1e-8) or row['application_passes'] != passes:
            raise ValueError(f'{field}: applied fertilizer differs from decision')
        fertilizer_cost = row['applied_product_kg_ha'] * option['price_per_kg_product']
        application_cost = passes * fs['application_cost_per_ha']
        net = revenue - fertilizer_cost - application_cost - fs['other_cost_per_ha']
        row.update(revenue_per_ha=revenue, fertilizer_cost_per_ha=fertilizer_cost,
                   application_cost_per_ha=application_cost, net_profit_per_ha=net)
        total_profit += net * area
        for n in row['nutrients']:
            lower, upper = n['lower'], n['upper']
            if n.get('normalization_scale') is not None:
                scale = n['normalization_scale']
            elif lower is not None and upper is not None:
                scale = upper - lower
            else:
                scale = abs(lower if lower is not None else upper)
            if lower is not None:
                soil_g.append((lower-n['observed_min'])/scale)
            if upper is not None:
                soil_g.append((n['observed_max']-upper)/scale)
    # The third objective is distance outside the allowed intervals, not an invented optimum inside them.
    deviation = sum(max(0.0, v) for v in soil_g)
    # Diagnostic scenarios can explore the soil trade-off directly through F[2].
    # Production scenarios retain the approved intervals as hard constraints.
    g = list(soil_g) if config.get('soil_constraint_mode', 'hard') == 'hard' else []
    for ratio in config.get('dose_ratios', []):
        a = decisions[ratio['numerator_field']]['dose_kg_product_ha']
        b = decisions[ratio['denominator_field']]['dose_kg_product_ha']
        # Ratios require a positive denominator (validated via its lower dose bound).
        g.extend([ratio['min'] - a / b, a / b - ratio['max']])
    return {'yield_dm_kg': total_yield, 'net_profit': total_profit, 'soil_deviation': deviation,
            'F': [-total_yield, -total_profit, deviation], 'G': g + [0.0],
            'feasible': all(v <= 0 for v in g), 'fields': results['fields']}


def aggregate_scenario_scores(named_scores, aggregation=None):
    """Aggregate environment scenarios without inventing scenario probabilities."""
    if not named_scores:
        raise ValueError('At least one environment scenario score is required')
    aggregation = aggregation or {'yield': 'mean', 'profit': 'mean',
                                   'soil': 'worst_case'}

    def aggregate(values, mode):
        return sum(values)/len(values) if mode == 'mean' else min(values)

    yields = [float(row['yield_dm_kg']) for _, row in named_scores]
    profits = [float(row['net_profit']) for _, row in named_scores]
    deviations = [float(row['soil_deviation']) for _, row in named_scores]
    total_yield = aggregate(yields, aggregation['yield'])
    total_profit = aggregate(profits, aggregation['profit'])
    soil_deviation = max(deviations)
    constraints = [float(value) for _, row in named_scores for value in row['G']]
    payload = {
        'yield_dm_kg': total_yield,
        'net_profit': total_profit,
        'soil_deviation': soil_deviation,
        'yield_dm_kg_range': [min(yields), max(yields)],
        'net_profit_range': [min(profits), max(profits)],
        'soil_deviation_range': [min(deviations), max(deviations)],
        'scenario_aggregation': aggregation,
        'scenarios': {name: row for name, row in named_scores},
        'F': [-total_yield, -total_profit, soil_deviation],
        'G': constraints,
        'feasible': all(value <= 0 for value in constraints),
    }
    if len(named_scores) == 1:
        payload['fields'] = copy.deepcopy(named_scores[0][1].get('fields') or {})
    return payload
