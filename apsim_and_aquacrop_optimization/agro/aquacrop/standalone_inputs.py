"""Native AquaCrop files from standalone soil, crop, weather and schedule JSON."""
from datetime import date
import math
import re
import shutil

from . import field_config as io
from .planned_rotation import planned_windows
from .weather_utils import weather_text


def registry(project):
    fields=[io.load_json(io.local_path(name)) for name in project['fields']]
    ids=[f['id'] for f in fields]
    if not ids or len(set(ids))!=len(ids) or any(not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',v) for v in ids):
        raise ValueError('Expected distinct safe standalone field identifiers')
    if any(f.get('management_mode')!='standalone' for f in fields):
        raise ValueError('Standalone project requires standalone fields')
    return fields


def prepare_field(field,catalog):
    model=io.load_json(io.local_path(field['model_file']))
    start,end=[date.fromisoformat(model['period'][key]) for key in ('start','end')]
    profiles=model['profiles'];sequence=model['sequence']
    if len(sequence)!=end.year-start.year+1:
        raise ValueError('Standalone sequence must cover the complete model period')
    settings={key:[] for key in ('RotationCrops','RotationCultivars','RotationSowingDepths','RotationRowSpacings',
        'RotationPopulations','RotationSowingDates','RotationHarvestDates','RotationHarvestCrops','RotationUses','PastureAges')}
    for state in sequence:
        profile=profiles[state];components=profile['components']
        row={'RotationCrops':'+'.join(c['crop'] for c in components),
             'RotationCultivars':';'.join(c['cultivar'] for c in components),
             'RotationSowingDepths':';'.join(str(c['depth_mm']) for c in components),
             'RotationRowSpacings':';'.join(str(c['row_spacing_mm']) for c in components),
             'RotationPopulations':';'.join(str(c['population_m2']) for c in components),
             'RotationSowingDates':profile['sowing_day_month'],'RotationHarvestDates':profile['harvest_day_month'],
             'RotationHarvestCrops':profile['harvest_product'],'RotationUses':profile['use'],'PastureAges':str(profile['pasture_age'])}
        for key,value in row.items():settings[key].append(value)
    settings={key:', '.join(value) for key,value in settings.items()}
    start,end,rotation,segments=planned_windows(field,start,end,settings,catalog)
    soil=model['soil'];layers=soil['layers'];water=soil['initial_water_m3_m3']
    if len(water)!=len(layers) or not layers:
        raise ValueError('Standalone initial water must match soil layers')
    for layer,theta in zip(layers,water):
        if not all(math.isfinite(float(v)) for v in [*layer.values(),theta]):raise ValueError('Non-finite soil input')
        if not 0<=layer['wilting_point']<=layer['field_capacity']<=layer['saturation']<=1 or not 0<=theta<=layer['saturation']:
            raise ValueError('Invalid standalone water/soil ordering')
        if layer['thickness_m']<=0 or layer['conductivity_mm_day']<0:raise ValueError('Invalid soil thickness/conductivity')
    latitude,weather=io.source_weather(field)
    if weather[0]['date']>start or weather[-1]['date']<end:raise ValueError('Standalone weather does not cover period')
    root=io.ROOT/'generated'/field['id'];data=root/'DATA'
    for path in (data,root/'LIST',root/'PARAM'):path.mkdir(parents=True,exist_ok=True)
    lines=[field['id']+' - standalone soil','7.3 : AquaCrop version',f"{soil['curve_number']} : Curve number",
           f"{soil['rew_mm']} : REW",f'{len(layers)} : Layers','-9 : Obsolete',
           'Thickness SAT FC WP KS Penetrability Gravel CRa CRb Description','m vol% mm/day %']
    for i,layer in enumerate(layers):
        lines.append(f"{layer['thickness_m']:.8f} {layer['saturation']*100:.8f} {layer['field_capacity']*100:.8f} {layer['wilting_point']*100:.8f} {layer['conductivity_mm_day']:.8f} 100 0 -0.390600 1.255639 layer_{i+1}")
    (data/'Soil.SOL').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    for name,title,values in [('Weather.Tnx','Standalone Tmin Tmax',[f"{r['tmin']:.3f} {r['tmax']:.3f}" for r in weather]),
                              ('Weather.PLU','Standalone rain',[f"{r['rain']:.3f}" for r in weather]),
                              ('Weather.ETo','FAO56 Hargreaves ETo',[f"{r['eto']:.5f}" for r in weather])]:
        (data/name).write_text(weather_text(weather[0]['date'],title,values),encoding='utf-8')
    shutil.copyfile(io.local_path(field['co2_file']),data/'MaunaLoa.CO2')
    (data/'Weather.CLI').write_text('Standalone weather\n7.3\nWeather.Tnx\nWeather.ETo\nWeather.PLU\nMaunaLoa.CO2\n',encoding='utf-8')
    for segment in segments:
        profile=catalog[segment['catalog_crop']];base=io.local_path(profile['file']).read_text(encoding='utf-8')
        duration=(date.fromisoformat(segment['crop_end'])-date.fromisoformat(segment['crop_start'])).days+1
        values=dict(population_per_ha=round(sum(c['population_m2'] for c in segment['components'])*10000),
                    maturity=duration,maximum_rooting=max(10,round(duration*.75)),senescence=max(10,round(duration*.85)))
        if segment['kind']=='grain':values.update(emergence=min(8,duration//8),flowering=round(duration*.55),
                                                  harvest_index_days=round(duration*.35),flowering_length=7)
        if segment['id'].endswith('understorey_after_oats'):values['sown_in_first_year']=0
        (data/(segment['id']+'.CRO')).write_text(io.crop_text(base,field['id']+' '+segment['id']+' - standalone effective crop',values),encoding='utf-8')
        (data/(segment['id']+'.MAN')).write_text(io.management_text(field,segment),encoding='utf-8')
    prm=[field['id']+' - standalone crop map','7.3 : AquaCrop version']
    for i,segment in enumerate(segments):prm+=io.project_run(segment,i==0)
    (root/'LIST/Field.PRM').write_text('\n'.join(prm)+'\n',encoding='utf-8')
    (root/'LIST/ListProjects.txt').write_text('Field.PRM\n',encoding='utf-8')
    shutil.copyfile(io.ROOT/'DATA/Simulation.PPn',root/'PARAM/Field.PPn')
    io.save_json(root/'plan.json',dict(workspace_root=str(io.ROOT),field_id=field['id'],aqua_root=f"generated/{field['id']}",
        project_file='LIST/Field.PRM',output_dir=f"outputs/{field['id']}",initial_water=water,
        maximum_compartment_thickness_m=field['maximum_compartment_thickness_m'],
        runs=[dict(id=s['id'],reset_assimilates=s['reset_assimilates']) for s in segments]))
    io.save_json(root/'source_mapping.json',dict(field=field,period_start=start.isoformat(),period_end=end.isoformat(),
        area_ha=model['area_ha'],latitude=latitude,soil_layer_count=len(layers),source_rotation=rotation,segments=segments,
        approximation='Standalone uncalibrated effective crops; field-capacity initialization; no external soil model.'))
    return root/'plan.json'
