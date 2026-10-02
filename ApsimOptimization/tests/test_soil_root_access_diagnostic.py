from scripts.diagnose_soil_root_access import patch_soil
from src.multifield_model import write_json


def model():
    physical = {
        '$type': 'Models.Soils.Physical, Models', 'Name': 'Physical',
        'AirDry': [0.1, 0.2], 'DUL': [0.4, 0.5],
        'Children': [
            {'$type': 'Models.Soils.SoilCrop, Models', 'Name': name,
             'LL': [0.3, 0.4], 'Children': []}
            for name in ('WheatSoil', 'OatsSoil')],
    }
    soil = {'$type': 'Models.Soils.Soil, Models', 'Name': 'Soil', 'Children': [
        physical,
        {'$type': 'Models.Soils.Water, Models', 'Name': 'Water',
         'InitialValues': [0.25, 0.3], 'Children': []},
    ]}
    manager = {'$type': 'Models.Manager, Models', 'Name': 'Sow using a variable rule',
               'Parameters': [
                   {'Key': 'RotationCrops', 'Value': 'Oats+RedClover+PlantainForage'},
                   {'Key': 'RotationCultivars', 'Value': 'Yakov;Clover;Timothy'},
                   {'Key': 'RotationSowingDepths', 'Value': '20;10;10'},
                   {'Key': 'RotationRowSpacings', 'Value': '150;150;150'},
                   {'Key': 'RotationPopulations', 'Value': '250;150;150'},
               ], 'Children': []}
    zone = {'$type': 'Models.Core.Zone, Models', 'Name': 'Field3',
            'Enabled': True, 'Children': [soil, manager]}
    simulation = {'$type': 'Models.Core.Simulation, Models', 'Name': 'Simulation',
                  'Enabled': True, 'Children': [zone]}
    return {'Name': 'Simulations', 'Children': [simulation]}


def test_patch_soil_uses_model_endpoints(tmp_path):
    path = tmp_path/'model.apsimx'
    write_json(path, model())
    audit = patch_soil(path, 'crop_ll_airdry_and_initial_dul')
    assert len(audit) == 6
    assert {row['source_endpoint'] for row in audit} == {
        'Physical.AirDry', 'Physical.DUL'}


def test_patch_soil_can_isolate_first_oats_component(tmp_path):
    path = tmp_path/'model.apsimx'
    write_json(path, model())
    audit = patch_soil(path, 'unmodified_oats_only')
    assert len(audit) == 5
    assert all(row['source_endpoint'] == 'first mixture component' for row in audit)
