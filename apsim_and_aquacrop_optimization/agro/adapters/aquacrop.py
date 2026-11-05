"""Execute an AquaCrop Python model in an isolated candidate workspace."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import subprocess
import sys

from agro.contracts import Capabilities, FieldYearResult, SimulationResult


def normalize_results(model_root, field_ids, years):
    """Read native harvest events; standing biomass is never treated as harvest."""
    fields = []
    root = Path(model_root)
    for field_id in field_ids:
        mapping = json.loads((root / 'generated' / field_id / 'source_mapping.json').read_text(encoding='utf-8'))
        segments = {row['id']: row for row in mapping['segments']}
        mass = {int(year): {} for year in years}
        seen = set()
        with (root / 'outputs' / field_id / 'harvests.csv').open(encoding='utf-8', newline='') as stream:
            for event in csv.DictReader(stream):
                year = int(event['date'][:4])
                if year not in mass:
                    continue
                identity = (event['date'], event['segment'], event['kind'])
                if identity in seen:
                    raise ValueError(f'Duplicate AquaCrop harvest: {identity}')
                seen.add(identity)
                segment = segments[event['segment']]
                crop = segment.get('source_yield_column', segment['catalog_crop']).removesuffix('Yield')
                quantity = float(event['dry_mass_t_ha']) * 1000.0
                if quantity < 0:
                    raise ValueError('Negative AquaCrop harvest')
                mass[year][crop] = mass[year].get(crop, 0.0) + quantity
        for year, yields in mass.items():
            # A year with no scheduled harvest is valid. A missing scheduled event is not.
            expected = {day for segment in segments.values()
                        for day in segment.get('harvest_dates', []) if int(day[:4]) == year}
            observed = {event[0] for event in seen if int(event[0][:4]) == year}
            if expected - observed:
                raise ValueError(f'{field_id}/{year}: missing scheduled harvests: {sorted(expected-observed)}')
            fields.append(FieldYearResult(field_id, year, float(mapping['area_ha']), yields))
    return SimulationResult('aquacrop', tuple(fields), {
        'workspace': str(root.resolve()), 'harvest_basis': 'native dry yield at cuts or grain crop end',
        'crop_representation': 'effective crop; species competition is not simulated',
    })


class AquaCropBackend:
    """A model consists of its input files and the Python preparation entry point.

    Candidate-specific changes use named AquaCrop parameters, explicitly distinct
    from APSIM fertilizer forms and nitrogen doses.
    """

    capabilities = Capabilities(frozenset({'harvested_dm_kg_ha'}),
                                frozenset({'crop_parameters'}))

    def __init__(self, model_root, command, years, field_ids=None, timeout_seconds=600,
                 rotation_populations=None):
        self.model_root = Path(model_root).resolve()
        self.command = list(command)
        self.years = tuple(int(year) for year in years)
        self.field_ids = tuple(field_ids) if field_ids is not None else None
        self.timeout_seconds = float(timeout_seconds)
        self.rotation_populations = rotation_populations or {}
        for policy in self.rotation_populations.values():
            if set(policy) != {'population_m2'}:
                raise ValueError('Native AquaCrop rotation overrides support only population_m2')
        import math
        if not self.command or not all(isinstance(part, str) for part in self.command):
            raise ValueError('Expected an engine command argument list')
        if not self.years or len(set(self.years)) != len(self.years):
            raise ValueError('Expected unique evaluation years')
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError('Expected a positive finite timeout')

    def simulate(self, decisions, workdir):
        for policy in decisions.values():
            self.capabilities.require(decisions=policy)
        workdir = Path(workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=False)
        request = {'model_root': str(self.model_root), 'workspace': str(workdir),
                   'command': self.command, 'years': self.years,
                   'field_ids': self.field_ids, 'decisions': decisions}
        request['rotation_populations'] = self.rotation_populations
        request_path = workdir / 'request.json'
        request_path.write_text(json.dumps(request, allow_nan=False, indent=2), encoding='utf-8')
        with (workdir / 'model.log').open('w', encoding='utf-8') as log:
            try:
                subprocess.run([sys.executable, '-m', 'agro.aquacrop.runner', str(request_path)],
                               cwd=Path(__file__).resolve().parents[2], stdout=log, stderr=subprocess.STDOUT,
                               timeout=self.timeout_seconds, check=True)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError(f'AquaCrop model failed; see {workdir / "model.log"}') from exc
        payload = json.loads((workdir / 'result.json').read_text(encoding='utf-8'))
        return SimulationResult('aquacrop', tuple(FieldYearResult(**row) for row in payload['fields']),
                                payload['provenance'])
