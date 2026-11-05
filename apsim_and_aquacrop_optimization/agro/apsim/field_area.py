"""Explicit operational plot areas, without modifying the calibration export."""
import math


def apply_field_area(document, area_ha):
    if area_ha is None:
        return []
    if isinstance(area_ha, bool) or not math.isfinite(float(area_ha)) or float(area_ha) <= 0:
        raise ValueError('field_area_ha must be positive and finite')
    changes=[]
    def walk(node):
        yield node
        for child in node.get('Children',[]):
            yield from walk(child)
    for zone in walk(document):
        if zone.get('$type','').split(',')[0]=='Models.Core.Zone':
            changes.append({'field':zone['Name'],'before_ha':zone['Area'],'after_ha':float(area_ha)})
            zone['Area']=float(area_ha)
    if not changes:
        raise ValueError('No APSIM field zones for field_area_ha override')
    return changes
