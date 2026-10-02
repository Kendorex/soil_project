"""Explicit engineering smoke test. Synthetic economics/soil ranges are NOT recommendations.

Run with --run from the project root in an environment containing APSIM.
Uses original model fertilizer types/doses and a tiny mixed NSGA-II population.
Production configuration is never edited.
"""
import argparse
import copy
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.multifield_model import inventory, write_json
from src.multifield_optimizer import ROOT, load_config, optimize
from src.multifield_results import read_results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--baseline-db', type=Path)
    args = parser.parse_args()
    config = load_config(ROOT/'configs/multifield.yaml')
    meta = inventory(config)
    if args.baseline_db:
        for year in range(2019, 2024):
            try:
                result = read_results(args.baseline_db, meta, year)
            except ValueError as exc:
                result = {'status': 'incomplete_harvest', 'error': str(exc)}
            write_json(args.baseline_db.parent/f'baseline_{year}.json', result)
            print(f'Baseline {year}: {result.get("status", "ok")}', flush=True)
    config.update(year=2023, currency='SYNTHETIC_TEST_UNITS', domain_values_confirmed=True,
                  scenario={'kind': 'diagnostic',
                            'name': 'engineering_runtime_smoke_test',
                            'recommendation_status': 'prohibited'},
                  decision_period={'start': '2022-09-01', 'end': '2023-12-31'},
                  planning_mode='historical_fixed_rotation',
                  rotation_transition_policy='harvest_on_winter_sowing_proxy',
                  yield_aggregation='total_harvest_dry_matter_kg', dose_ratios=[],
                  nsga2={'population': 4, 'generations': 1, 'seed': 42})
    for crop in config['crop_economics']:
        config['crop_economics'][crop] = {'price_per_kg': 1, 'marketable_kg_per_kg_dm': 1}
    for field, fm in meta['fields'].items():
        base = fm['baseline_fertilizer']
        # Fixed bounds reproduce values already present in the supplied model.
        dose = float(base['Amount'])
        option = base['FertiliserType']
        config['fertilizer_options'][option] = {'price_per_kg_product': 1, 'components': [
            {'apsim_type': option, 'mass_fraction': 1, 'apsim_amount_per_kg_product': 1}]}
        # Pick an existing scheduled sowing day for this field as a technical application date.
        # Exercise the previous-autumn path for the two 2023 winter-rye fields.
        rotation_dates = [d.strip() for d in fm['rotation']['RotationSowingDates'].split(',')]
        dates = [d for d in rotation_dates if d != '-']
        import datetime as dt
        target_index = config['year'] - int(meta['start'][:4])
        target_cultivar = fm['rotation']['RotationCultivars'].split(',')[target_index]
        application_year = 2022 if 'Winter_rye' in target_cultivar else 2023
        target_date = rotation_dates[target_index] if rotation_dates[target_index] != '-' else dates[0]
        date = dt.datetime.strptime(target_date, '%d-%b').replace(year=application_year).date().isoformat()
        config['fields'][field] = {'fertilizer_options': [option],
            'area_ha': fm['area_ha'],
            'objective_yield_components': fm['rotation']['RotationCrops'].split(',')[
                config['year'] - int(meta['start'][:4])].strip().split('+'),
            'dose_kg_product_ha': {'min': dose, 'max': dose},
            'application_dates': [date], 'application_depth_mm': 0,
            'application_cost_per_ha': 1, 'other_cost_per_ha': 1,
            'nutrients': [{'name': 'N_TEST', 'expression': 'sum([NO3].kgha) + sum([NH4].kgha)',
                          'unit': 'kg/ha', 'depth_description': 'full profile',
                          'min': 0, 'max': 1000000, 'period': 'daily'}]}
    # Vary a category, using only the two fertilizer types actually present in the model.
    config['fields']['Field1']['fertilizer_options'] = list(config['fertilizer_options'])
    out = ROOT/'outputs/multifield'/('ENGINEERING_ONLY_'+uuid.uuid4().hex[:10])
    write_json(out/'config_snapshot.json', config)
    write_json(out/'model_inventory.json', meta)
    result = optimize(config, meta, out)
    if result['failed'] or not result['pareto']:
        raise RuntimeError(f'Engineering smoke failed: {out}')
    print(f'ENGINEERING TEST ONLY: {out}; evaluated={result["evaluated"]}', flush=True)


if __name__ == '__main__':
    main()
