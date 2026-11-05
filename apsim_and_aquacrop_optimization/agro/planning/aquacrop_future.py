"""Native AquaCrop harvests and explicitly separate APSIM soil indicators."""
from copy import deepcopy
from dataclasses import asdict
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys

from agro.adapters.aquacrop import AquaCropBackend
from agro.adapters.rotation_management import walk, manager
from agro.contracts import FieldYearResult, SimulationResult
from agro.planning.future_model import ROTATION_KEYS, fingerprint, source_parts

ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def prepare_model(document, weather, settings, folder, bridge_year, initial_water=None):
    """Create a model from the new candidate, never from the old rotation/events."""
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    template = Path(settings['model_root'])
    shutil.copytree(template / 'DATA', folder / 'DATA')
    (folder / 'management').mkdir()
    (folder / 'configs').mkdir()
    shutil.copyfile(weather, folder / 'configs/weather.met')
    aqua_document = deepcopy(document)
    clock, zones = source_parts(aqua_document)
    skip = bridge_year - int(clock['Start'][:4])
    if skip <= 0 or bridge_year > int(clock['End'][:4]):
        raise ValueError('AquaCrop bridge must follow calibrated history and precede evaluated years')
    clock['Start'] = f'{bridge_year}-01-01T00:00:00'
    project = json.loads((template / 'project.json').read_text(encoding='utf-8-sig'))
    fields = [json.loads((template / name).read_text(encoding='utf-8-sig')) for name in project['fields']]
    if (len(fields) != len(zones) or len({field['id'] for field in fields}) != len(fields)
            or {field['source_zone'] for field in fields} != set(zones)):
        raise ValueError('AquaCrop template fields must match the new calibrated fields')
    paths, mapping = [], {}
    for field in fields:
        zone = zones[field['source_zone']]
        for name, keys in (('RotationManager', set(ROTATION_KEYS)),
                           ('Fertilise at sowing', {'FertiliserTypes', 'FertiliserAmounts'})):
            for parameter in manager(zone, name)['Parameters']:
                if parameter['Key'] in keys:
                    parameter['Value'] = ', '.join(v.strip() for v in parameter['Value'].split(',')[skip:])
        if initial_water is not None:
            water = next(n for n in walk(zone) if n.get('$type') == 'Models.Soils.Water, Models')
            values = initial_water[field['source_zone']]
            if len(values) != len(water['Thickness']) or any(not math.isfinite(v) or v < 0 or v > 1 for v in values):
                raise ValueError('Invalid APSIM-derived initial volumetric soil water')
            water['InitialValues'] = values
        field.pop('source_project', None)
        field.update(management_mode='planned_rotation', weather_file='configs/weather.met')
        field['notes'] = ['New calibrated RotationManager; explicit future crop-map candidate.',
                          'Uncalibrated AquaCrop effective crop profiles; no species competition or native Humic/N balance.']
        path = f"fields/{field['id']}.json"
        save(folder / path, field)
        paths.append(path)
        mapping[field['id']] = field['source_zone']
    save(folder / 'project.json', {'schema_version': 1, 'source_project': 'configs/rotation_new_calibr.apsimx', 'fields': paths})
    save(folder / 'configs/rotation_new_calibr.apsimx', aqua_document)
    manifest = {'engine': 'aquacrop_with_apsim_soil', 'field_mapping': mapping,
                'period': [clock['Start'][:10], clock['End'][:10]],
                'source_candidate_sha256': hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest(),
                'weather_sha256': fingerprint(weather),
                'crop_catalog_sha256': fingerprint(folder / 'DATA/crops/transferred/catalog.json'),
                'initial_water_ready': initial_water is not None,
                'initial_water_source': f'APSIM {(dt.date(bridge_year, 1, 1)-dt.timedelta(days=1)).isoformat()}' if initial_water is not None else 'Pending APSIM historical end state; preparation uses placeholder initial values, not a runnable evaluated candidate.',
                'harvest_source': 'native AquaCrop; no additive APSIM yield correction',
                'soil_metrics_source': 'APSIM on the identical crop map and weather',
                'limitation': 'One-way soil indicators; AquaCrop biomass is not fed back to APSIM carbon/nitrogen. Existing perennial biomass/reserves are approximated by effective profiles.'}
    save(folder / 'new_calibr_import.json', manifest)
    return manifest


def prepare_inputs(model, settings, work, years):
    """Validate all CRO/MAN/weather/soil files without running either engine."""
    work.mkdir(parents=True, exist_ok=False)
    request = {'model_root': str(model), 'workspace': str(work), 'command': settings['command'],
               'years': years, 'field_ids': None, 'decisions': {}, 'prepare_only': True}
    save(work / 'request.json', request)
    with (work / 'model.log').open('w', encoding='utf-8') as log:
        try:
            subprocess.run([sys.executable, '-m', 'agro.aquacrop.runner', str(work / 'request.json')],
                           cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True,
                           timeout=settings.get('timeout_seconds', 1800))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f'AquaCrop preparation failed; see {work / "model.log"}') from exc


def soil_water(database, document, bridge_year):
    when = f'{bridge_year - 1}-12-31'
    _, zones = source_parts(document)
    result = {}
    with sqlite3.connect(Path(database).as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for field, zone in zones.items():
            if not re.fullmatch(r'Field\d+', field):
                raise ValueError('Unsafe field name')
            row = connection.execute(f'SELECT * FROM OptSoil_{field} WHERE substr(Date,1,10)=?', (when,)).fetchone()
            if row is None:
                raise ValueError(f'Missing APSIM initial water: {field}/{when}')
            columns = sorted([key for key in row.keys() if re.fullmatch(r'SoilWater\(\d+\)', key)],
                             key=lambda key: int(key.partition('(')[2][:-1]))
            physical = next(n for n in walk(zone) if n.get('$type') == 'Models.Soils.Physical, Models')
            if columns != [f'SoilWater({i+1})' for i in range(len(physical['Thickness']))]:
                raise ValueError('APSIM soil-water report does not match physical layers')
            result[field] = [float(row[key]) for key in columns]
    return result


def combine(native_aqua, apsim, mapping):
    soil = {(row.field_id, row.year): row for row in apsim.fields}
    remapped = {(mapping[row.field_id], row.year): row for row in native_aqua.fields}
    if len(remapped) != len(native_aqua.fields) or set(remapped) != set(soil):
        raise ValueError('AquaCrop/APSIM field-year coverage differs')
    rows = []
    for key, row in remapped.items():
        ref = soil[key]
        if row.area_ha != ref.area_ha:
            raise ValueError('AquaCrop/APSIM field area differs')
        # Keep AquaCrop product identity and actual native harvest quantities.
        rows.append(FieldYearResult(key[0], key[1], row.area_ha, dict(row.harvested_dm_kg_ha), dict(ref.metrics)))
    return SimulationResult('aquacrop_with_apsim_soil', tuple(rows), {
        'harvest_source': 'native AquaCrop', 'soil_metrics_source': 'APSIM',
        'aquacrop': dict(native_aqua.provenance), 'apsim': dict(apsim.provenance),
        'limitation': 'No AquaCrop biomass feedback to APSIM; no fertilizer/depth yield-response transfer.'})


def simulate_map(document, runtime, work, settings, bridge_year, apsim_simulate):
    config = deepcopy(runtime)
    config['report_soil_water'] = True
    apsim, audit = apsim_simulate(document, config, None, work / 'apsim')
    water = soil_water(apsim.provenance['database'], document, bridge_year)
    save(work / 'initial_water.json', water)
    model = work / 'aquacrop_model'
    manifest = prepare_model(document, Path(runtime['model_root']) / 'weather.met', settings, model, bridge_year, water)
    backend = AquaCropBackend(model, settings['command'], runtime['evaluation_years'],
                             timeout_seconds=settings.get('timeout_seconds', 1800))
    aqua = backend.simulate({}, work / 'aquacrop')
    combined = combine(aqua, apsim, manifest['field_mapping'])
    audit['metric_sources'] = {'harvest': 'native AquaCrop', 'mineral_n': 'APSIM', 'humic': 'APSIM', 'fertilizer': 'APSIM application events'}
    save(work / 'simulation.json', asdict(combined))
    save(work / 'audit.json', audit)
    shutil.copyfile(work / 'apsim/candidate.apsimx', work / 'candidate.apsimx')
    save(work / 'aquacrop_manifest.json', manifest)
    return combined, audit
