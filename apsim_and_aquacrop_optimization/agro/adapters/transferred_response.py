"""Explicit external APSIM-derived management response over native AquaCrop.

This is a hybrid output correction, not a native AquaCrop nitrogen/depth model.
For each field/year and population regime:
    hybrid_yield = max(0, native_aqua + apsim_candidate - apsim_reference).
The reference has the same population regime and original fertilizer/depth.
Only experimentally evaluated management nodes are accepted. Soil indicators
remain APSIM outputs and are labelled as such. No extrapolation is performed.
"""
from agro.contracts import FieldYearResult, SimulationResult


def apply_response(native_aqua, reference_apsim, candidate_apsim, calibration_id):
    def index(result):
        return {(row.field_id, row.year): row for row in result.fields}
    aqua, reference, candidate = map(index, (native_aqua, reference_apsim, candidate_apsim))
    if set(aqua) != set(reference) or set(aqua) != set(candidate):
        raise ValueError('Transfer requires identical field/year coverage')
    rows, clipped = [], []
    for key, base in aqua.items():
        ref, trial = reference[key], candidate[key]
        if base.area_ha != ref.area_ha or base.area_ha != trial.area_ha:
            raise ValueError('Transfer requires identical field areas')
        raw = (sum(base.harvested_dm_kg_ha.values()) + sum(trial.harvested_dm_kg_ha.values())
               - sum(ref.harvested_dm_kg_ha.values()))
        if raw < 0:
            clipped.append({'field': key[0], 'year': key[1], 'unclipped_yield_kg_ha': raw})
        rows.append(FieldYearResult(key[0], key[1], base.area_ha, {'harvested_dry_matter': max(0.0, raw)},
                                   dict(trial.metrics)))
    return SimulationResult('aquacrop_with_apsim_management_response', tuple(rows), {
        'calibration_id': calibration_id, 'method': 'additive field/year management response',
        'native_crop_engine': 'aquacrop', 'fertilizer_and_depth_response_source': 'apsim',
        'soil_metrics_source': 'apsim', 'extrapolation': False, 'clipped_negative_predictions': clipped,
        'native_aquacrop_provenance': dict(native_aqua.provenance),
        'reference_apsim_provenance': dict(reference_apsim.provenance),
        'candidate_apsim_provenance': dict(candidate_apsim.provenance),
        'limitation': 'A response transfer between models; not validation against field observations',
    })
