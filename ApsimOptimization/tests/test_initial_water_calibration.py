import json

import pytest

from src.initial_water_calibration import (
    gravimetric_to_volumetric_fraction,
    patch_initial_water,
    water_between_airdry_and_dul,
)


def test_gravimetric_percent_is_converted_with_bulk_density():
    assert gravimetric_to_volumetric_fraction(18.75, 1.19) == pytest.approx(0.223125)


def test_water_fraction_interpolates_between_airdry_and_dul():
    assert water_between_airdry_and_dul(0.10, 0.50, 0.0) == pytest.approx(0.10)
    assert water_between_airdry_and_dul(0.10, 0.50, 0.5) == pytest.approx(0.30)
    assert water_between_airdry_and_dul(0.10, 0.50, 1.0) == pytest.approx(0.50)


def test_initial_water_patch_changes_only_requested_layers(tmp_path):
    apsimx = tmp_path / "temporary.apsimx"
    data = {
        "Children": [
            {
                "Name": "Water",
                "$type": "Models.Soils.Water, Models",
                "InitialValues": [0.5, 0.5, 0.5],
            },
            {
                "Name": "Physical",
                "$type": "Models.Soils.Physical, Models",
                "AirDry": [0.1, 0.15, 0.2],
                "DUL": [0.5, 0.55, 0.6],
            },
        ]
    }
    apsimx.write_text(json.dumps(data), encoding="utf-8")

    values = patch_initial_water(apsimx, 0.5, layers_to_adjust=2)

    assert values == pytest.approx([0.30, 0.35, 0.50])
    saved = json.loads(apsimx.read_text(encoding="utf-8"))
    assert saved["Children"][0]["InitialValues"] == pytest.approx([0.30, 0.35, 0.50])
