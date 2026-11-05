"""Explicit history-to-future APSIM preparation with archived climate analogues."""
from __future__ import annotations

from copy import deepcopy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from agro.adapters.rotation_management import walk, manager, parameters, apply_policies
from agro.apsim.met_weather import read_met

ROTATION_KEYS=('RotationCrops','RotationCultivars','RotationSowingDepths',
               'RotationRowSpacings','RotationPopulations','RotationSowingDates',
               'RotationHarvestDates','RotationHarvestCrops','RotationUses','PastureAges')
SUPPORTED_STATES={'spring_wheat','winter_rye','oats_with_underseed',
                  'perennial_grass_year1','perennial_grass_year2','perennial_grass_year3'}


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def state_of(row):
    crops=row['RotationCrops'];cultivar=row['RotationCultivars']
    if crops=='Wheat' and row['RotationUses']=='grain':
        return 'winter_rye' if 'Winter_rye' in cultivar else 'spring_wheat'
    if crops=='Oats+RedClover+PlantainForage' and row['RotationUses']=='grain':
        return 'oats_with_underseed'
    if crops=='RedClover+PlantainForage' and row['RotationUses']=='hay' and row['PastureAges'] in ('1','2','3'):
        return 'perennial_grass_year'+row['PastureAges']
    return None


def source_parts(document):
    clock=next(n for n in walk(document) if n.get('$type')=='Models.Clock, Models')
    zones={n['Name']:n for n in walk(document) if n.get('$type')=='Models.Core.Zone, Models'}
    return clock,zones


def profile_catalog(document):
    clock,zones=source_parts(document);first=int(clock['Start'][:4]);catalog={}
    for field,zone in zones.items():
        rp=parameters(manager(zone,'RotationManager'))
        arrays={key:[v.strip() for v in rp[key].split(',')] for key in ROTATION_KEYS}
        fp=parameters(manager(zone,'Fertilise at sowing'))
        forms=[v.strip() for v in fp['FertiliserTypes'].split(',')]
        doses=[v.strip() for v in fp['FertiliserAmounts'].split(',')]
        for index in range(len(arrays['RotationCrops'])):
            row={key:values[index] for key,values in arrays.items()}
            state=state_of(row)
            if state:
                catalog.setdefault(state,[]).append({'field':field,'year':first+index,
                    'rotation':row,'fertilizer_form':forms[index],'fertilizer_dose':doses[index]})
    if SUPPORTED_STATES-set(catalog):
        raise ValueError(f'Missing calibrated profiles: {sorted(SUPPORTED_STATES-set(catalog))}')
    return catalog


def extend_model(source, initial_states, sequences, start_year, policies=None, bridge_states=None):
    """Retain history and clone explicit source profiles for the example future."""
    document=deepcopy(source);clock,zones=source_parts(document)
    first=int(clock['Start'][:4]);last=int(clock['End'][:4]);catalog=profile_catalog(source)
    if set(zones)!=set(initial_states) or set(zones)!=set(sequences):
        raise ValueError('Planning states and sequences must cover every calibrated field')
    lengths={len(seq) for seq in sequences.values()}
    if len(lengths)!=1 or not next(iter(lengths)):
        raise ValueError('Future sequences must have equal positive lengths')
    years=next(iter(lengths));end=start_year+years-1
    bridge={int(y):v for y,v in (bridge_states or {}).items()}
    bridge[start_year-1]=initial_states
    missing=set(range(last+1,start_year))-set(bridge)
    if missing:raise ValueError(f'Missing observed bridge crop states for years: {sorted(missing)}')
    if start_year<=last+1:
        raise ValueError('This workflow requires a separate preceding-year bridge after calibrated history')
    assumed_profiles=[]
    for field,zone in zones.items():
        rp=parameters(manager(zone,'RotationManager'))
        arrays={key:[v.strip() for v in rp[key].split(',')] for key in ROTATION_KEYS}
        fp=parameters(manager(zone,'Fertilise at sowing'))
        forms=[v.strip() for v in fp['FertiliserTypes'].split(',')]
        doses=[v.strip() for v in fp['FertiliserAmounts'].split(',')]
        if any(len(a)!=last-first+1 for a in [*arrays.values(),forms,doses]):
            raise ValueError(f'{field}: source rotation does not match calibrated clock')
        for year in range(last+1,end+1):
            state=bridge[year][field] if year<start_year else sequences[field][year-start_year]
            if state not in SUPPORTED_STATES:
                raise ValueError(f'No calibrated crop-state adapter for {state}')
            candidates=catalog[state]
            local=[p for p in candidates if p['field']==field]
            profile=max(local or candidates,key=lambda p:(p['year'],p['field']))
            for key in ROTATION_KEYS:arrays[key].append(profile['rotation'][key])
            forms.append(profile['fertilizer_form']);doses.append(profile['fertilizer_dose'])
            assumed_profiles.append({'field':field,'target_year':year,'state':state,
                'profile_source_field':profile['field'],'profile_source_year':profile['year'],
                'assumption':'Reuse calibrated crop, cultivar and calendar; target-year technology not observed'})
        for parameter in manager(zone,'RotationManager')['Parameters']:
            if parameter['Key'] in arrays:parameter['Value']=', '.join(arrays[parameter['Key']])
        for parameter in manager(zone,'Fertilise at sowing')['Parameters']:
            if parameter['Key']=='FertiliserTypes':parameter['Value']=', '.join(forms)
            if parameter['Key']=='FertiliserAmounts':parameter['Value']=', '.join(doses)
    clock['End']=f'{end}-12-31T00:00:00'
    native={name:{'cultivar':p.get('cultivar'),'population_m2':p['population'],
                   'depth_mm':p['depth'],'row_spacing_mm':p['row_spacing'],
                   'dose_kg_n_ha':p['fertilizer']['dose_kg_n_ha'],'form':p['fertilizer']['apsim_type']}
            for name,p in (policies or {}).items()}
    document,changes=apply_policies(document,native,list(range(start_year,end+1)))
    for node in walk(document):
        if node.get('$type')=='Models.Climate.Weather, Models':node['FileName']='weather.met'
        if node.get('$type')=='Models.Storage.DataStore, Models':node['CustomFileName']='candidate.db'
    return document,{'assumed_calendar_profiles':assumed_profiles,'management_changes':changes,
                     'historical_period':[first,last],'future_period':[start_year,end]}


def write_analogue_weather(source,destination,history_end,mapping,end_year):
    """Replay source climate by month/day; handle leap dates explicitly."""
    data=read_met(source);columns=data['columns'];history_end=dt.date.fromisoformat(history_end)
    indexed={row['date']:row for row in data['rows']}
    if len(indexed)!=len(data['rows']):raise ValueError('Duplicate weather dates')
    mappings={int(target):int(origin) for target,origin in mapping.items()}
    expected=set(range(history_end.year+1,end_year+1))
    if set(mappings)!=expected:raise ValueError('Climate analogue mapping must cover every nonhistorical year')
    header=data['lines'][:data['header_index']+2]
    header.insert(data['header_index'],'! EXAMPLE SCENARIO: nonhistorical years replay archived weather; not observations or a forecast')
    rows=[];interpolated=[]
    first=min(indexed);last=dt.date(end_year,12,31);current=first
    while current<=last:
        if current<=history_end:
            if current not in indexed:raise ValueError(f'Missing historical weather: {current}')
            values=indexed[current]
        else:
            origin=mappings[current.year]
            try:reference=dt.date(origin,current.month,current.day)
            except ValueError:
                before=dt.date(origin,2,28);after=dt.date(origin,3,1)
                if before not in indexed or after not in indexed:raise ValueError('Missing leap-day interpolation neighbours')
                values={name:(indexed[before][name]+indexed[after][name])/2 for name in columns}
                interpolated.append(current.isoformat())
            else:
                if reference not in indexed:raise ValueError(f'Missing analogue weather: {reference}')
                values=indexed[reference]
        row=dict(values);row['year']=current.year;row['day']=current.timetuple().tm_yday
        if any(not math.isfinite(float(row[name])) for name in columns):raise ValueError('Non-finite weather')
        rows.append(' '.join(str(int(row[name])) if name in ('year','day') else format(row[name],'.17g') for name in columns))
        current+=dt.timedelta(days=1)
    destination.write_text('\n'.join(header+rows)+'\n',encoding='utf-8')
    return {'kind':'historical_climate_analogue_not_forecast','source_weather_sha256':fingerprint(source),
        'historical_end':history_end.isoformat(),'target_to_source_year':mappings,
        'leap_days_interpolated':interpolated,'days':len(rows),
        'ignored_source_dates_after_history_end':sum(day>history_end for day in indexed)}
