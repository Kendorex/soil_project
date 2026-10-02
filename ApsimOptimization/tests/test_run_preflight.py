import json

import pytest

from src.run_preflight import RunPreflightError, validate_prepared_apsimx


def make_weather(path, rows=((2023, 1), (2023, 2), (2023, 3))):
    lines = [
        "[weather.met.weather]",
        "latitude = 56.8",
        "tav = 5",
        "amp = 20",
        "year day radn maxt mint rain",
        "() () (MJ/m2/d) (oC) (oC) (mm)",
    ]
    lines.extend(f"{year} {day} 10 5 -2 0" for year, day in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_apsimx(path, weather_name="weather.met", datastore_name="result.db"):
    physical = {
        "Name": "Physical",
        "Thickness": [150],
        "BD": [1.2],
        "AirDry": [0.1],
        "LL15": [0.2],
        "DUL": [0.3],
        "SAT": [0.4],
        "KS": [10],
    }
    data = {
        "Children": [
            {"Name": "Clock", "Start": "2023-01-01T00:00:00", "End": "2023-01-03T00:00:00"},
            {"Name": "Weather", "FileName": weather_name},
            {"Name": "DataStore", "FileName": datastore_name},
            {"Name": "Water", "InitialValues": [0.3]},
            physical,
        ]
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def test_preflight_accepts_portable_complete_input(tmp_path):
    make_weather(tmp_path / "weather.met")
    apsimx = tmp_path / "run.apsimx"
    make_apsimx(apsimx)

    result = validate_prepared_apsimx(apsimx)

    assert result["status"] == "passed"
    assert result["clock_start"] == "2023-01-01"
    assert result["warnings"] == [
        "Weather lacks APSIM boundary day(s): 2022-12-31, 2023-01-04"
    ]


def test_preflight_rejects_absolute_weather_path_for_portability(tmp_path):
    weather = tmp_path / "weather.met"
    make_weather(weather)
    apsimx = tmp_path / "run.apsimx"
    make_apsimx(apsimx, weather_name=str(weather.resolve()))

    with pytest.raises(RunPreflightError, match="must be relative"):
        validate_prepared_apsimx(apsimx)


def test_post_gui_preflight_warns_when_verified_package_path_became_absolute(tmp_path):
    weather = tmp_path / "weather.met"
    make_weather(weather)
    apsimx = tmp_path / "run.apsimx"
    make_apsimx(apsimx, weather_name=str(weather.resolve()))

    result = validate_prepared_apsimx(apsimx, {"require_relative_paths": False})

    assert result["status"] == "passed"
    assert any("became absolute after GUI execution" in warning for warning in result["warnings"])


def test_post_gui_preflight_accepts_default_datastore_name_with_manifest_evidence(tmp_path):
    make_weather(tmp_path / "weather.met")
    apsimx = tmp_path / "run.apsimx"
    make_apsimx(apsimx, datastore_name="")

    result = validate_prepared_apsimx(
        apsimx,
        {"require_relative_paths": True, "allow_gui_datastore_default": True},
    )

    assert result["status"] == "passed"
    assert any("DataStore.FileName was cleared by GUI" in warning for warning in result["warnings"])


def test_preflight_rejects_missing_weather_day_inside_clock(tmp_path):
    make_weather(tmp_path / "weather.met", rows=((2023, 1), (2023, 3)))
    apsimx = tmp_path / "run.apsimx"
    make_apsimx(apsimx)

    with pytest.raises(RunPreflightError, match="missing=1"):
        validate_prepared_apsimx(apsimx)


def test_preflight_rejects_inconsistent_soil_water_limits(tmp_path):
    make_weather(tmp_path / "weather.met")
    apsimx = tmp_path / "run.apsimx"
    data = make_apsimx(apsimx)
    physical = next(item for item in data["Children"] if item["Name"] == "Physical")
    physical["LL15"] = [0.35]
    apsimx.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RunPreflightError, match="AirDry <= LL15 < DUL < SAT"):
        validate_prepared_apsimx(apsimx)
