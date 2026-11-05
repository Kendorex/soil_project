"""Python-side BMI protocol smoke tests for the aquacrop-bmi-rs PyO3 binding.

These complement (do not replace) the Rust-side tests in crates/aquacrop-bmi/tests/, which cover
the same registry/lifecycle/override logic more exhaustively in pure Rust. This file exists to
prove the actual Python-facing surface works: numpy array in/out, exception types, and (where
installed) duck-type compatibility against bmipy.Bmi's abstract method set.
"""

import json
import os

import numpy as np
import pytest

from aquacrop_bmi_rs import AquaCropBmi

TESTCASE_ROOT = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "..",
    "third-party",
    "KUL-RSDA",
    "AquaCrop",
    "testcase",
)


def _read(relative):
    with open(os.path.join(TESTCASE_ROOT, relative), encoding="utf-8") as handle:
        return handle.read()


def _scenario_dict():
    return {
        "season_year": 1,
        "crop_text": _read("DATA/AlfOttawaGDD.CRO"),
        "soil_text": _read("DATA/Ottawa.SOL"),
        "management_text": _read("DATA/Ottawa.MAN"),
        "temperature_text": _read("DATA/Ottawa.Tnx"),
        "rain_text": _read("DATA/Ottawa.PLU"),
        "eto_text": _read("DATA/Ottawa.ETo"),
        "co2_text": _read("SIMUL/MaunaLoa.CO2"),
        "simulation_start": {"year": 2014, "month": 5, "day": 21},
        "simulation_end": {"year": 2014, "month": 10, "day": 31},
        "crop_start": {"year": 2014, "month": 5, "day": 21},
        "crop_end": {"year": 2014, "month": 10, "day": 31},
    }


def _scenario_json():
    return json.dumps(_scenario_dict())


def test_component_name_and_counts():
    model = AquaCropBmi()
    assert model.get_component_name() == "AquaCrop"
    assert model.get_input_item_count() == 16
    # The supported registry exposes 32 output variables.
    assert model.get_output_item_count() == 32
    assert len(model.get_input_var_names()) == 16
    assert len(model.get_output_var_names()) == 32


def test_initialize_is_bmi_spec_compliant_and_reads_a_config_file(tmp_path):
    """`initialize` must take a path per BMI 2.0 ("the path to its configuration file"), not
    in-memory content. Use `initialize_from_json` or `initialize_from_dict` for that."""
    config_path = tmp_path / "scenario.json"
    config_path.write_text(_scenario_json(), encoding="utf-8")

    model = AquaCropBmi()
    model.initialize(str(config_path))
    assert model.get_current_time() == 0.0
    model.update()
    model.finalize()


def test_initialize_rejects_a_path_that_does_not_exist():
    model = AquaCropBmi()
    with pytest.raises(RuntimeError):
        model.initialize("/nonexistent/path/scenario.json")


def test_initialize_from_dict_runs_a_full_season():
    model = AquaCropBmi()
    model.initialize_from_dict(_scenario_dict())
    assert model.get_current_time() == 0.0
    end_time = model.get_end_time()

    days = 0
    while model.get_current_time() < end_time:
        model.update()
        days += 1
    assert days == int(end_time)

    dest = np.zeros(1)
    model.get_value("plant_yield~standard", dest)
    assert dest[0] >= 0.0
    model.finalize()


def test_initialize_update_finalize_runs_a_full_season():
    model = AquaCropBmi()
    model.initialize_from_json(_scenario_json())
    assert model.get_current_time() == 0.0
    end_time = model.get_end_time()
    assert end_time > 100

    days = 0
    while model.get_current_time() < end_time:
        model.update()
        days += 1
    assert days == int(end_time)

    dest = np.zeros(1)
    model.get_value("plant_yield~standard", dest)
    assert dest[0] >= 0.0
    model.finalize()


def test_get_value_and_set_value_round_trip_numpy_arrays():
    model = AquaCropBmi()
    model.initialize_from_json(_scenario_json())

    override = np.array([123.0])
    model.set_value("air_precipitation", override)
    model.update()

    dest = np.zeros(1)
    result = model.get_value("air_precipitation", dest)
    assert dest[0] == pytest.approx(123.0)
    assert result[0] == pytest.approx(123.0)


def test_vector_variable_grid_and_shape():
    model = AquaCropBmi()
    model.initialize_from_json(_scenario_json())
    model.update()

    grid = model.get_var_grid("soil__water_content_in_compartments")
    size = model.get_grid_size(grid)
    assert size > 0

    dest = np.zeros(size)
    model.get_value("soil__water_content_in_compartments", dest)
    assert np.all(dest >= 0.0)
    assert np.all(dest <= 1.0)


def test_get_var_type_is_numpy_parseable():
    """`get_var_type` returns a NumPy-compatible dtype string."""
    model = AquaCropBmi()
    model.initialize_from_json(_scenario_json())
    var_type = model.get_var_type("plant_yield~standard")
    assert var_type == "float64"
    assert np.dtype(var_type) == np.dtype("float64")


def test_get_grid_shape_accepts_int32_and_preserves_identity():
    """`get_grid_shape` updates an `int32` output buffer in place."""
    model = AquaCropBmi()
    model.initialize_from_json(_scenario_json())
    model.update()

    grid = model.get_var_grid("soil__water_content_in_compartments")
    shape = np.empty(model.get_grid_rank(grid), dtype=np.int32)
    result = model.get_grid_shape(grid, shape)
    assert result is shape
    assert shape[0] == model.get_grid_size(grid)


def test_get_grid_spacing_and_origin_preserve_identity():
    model = AquaCropBmi()
    model.initialize_from_json(_scenario_json())
    model.update()

    grid = model.get_var_grid("soil__water_content_in_compartments")
    ndim = model.get_grid_rank(grid)

    spacing = np.empty(ndim, dtype=float)
    assert model.get_grid_spacing(grid, spacing) is spacing

    origin = np.empty(ndim, dtype=float)
    assert model.get_grid_origin(grid, origin) is origin


def test_gdd_cumulative_from_planting_matches_the_verified_gd_column():
    """The BMI GDD value matches the cumulative daily-output GDD column."""
    day_file = os.path.join(TESTCASE_ROOT, "OUTP_REF", "OttawaPRMday.OUT")
    with open(day_file, encoding="utf-8") as handle:
        lines = handle.readlines()
    daily_gd = [float(line.split()[23]) for line in lines[5:15]]  # first 10 data rows

    model = AquaCropBmi()
    model.initialize_from_json(_scenario_json())
    dest = np.zeros(1)
    running_sum = 0.0
    for day_index, day_gd in enumerate(daily_gd, start=1):
        model.update()
        running_sum += day_gd
        model.get_value("crop__gdd_cumulative_from_planting", dest)
        # Each daily_gd entry is itself rounded to 1 decimal in the fixture, so the running sum's
        # own rounding error grows with the number of days summed -- +-0.05/day, plus a small
        # margin.
        assert dest[0] == pytest.approx(running_sum, abs=0.05 * day_index + 0.02)


def test_unknown_variable_raises_value_error():
    model = AquaCropBmi()
    model.initialize_from_json(_scenario_json())
    dest = np.zeros(1)
    with pytest.raises(ValueError):
        model.get_value("not_a_real_variable", dest)


def test_write_only_variable_rejects_get_with_not_implemented():
    model = AquaCropBmi()
    model.initialize_from_json(_scenario_json())
    dest = np.zeros(1)
    with pytest.raises(NotImplementedError):
        model.get_value("groundwater__depth", dest)


def test_double_initialize_raises_runtime_error():
    model = AquaCropBmi()
    model.initialize_from_json(_scenario_json())
    with pytest.raises(RuntimeError):
        model.initialize_from_json(_scenario_json())


def test_duck_type_matches_bmipy_bmi_method_set():
    bmipy = pytest.importorskip("bmipy")
    model = AquaCropBmi()
    missing = [
        name
        for name in dir(bmipy.Bmi)
        if not name.startswith("_") and not hasattr(model, name)
    ]
    assert not missing, f"AquaCropBmi is missing bmipy.Bmi methods: {missing}"
