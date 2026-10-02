import pytest

from src.oats_preseason_n import build_preseason_n_summary


def _row(date, mineral, organic, residue, mineralised, agwt=0, zadok=0, depth=0):
    return {
        "field": "Field2", "date": date, "MineralN": mineral,
        "NO3kgHa_total": mineral - 2, "NH4kgHa_total": 2,
        "UreakgHa_total": 0, "TotalOrganicN_total": organic,
        "SurfaceResidueWt": residue * 50, "SurfaceResidueN": residue,
        "MineralisedN_total": mineralised, "Rain": 1,
        "Denitrification_total": 0.1, "NutrientNatm_total": 0.1,
        "NutrientDenitrifiedN_total": 0.1, "NFlow_bottom_native": -0.2,
        "SurfaceResidueIncorporatedN": 0.5,
        "WheatRootNUptake": 0, "RedCloverRootNUptake": 0,
        "PlantainForageRootNUptake": 0,
        "OatsRootNUptake": -1, "LeachNO3": 0.2, "LeachNH4": 0,
        "LeachUrea": 0, "NO3RunoffLoss_total": 0.1,
        "NH4RunoffLoss_total": 0, "UreaRunoffLoss_total": 0,
        "OatsAGWt": agwt, "OatsZadok": zadok, "OatsRootDepth": depth,
    }


def test_build_preseason_n_summary_tracks_intervals_and_phases():
    rows = [
        _row("2022-08-17", 20, 1000, 5, 0),
        _row("2022-08-18", 21, 999, 6, 1),
        _row("2022-12-31", 25, 995, 4, 2),
        _row("2023-05-03", 30, 990, 3, 3),
        _row("2023-05-04", 60, 990, 3, 0),
        _row("2023-05-05", 58, 989, 3, 1, 1, 5, 20),
        _row("2023-07-20", 25, 985, 2, 2, 100, 71, 1200),
        _row("2023-08-30", 23, 980, 1, 3, 150, 90, 1400),
    ]
    windows = {"Field2": {
        "previous_harvest_date": "2022-08-17", "sowing_date": "2023-05-04",
        "end_date": "2023-08-30",
    }}
    snapshots, intervals = build_preseason_n_summary(rows, windows)
    assert next(row for row in snapshots if row["phase"] == "grain_fill_start")[
        "MineralN"
    ] == 25
    fallow = next(row for row in intervals if row["interval"] == "post_harvest_to_pre_sowing")
    assert fallow["net_mineralised_n_sum_kg_ha"] == 6
    assert fallow["mineral_n_change_kg_ha"] == 9
    assert fallow["nutrient_n_atmosphere_loss_sum_kg_ha"] == pytest.approx(0.3)
    assert fallow["bottom_n_flow_native_sum"] == pytest.approx(-0.6)
    active = next(row for row in intervals if row["interval"] == "oat_active")
    assert active["net_mineralised_n_sum_kg_ha"] == 6
    assert active["mineral_n_change_kg_ha"] == -35
