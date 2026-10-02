import sqlite3

from src.soil_diagnostics import Candidate, apsim_error_rows, candidate_rows_from_database, not_configured_rows


def test_candidate_statuses_distinguish_values_null_missing_and_error(tmp_path):
    db = tmp_path / "candidate.db"
    connection = sqlite3.connect(db)
    connection.execute('CREATE TABLE Report("WithValues" REAL, "AllNull" REAL)')
    connection.execute("INSERT INTO Report VALUES (1.0, NULL)")
    connection.commit()
    connection.close()
    candidates = [
        Candidate("A", "a as WithValues", "WithValues", "WithValues"),
        Candidate("B", "b as AllNull", "AllNull", "AllNull"),
        Candidate("C", "c as Missing", "Missing", "Missing"),
    ]
    rows = candidate_rows_from_database(db, candidates)
    assert [row["status"] for row in rows] == ["works_with_values", "works_all_null", "missing_column"]
    assert apsim_error_rows([candidates[0]], RuntimeError("bad"))[0]["status"] == "apsim_error"
    assert not_configured_rows(["NO3Leaching"])[0]["status"] == "not_configured"
