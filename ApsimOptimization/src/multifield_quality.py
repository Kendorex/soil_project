"""Versioned quality checkpoints for the multi-field APSIM model."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from .historical_case import parse_technology_nutrients
from .multifield_model import write_json

# Kept as a public alias for existing analysis scripts and tests.
parse_technology = parse_technology_nutrients


MONTHS = {name: number for number, name in enumerate(
    ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'), 1)}
TRACE_COLUMNS = {
    'Wheat': ('WheatLAI', 'WheatAGWt', 'WheatAGN', 'WheatYield', 'WheatStage', 'WheatZadok'),
    'Oats': ('OatsLAI', 'OatsAGWt', 'OatsAGN', 'OatsYield', 'OatsStage', 'OatsZadok'),
    'RedClover': ('CloverLAI', 'CloverAGWt', 'CloverAGN', 'CloverYield', 'CloverStage', None),
    'PlantainForage': ('PlantainLAI', 'PlantainAGWt', 'PlantainAGN',
                       'PlantainYield', None, None),
    'Potato': ('PotatoLAI', 'PotatoAGWt', 'PotatoAGN', 'PotatoYield', 'PotatoStage', None),
    'Rape': ('RapeLAI', 'RapeAGWt', 'RapeAGN', 'RapeYield', 'RapeStage', None),
    'Mustard': ('MustardLAI', 'MustardAGWt', 'MustardAGN', 'MustardYield', 'MustardStage', None),
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_csv(path, rows, fields=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    if fields is None:
        # Checkpoints are immutable and their schema can grow over time.  Use
        # the ordered union so a newer checkpoint can add plot-ready columns
        # without making older history rows unreadable.
        fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def finite(value):
    return (float(value) if value is not None and not isinstance(value, bool) and
            math.isfinite(float(value)) else None)


def metrics(rows):
    pairs = []
    for row in rows:
        try:
            observed = finite(row.get('observed_yield_t_ha'))
            simulated = finite(row.get('simulated_yield_t_ha'))
        except (TypeError, ValueError):
            continue
        if observed is not None and simulated is not None:
            pairs.append((observed, simulated))
    if not pairs:
        return {'n': 0, 'mae_t_ha': None, 'rmse_t_ha': None, 'bias_t_ha': None,
                'r2_1to1': None, 'pearson_r': None}
    observed = [a for a, _ in pairs]
    simulated = [b for _, b in pairs]
    errors = [b-a for a, b in pairs]
    mean_observed = sum(observed)/len(observed)
    mean_simulated = sum(simulated)/len(simulated)
    sse = sum(value*value for value in errors)
    total = sum((value-mean_observed)**2 for value in observed)
    covariance = sum((a-mean_observed)*(b-mean_simulated)
                     for a, b in pairs)
    spread = math.sqrt(sum((a-mean_observed)**2 for a in observed) *
                       sum((b-mean_simulated)**2 for b in simulated))
    return {
        'n': len(pairs),
        'mae_t_ha': sum(abs(value) for value in errors)/len(errors),
        'rmse_t_ha': math.sqrt(sse/len(errors)),
        'bias_t_ha': sum(errors)/len(errors),
        'r2_1to1': 1-sse/total if total else None,
        'pearson_r': covariance/spread if spread else None,
    }


def split_rotation(value):
    return [item.strip() for item in value.split(',')]


def apsim_date(value, harvest_year, previous_year=False):
    if not value or value.strip() == '-':
        return None
    match = re.fullmatch(r'(\d{1,2})-([A-Za-z]{3})', value.strip())
    if not match or match.group(2).title() not in MONTHS:
        return None
    year = harvest_year-1 if previous_year else harvest_year
    return dt.date(year, MONTHS[match.group(2).title()], int(match.group(1)))


def table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def report_value_on_date(db, table, simulation_id, column, date):
    """Return one daily report value without assuming the APSIM timestamp suffix."""
    if not table_exists(db, table):
        return None
    available = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
    if column not in available or 'Clock.Today' not in available:
        return None
    quoted = '"'+column.replace('"', '""')+'"'
    row = db.execute(
        f'SELECT {quoted} FROM "{table}" WHERE SimulationID=? '
        'AND substr("Clock.Today",1,10)=? ORDER BY "Clock.Today" LIMIT 1',
        (simulation_id, date.isoformat())).fetchone()
    return finite(row[0]) if row is not None else None


def evidence_tier(flags):
    """Classify comparison evidence while retaining every case in overall metrics."""
    flags = set(flags)
    if 'observed_sowing_before_model_clock' in flags:
        return 'boundary_limited'
    if 'unsupported_observed_cultivar_proxy' in flags:
        return 'cultivar_proxy'
    if 'unresolved_hydrology_case' in flags:
        return 'input_sensitive'
    if {'forage_mass_basis_unconfirmed', 'timothy_represented_by_plantain'} & flags:
        return 'structural_proxy'
    if 'zero_surface_paw_at_model_sowing' in flags:
        return 'input_sensitive'
    return 'standard'


def rows_for_year(db, table, simulation_id, year):
    if not table_exists(db, table):
        return []
    return db.execute(
        f'SELECT Date, YieldDM FROM "{table}" WHERE SimulationID=? '
        'AND substr(Date,1,4)=? ORDER BY Date', (simulation_id, str(year))).fetchall()


def value_at(rows, column, index):
    value = rows[index][column]
    return finite(value)


def component_trace(db, simulation_id, report_table, component, start, end, harvest_rows):
    columns = TRACE_COLUMNS[component]
    available = {row[1] for row in db.execute(f'PRAGMA table_info("{report_table}")')}
    selected = ['Clock.Today', 'WaterStored', 'MineralN'] + [c for c in columns if c]
    selected = [c for c in selected if c in available]
    quoted = ', '.join('"'+column.replace('"', '""')+'"' for column in selected)
    rows = db.execute(
        f'SELECT {quoted} FROM "{report_table}" WHERE SimulationID=? '
        'AND substr("Clock.Today",1,10)>=? AND substr("Clock.Today",1,10)<=? '
        'ORDER BY "Clock.Today"', (simulation_id, start.isoformat(), end.isoformat())).fetchall()
    lai, biomass, crop_n, yield_column, stage, zadok = columns
    active = [row for row in rows if (finite(row[biomass]) or 0) > 0]

    def maximum(column):
        candidates = [(finite(row[column]), str(row['Clock.Today'])[:10]) for row in rows]
        candidates = [(value, date) for value, date in candidates if value is not None]
        return max(candidates, default=(None, None))

    def first_zadok(threshold):
        if not zadok:
            return None
        for row in rows:
            value = finite(row[zadok])
            if value is not None and value >= threshold:
                return str(row['Clock.Today'])[:10]
        return None

    max_lai, max_lai_date = maximum(lai)
    max_biomass, max_biomass_date = maximum(biomass)
    max_crop_n, max_crop_n_date = maximum(crop_n)
    max_report_yield, max_report_yield_date = maximum(yield_column)
    last = active[-1] if active else (rows[-1] if rows else None)
    first = active[0] if active else None
    harvest_dates = [str(row['Date'])[:10] for row in harvest_rows]
    return {
        'component': component,
        'cycle_start': start.isoformat(),
        'cycle_end': end.isoformat(),
        'first_active_date': str(first['Clock.Today'])[:10] if first else None,
        'harvest_dates': ';'.join(harvest_dates),
        'max_lai': max_lai,
        'max_lai_date': max_lai_date,
        'max_aboveground_biomass_g_m2': max_biomass,
        'max_aboveground_biomass_date': max_biomass_date,
        'max_crop_n_g_m2': max_crop_n,
        'max_crop_n_date': max_crop_n_date,
        'max_daily_report_yield_kg_ha': max_report_yield,
        'max_daily_report_yield_date': max_report_yield_date,
        'last_active_stage': last[stage] if last is not None and stage else None,
        'last_active_zadok': finite(last[zadok]) if last is not None and zadok else None,
        'simulated_tillering_date_zadok21': first_zadok(21),
        'simulated_heading_date_zadok50': first_zadok(50),
        'simulated_maturity_date_zadok90': first_zadok(90),
        'water_stored_at_first_active_mm': finite(first['WaterStored']) if first is not None and 'WaterStored' in available else None,
        'water_stored_at_last_active_mm': finite(last['WaterStored']) if last is not None and 'WaterStored' in available else None,
        'mineral_n_at_first_active_kg_ha': finite(first['MineralN']) if first is not None and 'MineralN' in available else None,
        'mineral_n_at_last_active_kg_ha': finite(last['MineralN']) if last is not None and 'MineralN' in available else None,
    }


def build_checkpoint(baseline_dir, observations, label, notes=''):
    baseline_dir = Path(baseline_dir).resolve()
    db_path = baseline_dir/'candidate.db'
    inventory_path = baseline_dir/'model_inventory.json'
    config_path = baseline_dir/'config_snapshot.json'
    for path in (db_path, inventory_path, config_path):
        if not path.is_file():
            raise ValueError(f'Missing baseline artifact: {path}')
    inventory = json.loads(inventory_path.read_text(encoding='utf-8-sig'))
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    preparation_path = baseline_dir/'preparation.json'
    preparation = (json.loads(preparation_path.read_text(encoding='utf-8-sig'))
                   if preparation_path.is_file() else {})
    replay_entries = {
        (int(row['harvest_year']), row['field']): row
        for row in ((preparation.get('historical_replay') or {}).get('entries') or [])
    }
    clock_start = dt.date.fromisoformat(inventory['start'][:10])
    start_year = clock_start.year
    field_rows, trace_rows = [], []
    with sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        simulations = db.execute('SELECT ID, Name FROM _Simulations WHERE Name=?',
                                 (inventory['simulation'],)).fetchall()
        if len(simulations) != 1:
            raise ValueError('Missing or ambiguous simulation in baseline database')
        simulation_id = simulations[0]['ID']
        for year_text, records in sorted((observations.get('years') or {}).items()):
            year = int(year_text)
            for record in records:
                field = record['field']
                fm = inventory['fields'][field]
                rotation = fm['rotation']
                index = year-start_year
                arrays = {key: split_rotation(rotation[key]) for key in (
                    'RotationCrops', 'RotationCultivars', 'RotationSowingDepths',
                    'RotationRowSpacings', 'RotationPopulations',
                    'RotationSowingDates', 'RotationHarvestDates')}
                if not all(0 <= index < len(value) for value in arrays.values()):
                    raise ValueError(f'{field}/{year}: rotation index unavailable')
                crop = arrays['RotationCrops'][index]
                cultivar = arrays['RotationCultivars'][index]
                components = record['yield_components']
                harvest_by_component = {
                    component: rows_for_year(db, f'OptHarvest_{field}_{component}',
                                             simulation_id, year)
                    for component in components}
                complete = all(harvest_by_component[component] for component in components)
                simulated = (sum(float(row['YieldDM']) for rows in harvest_by_component.values()
                                 for row in rows)/1000.0 if complete else None)
                observed = float(record['observed_yield_t_ha'])
                error = simulated-observed if simulated is not None else None
                sow_text = arrays['RotationSowingDates'][index]
                harvest_text = arrays['RotationHarvestDates'][index]
                sow_probe = apsim_date(sow_text, year)
                harvest_probe = apsim_date(harvest_text, year)
                previous_year = bool(sow_probe and harvest_probe and sow_probe > harvest_probe)
                model_sow = apsim_date(sow_text, year, previous_year)
                model_harvest = harvest_probe
                nutrient_labels = parse_technology_nutrients(record.get('technology'))
                fert = fm.get('baseline_fertilizer') or {}
                original_model_applied = float(fert['Amount']) if model_sow is not None else 0.0
                replay_entry = replay_entries.get((year, field))
                model_applied = (float(replay_entry['amount_kg_n_ha'])
                                 if replay_entry is not None else original_model_applied)
                flags = list(record.get('evidence_flags') or [])
                if record.get('model_crop') != crop or record.get('model_cultivar') != cultivar:
                    flags.append('observation_model_mapping_mismatch')
                if not complete:
                    flags.append('missing_harvest_event')
                if record.get('observed_harvest_date') is None:
                    flags.append('observed_harvest_date_missing')
                if record['crop_group'] == 'Клевер + тимофеевка':
                    flags.append('forage_mass_basis_unconfirmed')
                    flags.append('timothy_represented_by_plantain')
                if nutrient_labels['N'] is not None and not math.isclose(
                        nutrient_labels['N'], model_applied, abs_tol=1e-9):
                    flags.append('observed_n_label_differs_from_model_sowing_amount')
                if replay_entry is not None:
                    flags.append('historical_n_label_replayed_with_model_product_and_sowing_event')
                if (nutrient_labels['P'] or 0) > 0 or (nutrient_labels['K'] or 0) > 0:
                    flags.append('observed_pk_not_simulated')
                observed_sow = (dt.date.fromisoformat(record['observed_sow_date'])
                                 if record.get('observed_sow_date') else None)
                if observed_sow is not None and observed_sow < clock_start:
                    flags.append('observed_sowing_before_model_clock')
                report_table = f'Report{int(field[5:])}'
                surface_paw = (report_value_on_date(
                    db, report_table, simulation_id, 'PAW(1)', model_sow)
                    if model_sow is not None and model_sow >= clock_start else None)
                if surface_paw is not None and surface_paw <= 1e-12:
                    flags.append('zero_surface_paw_at_model_sowing')
                field_row = {
                    'year': year, 'field': field, 'crop_group': record['crop_group'],
                    'observed_crop': record['crop'], 'observed_cultivar': record['cultivar'],
                    'observed_technology': record['technology'],
                    'observed_yield_t_ha': observed,
                    'simulated_yield_t_ha': simulated,
                    'error_t_ha': error,
                    'absolute_error_t_ha': abs(error) if error is not None else None,
                    'relative_error_percent': 100*error/observed if error is not None and observed else None,
                    'model_crop': crop, 'model_cultivar': cultivar,
                    'yield_components': '+'.join(components),
                    'harvest_event_count': sum(len(rows) for rows in harvest_by_component.values()),
                    'simulated_harvest_dates': ';'.join(sorted({str(row['Date'])[:10]
                        for rows in harvest_by_component.values() for row in rows})),
                    'observed_sow_date': record.get('observed_sow_date'),
                    'model_sow_date': model_sow.isoformat() if model_sow else None,
                    'observed_tillering_date': record.get('observed_tillering_date'),
                    'simulated_tillering_date': None,
                    'tillering_error_days': None,
                    'observed_heading_date': record.get('observed_heading_date'),
                    'simulated_heading_date': None,
                    'heading_error_days': None,
                    'simulated_maturity_date': None,
                    'observed_harvest_date': record.get('observed_harvest_date'),
                    'model_harvest_rule_date': model_harvest.isoformat() if model_harvest else None,
                    'model_population_m2': arrays['RotationPopulations'][index],
                    'model_row_spacing_mm': arrays['RotationRowSpacings'][index],
                    'model_sowing_depth_mm': arrays['RotationSowingDepths'][index],
                    'observed_n_label_kg_ha': nutrient_labels['N'],
                    'observed_p_label_kg_ha': nutrient_labels['P'],
                    'observed_k_label_kg_ha': nutrient_labels['K'],
                    'model_fertilizer_type': fert.get('FertiliserType'),
                    'model_fertilizer_at_sowing_kg_ha': model_applied,
                    'original_static_model_fertilizer_kg_ha': original_model_applied,
                    'model_fertilizer_amount_source': ('historical_n_label_replay'
                        if replay_entry is not None else 'original_static_manager'),
                    'surface_paw_at_model_sowing': surface_paw,
                    'evidence_tier': evidence_tier(flags),
                    'diagnostic_flags': ';'.join(flags),
                }
                field_rows.append(field_row)
                if table_exists(db, report_table):
                    cycle_start = (dt.date.fromisoformat(record['observed_sow_date'])
                                   if record.get('observed_sow_date') else
                                   model_sow or dt.date(year, 1, 1))
                    event_dates = [dt.date.fromisoformat(str(row['Date'])[:10])
                                   for rows in harvest_by_component.values() for row in rows]
                    cycle_end = max(event_dates) if event_dates else model_harvest or dt.date(year, 12, 31)
                    for component in components:
                        trace = component_trace(db, simulation_id, report_table, component,
                                                cycle_start, cycle_end,
                                                harvest_by_component[component])
                        trace_rows.append({'year': year, 'field': field,
                                           'crop_group': record['crop_group'], **trace})
                        if component == components[0]:
                            field_row['simulated_tillering_date'] = trace['simulated_tillering_date_zadok21']
                            field_row['simulated_heading_date'] = trace['simulated_heading_date_zadok50']
                            field_row['simulated_maturity_date'] = trace['simulated_maturity_date_zadok90']
                            for observed_key, simulated_key, error_key in (
                                    ('observed_tillering_date', 'simulated_tillering_date', 'tillering_error_days'),
                                    ('observed_heading_date', 'simulated_heading_date', 'heading_error_days')):
                                if field_row[observed_key] and field_row[simulated_key]:
                                    field_row[error_key] = (
                                        dt.date.fromisoformat(field_row[simulated_key]) -
                                        dt.date.fromisoformat(field_row[observed_key])).days
    return config, inventory, field_rows, trace_rows


def aggregate_summary(field_rows):
    comparable = [row for row in field_rows if row['simulated_yield_t_ha'] is not None]
    by_year, by_crop, by_field, by_evidence_tier = {}, {}, {}, {}
    for name, key, target in (
            ('year', lambda row: str(row['year']), by_year),
            ('crop', lambda row: row['crop_group'], by_crop),
            ('field', lambda row: row['field'], by_field),
            ('evidence_tier', lambda row: row.get('evidence_tier', 'unclassified'),
             by_evidence_tier)):
        grouped = defaultdict(list)
        for row in field_rows:
            grouped[key(row)].append(row)
        target.update({group: metrics(rows) for group, rows in sorted(grouped.items())})
    return {
        'overall': metrics(field_rows),
        'year_2023': metrics([row for row in field_rows if row['year'] == 2023]),
        'by_year': by_year,
        'by_crop': by_crop,
        'by_field': by_field,
        'by_evidence_tier': by_evidence_tier,
        'observations': len(field_rows),
        'comparable': len(comparable),
        'coverage_percent': 100*len(comparable)/len(field_rows) if field_rows else None,
        'missing_harvest_cases': [f'{row["year"]}/{row["field"]}' for row in field_rows
                                  if row['simulated_yield_t_ha'] is None],
        'management_mismatch_cases': [f'{row["year"]}/{row["field"]}' for row in field_rows
            if ('observed_n_label_differs_from_model_sowing_amount' in row['diagnostic_flags'] or
                'observed_pk_not_simulated' in row['diagnostic_flags'])],
    }


def rebuild_history(root):
    root = Path(root)
    index_rows, overall_rows, field_history, crop_history, evidence_history = [], [], [], [], []
    checkpoints = []
    for directory in sorted((root/'checkpoints').glob('*')):
        manifest_path, summary_path, fields_path = (directory/'manifest.json',
                                                     directory/'summary.json',
                                                     directory/'field_metrics.csv')
        if not all(path.is_file() for path in (manifest_path, summary_path, fields_path)):
            continue
        manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
        summary = json.loads(summary_path.read_text(encoding='utf-8-sig'))
        common = {key: manifest.get(key) for key in (
            'checkpoint_id', 'created_utc', 'label', 'model_sha256',
            'prepared_model_sha256', 'weather_sha256',
            'rotation_transition_policy', 'baseline_dir', 'notes')}
        coverage = (100*summary['comparable']/summary['observations']
                    if summary.get('observations') else None)
        index_rows.append({**common, 'overall_rmse_t_ha': summary['overall']['rmse_t_ha'],
                           'year_2023_rmse_t_ha': summary['year_2023']['rmse_t_ha'],
                           'comparable': summary['comparable'],
                           'observations': summary.get('observations'),
                           'coverage_percent': coverage})
        for scope in ('overall', 'year_2023'):
            overall_rows.append({**common, 'scope': scope, **summary[scope]})
        with fields_path.open(encoding='utf-8-sig', newline='') as stream:
            checkpoint_fields = list(csv.DictReader(stream))
            field_history.extend({**common, **row} for row in checkpoint_fields)
            checkpoints.append((common, checkpoint_fields))
        for crop, values in summary['by_crop'].items():
            crop_history.append({**common, 'crop_group': crop, **values})
        for tier, values in summary.get('by_evidence_tier', {}).items():
            evidence_history.append({**common, 'evidence_tier': tier, **values})
    write_csv(root/'index.csv', index_rows)
    write_csv(root/'overall_history.csv', overall_rows)
    write_csv(root/'field_history.csv', field_history)
    write_csv(root/'crop_history.csv', crop_history)
    write_csv(root/'evidence_history.csv', evidence_history)
    paired = []
    if checkpoints:
        reference_common, reference_rows = checkpoints[0]
        reference_by_key = {(row['year'], row['field']): row for row in reference_rows}
        for common, current_rows in checkpoints:
            current_by_key = {(row['year'], row['field']): row for row in current_rows}
            keys = [key for key in reference_by_key.keys() & current_by_key.keys()
                    if metrics([reference_by_key[key]])['n'] and
                    metrics([current_by_key[key]])['n']]
            reference_metric = metrics([reference_by_key[key] for key in keys])
            current_metric = metrics([current_by_key[key] for key in keys])
            paired.append({
                **common,
                'reference_checkpoint_id': reference_common['checkpoint_id'],
                'n_common': len(keys),
                'reference_rmse_t_ha': reference_metric['rmse_t_ha'],
                'current_rmse_t_ha': current_metric['rmse_t_ha'],
                'rmse_change_t_ha': (current_metric['rmse_t_ha']-
                                     reference_metric['rmse_t_ha']),
                'reference_mae_t_ha': reference_metric['mae_t_ha'],
                'current_mae_t_ha': current_metric['mae_t_ha'],
                'mae_change_t_ha': (current_metric['mae_t_ha']-
                                    reference_metric['mae_t_ha']),
            })
    write_csv(root/'paired_history.csv', paired)


def record_checkpoint(baseline_dir, observations_path, root, label, notes=''):
    baseline_dir = Path(baseline_dir).resolve()
    observations_path = Path(observations_path).resolve()
    observations = json.loads(observations_path.read_text(encoding='utf-8-sig'))
    config, inventory, field_rows, trace_rows = build_checkpoint(
        baseline_dir, observations, label, notes)
    preparation_path = baseline_dir/'preparation.json'
    preparation = (json.loads(preparation_path.read_text(encoding='utf-8-sig'))
                   if preparation_path.is_file() else {})
    summary = aggregate_summary(field_rows)
    created = dt.datetime.now(dt.timezone.utc)
    safe_label = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_') or 'checkpoint'
    checkpoint_id = f'{created:%Y%m%dT%H%M%SZ}_{safe_label}_{inventory["model_sha256"][:8]}'
    directory = Path(root)/'checkpoints'/checkpoint_id
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        'checkpoint_id': checkpoint_id,
        'created_utc': created.isoformat(),
        'label': label,
        'notes': notes,
        'baseline_dir': str(baseline_dir),
        'baseline_database_sha256': sha256(Path(baseline_dir)/'candidate.db'),
        'model_sha256': inventory['model_sha256'],
        'prepared_model_sha256': sha256(Path(baseline_dir)/'candidate.apsimx'),
        'weather_sha256': inventory['weather_sha256'],
        'rotation_transition_policy': config.get('rotation_transition_policy',
                                                 'preserve_source'),
        'historical_replay': (preparation.get('historical_replay') or {}).get('enabled', False),
        'observations_path': str(observations_path),
        'observations_sha256': sha256(observations_path),
        'config_schema_version': config.get('schema_version'),
    }
    write_json(directory/'manifest.json', manifest)
    write_json(directory/'summary.json', summary)
    write_csv(directory/'field_metrics.csv', field_rows)
    write_csv(directory/'crop_trace.csv', trace_rows)
    write_csv(directory/'management_mismatches.csv', [row for row in field_rows
        if ('observed_n_label_differs_from_model_sowing_amount' in row['diagnostic_flags'] or
            'observed_pk_not_simulated' in row['diagnostic_flags'])], fields=list(field_rows[0]))
    rebuild_history(root)
    return directory, summary
