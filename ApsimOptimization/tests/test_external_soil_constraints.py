import csv

from src.constraints import evaluate, load_external_soil_data, soil_values_for_run


FIELDS = ["technology_id", "scenario_id", "N_NO3", "P_2O5", "SOC", "SOC_0", "control_date", "soil_layer", "units", "source_note"]


def test_external_soil_csv_is_used_only_when_explicitly_configured(tmp_path):
    path = tmp_path / "soil.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow({"technology_id": "T1", "scenario_id": "s1", "N_NO3": 5, "P_2O5": 60, "SOC": 2, "SOC_0": 1.5, "control_date": "2022-08-01", "soil_layer": "0-20", "units": "confirmed", "source_note": "lab"})
    limits = {"source": "external_file", "external_file": str(path), "strict": False, "N_NO3_min": 4, "P_2O5_min": 51}
    rows, error = load_external_soil_data(limits, tmp_path)
    values = soil_values_for_run({}, limits, {"technology_id": "T1", "scenario_id": "s1"}, rows, error)
    assert evaluate(values, limits)[:2] == ("checked_passed", True)


def test_missing_external_file_is_unchecked_in_non_strict_mode(tmp_path):
    limits = {"source": "external_file", "external_file": "missing.csv", "strict": False}
    rows, error = load_external_soil_data(limits, tmp_path)
    values = soil_values_for_run({}, limits, {"technology_id": "T1", "scenario_id": "s1"}, rows, error)
    assert evaluate(values, limits)[:2] == ("soil_constraints_unchecked", None)
