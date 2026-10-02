import csv
import json
import sqlite3

from src.constraints import evaluate
from src.soil_diagnostics import (
    Candidate,
    candidate_rows_from_database,
    flatten_candidates,
    load_soil_output_candidates,
    scan_soil_related_nodes,
    write_candidate_test_csv,
)


def test_scan_apsimx_json_finds_soil_related_nodes(tmp_path):
    apsimx = tmp_path / "test.apsimx"
    apsimx.write_text(
        json.dumps(
            {
                "Name": "Simulation",
                "Children": [
                    {"Name": "Soil", "$type": "Models.Soils.Soil, Models"},
                    {"Name": "Weather", "$type": "Models.Climate.Weather, Models"},
                    {"Name": "OrganicMatter", "$type": "Models.Soils.Organic, Models"},
                ],
            }
        ),
        encoding="utf-8",
    )

    diagnosis = scan_soil_related_nodes(apsimx)

    names = {item["name"] for item in diagnosis["matches"]}
    assert "Soil" in names
    assert "OrganicMatter" in names


def test_candidate_yaml_parser_and_flattening(tmp_path):
    path = tmp_path / "soil_output_candidates.yaml"
    path.write_text(
        """
candidates:
  N_NO3:
    - "[Soil].NO3"
    - "[Soil].Nitrogen.NO3"
  SOC:
    - "[Soil].Carbon"
""",
        encoding="utf-8",
    )

    candidates = load_soil_output_candidates(path)
    flattened = flatten_candidates(candidates)

    assert candidates["N_NO3"] == ["[Soil].NO3", "[Soil].Nitrogen.NO3"]
    assert flattened[0] == Candidate("N_NO3", "[Soil].NO3", "candidate__N_NO3__001", "candidate__N_NO3__001")
    assert flattened[-1] == Candidate("SOC", "[Soil].Carbon", "candidate__SOC__001", "candidate__SOC__001")


def test_candidate_result_statuses_are_written(tmp_path):
    db = tmp_path / "result.db"
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE Report("candidate__N_NO3__001" REAL, Yield REAL)')
        connection.execute('INSERT INTO Report VALUES (4.5, 1000)')
        connection.execute('INSERT INTO Report VALUES (5.5, 2000)')

    candidates = [
        Candidate("N_NO3", "[Soil].NO3", "candidate__N_NO3__001"),
        Candidate("SOC", "[Soil].Carbon", "candidate__SOC__001"),
    ]
    rows = candidate_rows_from_database(db, candidates)
    csv_path = tmp_path / "soil_output_candidate_test.csv"
    write_candidate_test_csv(csv_path, rows)

    written = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    statuses = {row["target_name"]: row["status"] for row in written}
    assert statuses == {"N_NO3": "works_with_values", "SOC": "missing_column"}
    assert written[0]["sample_non_null_count"] == "2"


def test_candidate_columns_do_not_enable_constraints_automatically():
    status, feasible, reason = evaluate(
        {
            "candidate__N_NO3__001": 10,
            "candidate__P_2O5__001": 100,
            "candidate__SOC__001": 2,
            "N_NO3": None,
            "P_2O5": None,
            "SOC": None,
        },
        {"N_NO3_min": 4, "P_2O5_min": 51, "SOC_0": 1.5, "strict": False},
    )

    assert status == "soil_constraints_unchecked"
    assert feasible is None
    assert "missing exact soil data" in reason
