from pathlib import Path

from src.rotation_visualization import plot_rotation_map, write_rotation_text_report


def result() -> dict:
    selected = {
        "initial_crop_name": "Озимая рожь",
        "sequence_ids": ["spring_wheat", "oats_with_underseed"],
        "sequence_names": ["Яровая пшеница", "Овес с подсевом"],
        "transition_codes": ["Ц", "Ц"],
        "mean_score": 1.0,
    }
    return {
        "start_year": 2025,
        "end_year": 2026,
        "fields": {"Field1": {"selected_alternative": selected, "alternatives": [selected]}},
        "farm_selection": {
            "within_historical_ranges": True,
            "range_violation_units": 0,
            "annual_counts": [
                {"year": 2025, "spring_wheat": 1, "oats_with_underseed": 0},
                {"year": 2026, "spring_wheat": 0, "oats_with_underseed": 1},
            ],
            "categories": {
                "spring_wheat": {"states": ["spring_wheat"]},
                "oats_with_underseed": {"states": ["oats_with_underseed"]},
            },
        },
    }


def test_text_report_contains_fields_years_and_status(tmp_path: Path):
    path = tmp_path / "rotation.txt"
    write_rotation_text_report(path, result())
    text = path.read_text(encoding="utf-8")
    assert "Field1" in text
    assert "2025: Яровая пшеница" in text
    assert "Соблюдены полностью: да" in text


def test_png_map_is_created(tmp_path: Path):
    path = tmp_path / "rotation.png"
    plot_rotation_map(path, result())
    assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
