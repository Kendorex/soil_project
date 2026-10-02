from pathlib import Path

import src.execution as execution


class _Logger:
    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def exception(self, *_args, **_kwargs):
        return None


def test_ignore_cache_forces_apsimx_execution(monkeypatch, tmp_path):
    calls = {"run": 0, "load": 0, "save": 0}

    class FakeCache:
        def __init__(self, directory):
            self.directory = directory

        def load(self, _row):
            calls["load"] += 1
            return {"profit": 999999}

        def save(self, _row, _database, _result):
            calls["save"] += 1

    def fake_run(_paths, _config, _logger):
        calls["run"] += 1

    database = tmp_path / "result.db"
    database.touch()
    monkeypatch.setattr(execution, "ResultCache", FakeCache)
    monkeypatch.setattr(execution, "run_apsim_files", fake_run)
    monkeypatch.setattr(execution, "find_db_for_apsimx", lambda _path: database)
    monkeypatch.setattr(
        execution,
        "read_report",
        lambda *_args: (
            {
                "yield_t_ha": 1.0,
                "yield_raw": 1000.0,
                "yield_status": "yield_read_kg_ha_converted_to_t_ha",
                "run_status": "completed",
                "warning": "",
            },
            {"tables": {"Report": {"columns": ["Yield"], "row_count": 1}}},
        ),
    )
    monkeypatch.setattr(
        execution,
        "calculate_economics",
        lambda *_args, **_kwargs: {
            "grain_revenue": 14300.0,
            "fertilizer_material_cost": 0.0,
            "fertilizer_cost": 0.0,
            "application_cost": 0.0,
            "finance_cost": 0.0,
            "financing_cost": 0.0,
            "fertilizer_controllable_cost": 0.0,
            "seed_rate_kg_ha": 138.64,
            "seed_cost_total": 4159.2,
            "seed_cost_adjustment": 0.0,
            "seed_economics_status": "provisional",
            "controllable_cost": 0.0,
            "profit": -8650.0,
        },
    )
    monkeypatch.setattr(execution, "load_external_soil_data", lambda *_args: ({}, ""))
    monkeypatch.setattr(
        execution,
        "soil_values_for_run",
        lambda *_args: {"N_NO3": None, "P_2O5": None, "SOC": None, "SOC_0": None},
    )
    monkeypatch.setattr(
        execution,
        "evaluate",
        lambda *_args: ("soil_constraints_unchecked", None, "soil constraints source is disabled"),
    )

    row = {
        "run_id": "T1_s3_norm",
        "technology_id": "T1",
        "scenario_id": "s3_norm",
        "weather_file": "data/weather/weather_s3_norm.met",
        "cultivar": "TestCultivar",
        "fertilizer_scheme_id": "F0_no_fertilizer",
        "fertilizer_type": "",
        "fertilizer_amount": 0,
        "fertilizer_passes": 0,
        "sowing_date": "15-aug..30-oct",
        "population": 250,
        "depth": 30,
        "row": 250,
        "working_apsimx": "work/T1/s3_norm.apsimx",
    }
    config = {
        "_root": Path(tmp_path),
        "paths": {"outputs_dir": "outputs"},
        "soil_constraints": {"strict": False, "source": "disabled"},
        "results": {"report_table": "Report", "yield_column": "Yield"},
        "nitrogen_balance": {},
    }
    inputs = {
        "schemes": [{"id": "F0_no_fertilizer", "passes": 0, "events": []}],
        "prices": {},
        "constants": {},
    }

    result = execution.execute_rows(
        [row], config, inputs, _Logger(), continue_on_error=False, use_cache=False
    )

    assert calls == {"run": 1, "load": 0, "save": 1}
    assert result[0]["actual_apsim_run"] == 1
    assert result[0]["cache_hit"] is False
