"""Joint sequence/policy decision space with an explicit management seed."""
from agro.apsim.multiyear_rotation_optimizer import variable_spec,decode_vector


def policy_space(config,preset):
    specs=variable_spec(config);ordered=[]
    for crop,key,options in specs:
        target=preset[crop]['fertilizer'] if key=='fertilizer_scheme' else preset[crop][key]
        values=list(options)
        # Put the configured starting policy at index zero without changing bounds.
        if target in values:values=[target]+[v for v in values if v!=target]
        else:raise ValueError(f'Starting policy outside joint decision domain: {crop}/{key}')
        ordered.append(values)
    return {'specs':specs,'options':ordered}


def decode_policies(config,space,vector):
    if len(vector)!=len(space['specs']):raise ValueError('Invalid joint management vector length')
    original=[]
    for index,(_,_,values),options in zip(vector,space['specs'],space['options']):
        if int(index)!=index or not 0<=int(index)<len(options):raise ValueError('Joint policy index outside domain')
        original.append(values.index(options[int(index)]))
    return decode_vector(config,original)
