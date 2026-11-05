"""APSIM process boundary and conversion to common field/year results."""
from __future__ import annotations

from pathlib import Path
import subprocess

from agro.contracts import Capabilities, FieldYearResult, SimulationResult


def run_apsim(config, model, database_path=None):
    from agro.adapters.apsim_results import finite

    model = Path(model)
    runtime = config['runtime']
    command = runtime.get('command')
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise ValueError('runtime.command must be a nonempty argument list (no shell)')
    timeout = finite(runtime.get('timeout_seconds'), 'runtime.timeout_seconds')
    if timeout <= 0:
        raise ValueError('runtime.timeout_seconds must be positive')
    command = command + [model.name, '--single-threaded']
    database = Path(database_path) if database_path is not None else model.with_suffix('.db')
    # An unsuccessful run must never expose a database from an earlier run.
    if database.exists():
        raise FileExistsError(f'APSIM work directory already contains a database: {database}')
    with (model.parent / 'apsim.log').open('w', encoding='utf-8') as log:
        completed = subprocess.run(command, cwd=model.parent, stdout=log,
                                   stderr=subprocess.STDOUT, timeout=timeout, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f'APSIM exit {completed.returncode}; see {model.parent / "apsim.log"}')
    if not database.is_file():
        raise RuntimeError('APSIM produced no database')
    return database


def normalize_results(annual_results, provenance=None):
    fields = []
    for year, annual in annual_results.items():
        for field_id, row in annual['fields'].items():
            metrics = {key: float(row[key]) for key in
                       ('mineral_n_end_kg_ha', 'urea_n_end_kg_ha') if key in row}
            fields.append(FieldYearResult(field_id, int(year), float(row['area_ha']),
                                          dict(row['yield_dm_kg_ha']), metrics))
    return SimulationResult('apsim', tuple(fields), provenance or {})


class ApsimBackend:
    """The existing multi-field APSIM model behind a common execution interface."""

    capabilities = Capabilities(
        frozenset({'harvested_dm_kg_ha', 'mineral_n_end_kg_ha', 'urea_n_end_kg_ha'}),
        frozenset({'option', 'dose_kg_product_ha', 'cultivar', 'sowing_date',
                   'population_m2', 'depth_mm', 'row_spacing_mm'}))

    def __init__(self, config, years, source_reports='preserve'):
        from copy import deepcopy
        self.config = deepcopy(config)
        self.years = tuple(int(year) for year in years)
        if source_reports not in {'preserve', 'optimizer_only'}:
            raise ValueError('source_reports must be preserve or optimizer_only')
        self.source_reports = source_reports
        if not self.years or len(set(self.years)) != len(self.years):
            raise ValueError('Expected unique evaluation years')

    def simulate(self, decisions, workdir):
        from agro.apsim.multifield_model import inventory, prepare, fingerprint
        from agro.adapters.apsim_results import read_results

        unknown = set(decisions) - set(self.config['fields'])
        if unknown:
            raise ValueError(f'Unknown APSIM fields: {sorted(unknown)}')
        if decisions and set(decisions) != set(self.config['fields']):
            raise ValueError('APSIM management decisions must cover every configured field')
        for field_id, policy in decisions.items():
            self.capabilities.require(decisions=policy)
            if not {'option', 'dose_kg_product_ha'} <= set(policy):
                raise ValueError('APSIM field decisions require option and dose_kg_product_ha')
            spec = self.config['fields'][field_id]
            if policy['option'] not in spec['fertilizer_options']:
                raise ValueError(f'Unsupported fertilizer option for {field_id}')
            from agro.adapters.apsim_results import finite
            dose = finite(policy['dose_kg_product_ha'], 'dose_kg_product_ha')
            if not spec['dose_kg_product_ha']['min'] <= dose <= spec['dose_kg_product_ha']['max']:
                raise ValueError(f'Fertilizer dose outside configured bounds for {field_id}')
        meta = inventory(self.config)
        model = prepare(self.config, workdir, decisions or None)
        removed_reports = []
        if self.source_reports == 'optimizer_only':
            from agro.apsim.multifield_model import read_json, write_json, walk, kind
            data = read_json(model)
            # Old diagnostic reports may use expressions unsupported by the installed
            # APSIM version. Keep only the reports prepared for this optimizer.
            for _, node in list(walk(data)):
                children = node.get('Children', [])
                removed_reports.extend(child['Name'] for child in children
                                       if kind(child) == 'Models.Report' and
                                       not child['Name'].startswith(('OptSoil_', 'OptHarvest_')))
                node['Children'] = [child for child in children if kind(child) != 'Models.Report'
                                    or child['Name'].startswith(('OptSoil_', 'OptHarvest_'))]
            write_json(model, data)
        database = run_apsim(self.config, model)
        results = {year: read_results(database, meta, year, self.config,
                                     audit_applications=bool(decisions)) for year in self.years}
        return normalize_results(results, {
            'model_sha256': fingerprint(self.config['model']),
            'weather_sha256': fingerprint(self.config['weather']),
            'database': str(database), 'command': self.config['runtime']['command'],
            'source_reports': self.source_reports, 'removed_source_reports': removed_reports,
        })
