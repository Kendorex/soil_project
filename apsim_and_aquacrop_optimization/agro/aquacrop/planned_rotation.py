"""Build explicit effective-crop windows from a newly planned RotationManager."""
from datetime import date, timedelta

from .field_config import parse_day_month, unique_node, walk

PERENNIALS = {'RedClover', 'PlantainForage'}
KEYS = ('RotationCrops', 'RotationCultivars', 'RotationSowingDepths',
        'RotationRowSpacings', 'RotationPopulations', 'RotationSowingDates',
        'RotationHarvestDates', 'RotationHarvestCrops', 'RotationUses', 'PastureAges')


def planned_rotation(field, simulation, zone, catalog):
    clock = unique_node(list(walk(simulation)), kind='Models.Clock, Models')
    start, end = [date.fromisoformat(clock[key][:10]) for key in ('Start', 'End')]
    settings = {p['Key']: p['Value'] for p in unique_node(list(walk(zone)), name='RotationManager')['Parameters']}
    return planned_windows(field, start, end, settings, catalog)


def planned_windows(field, start, end, settings, catalog):
    """Pure effective-window calculation, also used by standalone JSON schedules."""
    arrays = {key: [v.strip() for v in settings[key].split(',')] for key in KEYS}
    count = end.year - start.year + 1
    if any(len(values) != count for values in arrays.values()):
        raise ValueError('Planned rotation must cover its entire clock')
    rows, intervals, active = [], [], {}
    for index, year in enumerate(range(start.year, end.year + 1)):
        names = arrays['RotationCrops'][index].split('+')
        values = {key: arrays[key][index].split(';') for key in KEYS[1:5]}
        if any(len(v) != len(names) for v in values.values()):
            raise ValueError('Planned mixture component counts differ')
        components = [dict(crop=name, cultivar=values['RotationCultivars'][i],
                           population_m2=float(values['RotationPopulations'][i]),
                           depth_mm=float(values['RotationSowingDepths'][i]),
                           row_spacing_mm=float(values['RotationRowSpacings'][i])) for i, name in enumerate(names)]
        use = arrays['RotationUses'][index]
        harvest = parse_day_month(arrays['RotationHarvestDates'][index], year)
        next_winter = index + 1 < count and 'Winter_rye' in arrays['RotationCultivars'][index + 1]
        next_sow = parse_day_month(arrays['RotationSowingDates'][index + 1], year) if next_winter else None
        if use == 'hay' and set(names) == PERENNIALS:
            age = int(arrays['PastureAges'][index])
            compatible = set(active) == set(names) and all(active[c['crop']]['cultivar'] == c['cultivar'] for c in components)
            if not compatible or (index and age == 1 and arrays['RotationUses'][index - 1] == 'hay'):
                active = {c['crop']: dict(c) for c in components}
            effective = list(active.values())
            sow = date(max(1, year - age + 1), 1, 1)
            crop_start = max(start, date.fromisoformat(f"{year}-{field['forage_season']['start']}"))
            crop_end = date.fromisoformat(f"{year}-{field['forage_season']['end']}")
            if index + 1 < count and arrays['RotationUses'][index + 1] != 'hay':
                crop_end = min(crop_end, parse_day_month(settings.get('TerminationDate', '20-Aug'), year) - timedelta(days=1))
            profile, kind, column = 'ForageMix_Local', 'forage', 'MixYield'
            cultivation_year = max(1, age)
            reset = index == 0 or arrays['RotationUses'][index - 1] != 'hay' or age == 1
        elif use == 'grain' and (names == ['Wheat'] or names == ['Oats'] or set(names) == {'Oats', *PERENNIALS}):
            annual = next(c for c in components if c['crop'] not in PERENNIALS)
            winter = 'Winter_rye' in annual['cultivar']
            sow = parse_day_month(arrays['RotationSowingDates'][index], year - int(winter))
            crop_start = max(start, sow)
            crop_end = harvest - timedelta(days=1)
            profile = (f"OatsForageMix_{annual['cultivar']}" if len(names) > 1
                       else f"{annual['crop']}_{annual['cultivar']}")
            effective, kind, column, cultivation_year, reset = components, 'grain', annual['crop'] + 'Yield', 1, True
            active = {c['crop']: dict(c) for c in components if c['crop'] in PERENNIALS}
        else:
            raise ValueError(f'Unsupported planned AquaCrop crop/use: {names}/{use}')
        if next_sow is not None:
            crop_end = min(crop_end, next_sow - timedelta(days=1))
        if profile not in catalog:
            raise ValueError(f'No explicit AquaCrop crop profile for {profile}')
        if crop_start > crop_end or not crop_start <= harvest <= (crop_end + timedelta(days=int(kind == 'grain'))):
            raise ValueError(f'Harvest outside planned growing window: {field["id"]}/{year}')
        rows.append(dict(year=year, declared_components=components, active_perennials=list(active.values()),
                         sowing_date=sow.isoformat(), harvest_dates=[harvest.isoformat()], harvest_use=use,
                         source_harvest_crops=arrays['RotationHarvestCrops'][index].split('+'),
                         pasture_age=int(arrays['PastureAges'][index]),
                         cultivar_application='explicit planned profiles; carried perennial sowing parameters retained'))
        intervals.append(dict(id=f'{year}_{profile}', year=year, kind=kind, source_yield_column=column,
                              harvest_use=use, crop_start=crop_start.isoformat(), crop_end=crop_end.isoformat(),
                              components=effective, cultivation_year=cultivation_year, catalog_crop=profile,
                              harvest_dates=[harvest.isoformat()], source_sowing_date=sow.isoformat(), reset_assimilates=reset))
        if kind == 'grain' and active:
            last = date.fromisoformat(f"{year}-{field['forage_season']['end']}")
            if next_sow is not None:
                last = min(last, next_sow - timedelta(days=1))
            if harvest <= last:
                intervals.append(dict(id=f'{year}_understorey_after_oats', year=year, kind='forage',
                                      source_yield_column='MixYield', harvest_use='understorey',
                                      crop_start=harvest.isoformat(), crop_end=last.isoformat(), components=list(active.values()),
                                      cultivation_year=1, catalog_crop='ForageMix_Local', harvest_dates=[],
                                      source_sowing_date=(sow + timedelta(days=1)).isoformat(), reset_assimilates=True))
    intervals.sort(key=lambda item: item['crop_start'])
    for index, item in enumerate(intervals):
        if index and intervals[index - 1]['crop_end'] >= item['crop_start']:
            raise ValueError('Planned effective-crop windows overlap')
        item['simulation_start'] = start.isoformat() if index == 0 else item['crop_start']
        item['simulation_end'] = ((date.fromisoformat(intervals[index + 1]['crop_start']) - timedelta(days=1)).isoformat()
                                  if index + 1 < len(intervals) else end.isoformat())
    return start, end, rows, intervals
