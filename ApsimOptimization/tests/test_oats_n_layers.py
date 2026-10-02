import pytest

from src.oats_n_layers import build_oats_n_layers, build_oats_n_uptake_layers


def _row(date, zadok, depth, no3, nh4, urea):
    row = {
        "scenario": "control", "field": "Field2", "date": date,
        "OatsZadok": zadok, "OatsRootDepth": depth,
        "MineralN": sum(no3) + sum(nh4) + sum(urea),
    }
    for prefix, values in (("NO3kgHa", no3), ("NH4kgHa", nh4), ("UreakgHa", urea)):
        for index, value in enumerate(values, 1):
            row[f"{prefix}_layer_{index}"] = value
    for index in range(1, len(no3) + 1):
        row[f"OatsRootLengthDensity_layer_{index}"] = index / 100.0
        row[f"OatsRootNUptakeLayered_layer_{index}"] = float(index)
    return row


def test_build_oats_n_layers_accounts_for_partial_layer_overlap():
    daily = [
        _row("2023-05-10", 10, 200, [10, 20, 30], [1, 2, 3], [0, 0, 0]),
        _row("2023-06-20", 50, 450, [9, 18, 27], [1, 2, 3], [0, 0, 0]),
        _row("2023-07-10", 71, 700, [8, 16, 24], [1, 2, 3], [0, 0, 0]),
        _row("2023-08-01", 89, 800, [7, 14, 21], [1, 2, 3], [0, 0, 0]),
    ]
    summaries, layers = build_oats_n_layers(daily, {"Field2": [150, 150, 300]})
    first = next(row for row in summaries if row["phase"] == "first_active")
    assert first["no3_nh4_profile_kg_n_ha"] == 66
    assert first["no3_nh4_inside_root_depth_proxy_kg_n_ha"] == pytest.approx(18 + 1 / 3)
    assert first["no3_nh4_below_root_depth_proxy_kg_n_ha"] == pytest.approx(47 + 2 / 3)
    partial = next(
        row for row in layers
        if row["phase"] == "first_active" and row["layer"] == 2
    )
    assert partial["rooted_layer_fraction"] == pytest.approx(1 / 3)
    grain_fill = next(row for row in summaries if row["phase"] == "grain_fill_start")
    assert grain_fill["no3_nh4_inside_root_depth_fraction"] == pytest.approx(1.0)


def test_build_oats_n_layers_rejects_missing_report_layer():
    daily = [_row("2023-05-10", 10, 100, [10], [1], [0])]
    with pytest.raises(ValueError, match="missing NO3kgHa layers"):
        build_oats_n_layers(daily, {"Field2": [150, 150]})


def test_build_oats_n_uptake_layers_sums_phase_windows():
    daily = [
        _row("2023-05-10", 20, 200, [10, 20], [1, 2], [0, 0]),
        _row("2023-07-10", 75, 400, [8, 16], [1, 2], [0, 0]),
    ]
    rows = build_oats_n_uptake_layers(daily, {"Field2": [150, 150]})
    first = rows[0]
    second = rows[1]
    assert first["root_n_uptake_active_native_sum"] == 2
    assert first["root_n_uptake_pre_heading_native_sum"] == 1
    assert first["root_n_uptake_grain_fill_native_sum"] == 1
    assert first["root_n_uptake_active_fraction"] == pytest.approx(1 / 3)
    assert second["root_n_uptake_active_native_sum"] == 4
