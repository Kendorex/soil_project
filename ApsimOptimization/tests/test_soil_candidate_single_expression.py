from pathlib import Path

import src.soil_diagnostics as diagnostics


class Logger:
    def info(self, *_args):
        return None

    def warning(self, *_args):
        return None


def test_candidates_are_run_one_by_one_and_one_error_does_not_stop_rest(tmp_path, monkeypatch):
    candidates = tmp_path / "candidates.yaml"
    candidates.write_text(
        'candidates:\n  MineralN:\n    - "first as First"\n    - "broken as Broken"\n    - "third as Third"\n',
        encoding="utf-8",
    )
    staged: list[str] = []

    def stage_test_candidate(_config, _scenario, single, suffix=""):
        assert len(single) == 1
        staged.append(single[0].expression)
        return tmp_path / f"{suffix}.apsimx"

    def run_test_candidate(files, _config, _logger):
        if "002_" in files[0].name:
            raise RuntimeError("bad expression")

    def test_result_rows(_database, single, _table):
        candidate = single[0]
        return [{
            "target_name": candidate.target_name, "expression": candidate.expression,
            "alias": candidate.alias, "status": "works_with_values", "sqlite_column_name": candidate.sqlite_column_name,
            "sample_non_null_count": 1, "min_value": 1, "max_value": 1, "units": "", "error_message": "",
        }]

    monkeypatch.setattr(diagnostics, "stage_candidate_test_apsimx", stage_test_candidate)
    monkeypatch.setattr(diagnostics, "run_apsim_files", run_test_candidate)
    monkeypatch.setattr(diagnostics, "find_db_for_apsimx", lambda path: path.with_suffix(".db"))
    monkeypatch.setattr(diagnostics, "candidate_rows_from_database", test_result_rows)
    rows = diagnostics.run_soil_candidate_test({"results": {}}, "s3_norm", candidates, Logger())

    assert len(staged) == 3
    assert [row["status"] for row in rows] == ["works_with_values", "apsim_error", "works_with_values"]
