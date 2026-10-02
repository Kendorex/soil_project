import copy

from src.multifield_model import fertilizer_code, parameters, patch_sowing_decision
from src.multifield_optimizer import optimize


def synthetic_config():
    field = {
        'fertilizer_options': ['a'], 'area_ha': 1,
        'objective_yield_components': ['Wheat'],
        'dose_kg_product_ha': {'min': 0, 'max': 10},
        'application_dates': ['2023-05-01'], 'application_depth_mm': 0,
        'application_cost_per_ha': 2, 'other_cost_per_ha': 10,
        'nutrients': [{'name': 'N', 'expression': 'sum([NO3].kgha)',
                       'unit': 'kg/ha', 'depth_description': 'whole profile',
                       'min': 10, 'max': 20, 'period': 'daily'}],
    }
    option = {'price_per_kg_product': 3, 'components': [{
        'apsim_type': 'NO3N', 'mass_fraction': 1,
        'apsim_amount_per_kg_product': 1,
    }]}
    return {
        'scenario': {'kind': 'diagnostic'}, 'year': 2023,
        'decision_period': {'start': '2022-09-01', 'end': '2023-12-31'},
        'planning_mode': 'historical_fixed_rotation',
        'rotation_transition_policy': 'preserve_source',
        'domain_values_confirmed': True, 'currency': 'TEST',
        'yield_aggregation': 'total_harvest_dry_matter_kg',
        'soil_constraint_mode': 'hard', 'dose_ratios': [],
        'nsga2': {'population': 8, 'generations': 3, 'seed': 42},
        'fertilizer_options': {'a': option},
        'fields': {'Field1': field, 'Field2': copy.deepcopy(field)},
        'crop_economics': {
            'Wheat': {'price_per_kg': 2, 'marketable_kg_per_kg_dm': 1}},
    }


def synthetic_meta():
    rotation = {
        'RotationCrops': 'Wheat,Wheat',
        'RotationCultivars': 'Zlata,Zlata',
    }
    return {
        'simulation': 'Wheat', 'start': '2022-01-01', 'end': '2023-12-31',
        'fields': {
            'Field1': {'area_ha': 1, 'crops': ['Wheat'], 'rotation': rotation},
            'Field2': {'area_ha': 1, 'crops': ['Wheat'],
                       'rotation': copy.deepcopy(rotation)},
        },
    }


def rotation_manager():
    values = {
        'RotationCrops': 'Oats+RedClover,Wheat',
        'RotationCultivars': 'Yakov;Default,Zlata',
        'RotationSowingDepths': '20;10,28',
        'RotationRowSpacings': '150;150,200',
        'RotationPopulations': '250;150,480',
        'RotationSowingDates': '4-May,3-May',
        'RotationHarvestDates': '31-Aug,31-Aug',
    }
    return {'Name': 'Sow using a variable rule',
            'Parameters': [{'Key': key, 'Value': value} for key, value in values.items()]}


def test_patch_sowing_decision_changes_only_selected_component_and_year():
    manager = rotation_manager()
    change = patch_sowing_decision(
        manager, 2022, 2021,
        {'model_crop': 'Wheat', 'model_cultivar': 'Zlata'},
        {'cultivar': 'Irgina', 'sowing_date': '2022-05-11',
         'population_m2': 400.0, 'depth_mm': 40.0, 'row_spacing_mm': 150.0},
    )
    values = parameters(manager)
    assert values['RotationCultivars'] == 'Yakov;Default,Irgina'
    assert values['RotationSowingDates'] == '4-May,11-May'
    assert values['RotationPopulations'] == '250;150,400.0'
    assert values['RotationSowingDepths'] == '20;10,40.0'
    assert values['RotationRowSpacings'] == '150;150,150.0'
    assert change['component_index'] == 0


def test_patch_oat_component_preserves_underseeded_forages():
    manager = rotation_manager()
    patch_sowing_decision(
        manager, 2021, 2021,
        {'model_crop': 'Oats', 'model_cultivar': 'Yakov'},
        {'population_m2': 600.0, 'depth_mm': 40.0},
    )
    values = parameters(manager)
    assert values['RotationPopulations'] == '600.0;150,480'
    assert values['RotationSowingDepths'] == '40.0;10,28'


def test_fertilizer_date_follows_sowing_decision():
    config = synthetic_config()
    config['fields']['Field1']['sowing'] = {
        'model_crop': 'Wheat', 'fertilizer_follows_sowing': True}
    source = '''private void OnSowing(object sender, EventArgs e)\n        {\n        }\n    }'''
    code = fertilizer_code(source, config, 'Field1', {
        'option': 'a', 'dose_kg_product_ha': 5,
        'sowing_date': '2023-05-11',
    })
    assert 'new DateTime(2023, 5, 11)' in code
    assert 'new DateTime(2023, 5, 1)' not in code


def test_nsga2_decodes_sowing_choices(tmp_path):
    config = synthetic_config()
    config['nsga2'] = {'population': 4, 'generations': 1, 'seed': 3}
    config['fields']['Field1']['sowing'] = {
        'model_crop': 'Wheat',
        'cultivar_options': ['Zlata', 'Irgina'],
        'date_options': ['2023-05-03', '2023-05-11'],
        'population_options_m2': [300, 400],
        'depth_options_mm': [20, 40],
        'row_spacing_options_mm': [125, 150],
        'fertilizer_follows_sowing': True,
    }
    seen = []

    def evaluate(decisions):
        seen.append(copy.deepcopy(decisions))
        return {'F': [-1, -1, 0], 'G': [-1] * 5, 'feasible': True}

    optimize(config, synthetic_meta(), tmp_path, evaluate)
    assert seen
    for decisions in seen:
        wheat = decisions['Field1']
        assert wheat['cultivar'] in {'Zlata', 'Irgina'}
        assert wheat['sowing_date'] in {'2023-05-03', '2023-05-11'}
        assert wheat['population_m2'] in {300.0, 400.0}
        assert wheat['depth_mm'] in {20.0, 40.0}
        assert wheat['row_spacing_mm'] in {125.0, 150.0}
