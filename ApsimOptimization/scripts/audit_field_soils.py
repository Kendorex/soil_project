#!/usr/bin/env python3
"""Write a traceable comparison of soil inputs across APSIM fields."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.field_soil_audit import differences, digest
from src.multifield_model import child, fingerprint, load_model, write_json
from src.multifield_optimizer import ROOT, load_config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path,
                        default=ROOT/'configs'/'multifield_historical_replay.yaml')
    parser.add_argument('--output-dir', type=Path,
                        default=ROOT/'outputs'/'multifield'/'FIELD_SOIL_AUDIT')
    args = parser.parse_args(argv)
    config = load_config(args.config)
    _, _, zones, _ = load_model(config)
    soils = {zone['Name']: child(zone, 'Soil') for zone in zones}
    reference_field = sorted(soils, key=lambda name: int(name[5:]))[0]
    rows = [{'field': field, 'soil_sha256': digest(soil),
             'static_soil_sha256': digest(soil, ignore_initial_state=True),
             'same_as_reference': digest(soil) == digest(soils[reference_field]),
             'same_static_soil_as_reference':
                 digest(soil, ignore_initial_state=True) ==
                 digest(soils[reference_field], ignore_initial_state=True)}
            for field, soil in sorted(soils.items(), key=lambda item: int(item[0][5:]))]
    variants = sorted({row['soil_sha256'] for row in rows})
    static_variants = sorted({row['static_soil_sha256'] for row in rows})
    payload = {
        'created_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'config': str(args.config.resolve()),
        'model': config['model'],
        'model_sha256': fingerprint(config['model']),
        'reference_field': reference_field,
        'field_count': len(rows),
        'unique_soil_input_variants': len(variants),
        'unique_static_soil_profiles': len(static_variants),
        'all_fields_share_identical_soil_inputs': len(variants) == 1,
        'all_fields_share_identical_static_soil_profile': len(static_variants) == 1,
        'fields': rows,
        'differences_from_reference': {
            field: differences(soils[reference_field], soil)
            for field, soil in soils.items() if field != reference_field
        },
        'interpretation': (
            'This is a structural input comparison. Identical inputs do not prove identical '
            'real soils; differences do not establish which profile is correct.'
        ),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir/'field_soil_audit.json', payload)
    with (args.output_dir/'field_soil_hashes.csv').open(
            'w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f'FIELDS: {len(rows)}; UNIQUE SOIL INPUTS: {len(variants)}')
    print(f'OUTPUT: {args.output_dir.resolve()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
