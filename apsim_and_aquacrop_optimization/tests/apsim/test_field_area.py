from copy import deepcopy
import json
import pytest
from agro.apsim.field_area import apply_field_area


@pytest.mark.parametrize('area',[0,-1,float('nan'),True])
def test_bad_area_is_rejected(area):
    with pytest.raises(ValueError):apply_field_area({},area)


def test_area_only_changes_zone_areas_not_cultivars_soil_or_reports():
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/'models/apsim/simulation/calibrated_history/simulation.apsimx'
    source=json.loads(path.read_text(encoding='utf-8'));copy=deepcopy(source)
    changes=apply_field_area(copy,.1)
    assert len(changes)==10 and all(r['before_ha']==1. and r['after_ha']==.1 for r in changes)
    apply_field_area(copy,1.)
    assert copy==source
