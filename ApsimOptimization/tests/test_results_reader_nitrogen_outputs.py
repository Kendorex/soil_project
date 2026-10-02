import sqlite3

from src.results_reader import read_report


def test_nitrogen_outputs_are_read_from_the_harvest_row(tmp_path):
    db = tmp_path / "nitrogen.db"
    columns = (
        "Yield REAL, MineralN REAL, MineralNWithUrea REAL, NO3kgHa REAL, NH4kgHa REAL, "
        "OrganicC REAL, DenitrifiedN REAL, NAtmosphereLoss REAL, NO3Leaching REAL, Date TEXT, "
        '"Wheat.Phenology.CurrentStageName" TEXT'
    )
    with sqlite3.connect(db) as connection:
        connection.execute(f"CREATE TABLE Report({columns})")
        connection.execute(
            "INSERT INTO Report VALUES "
            "(5000, 15, 16, 10, 5, 200, 2, 1, 0.5, '2022-08-01', 'HarvestRipe')"
        )
        connection.execute(
            "INSERT INTO Report VALUES "
            "(0, 99, 100, 90, 9, 300, 3, 2, 1, '2022-08-02', 'Sowing')"
        )
    values, _ = read_report(db, {"report_table": "Report", "yield_column": "Yield"}, {"allow_urea_in_mineral_n": False})
    assert values["MineralN_end"] == 15
    assert values["NO3kgHa_end"] == 10
    assert values["NH4kgHa_end"] == 5
    assert values["OrganicC_end"] == 200
    assert values["DenitrifiedN"] == 2
    assert values["NAtmosphereLoss"] == 1
    assert values["NO3Leaching"] == 0.5
    assert values["soil_output_timing"] == "harvest_row"
    assert values["soil_output_date"] == "2022-08-01"
    assert values["nitrogen_balance_outputs_status"] == "available"


def test_no_harvest_uses_last_report_row(tmp_path):
    db = tmp_path / "no_harvest.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE Report(Yield REAL, NO3kgHa REAL, NH4kgHa REAL)")
        connection.execute("INSERT INTO Report VALUES (0, 2, 1)")
        connection.execute("INSERT INTO Report VALUES (0, 4, 3)")
    values, _ = read_report(db, {"report_table": "Report", "yield_column": "Yield"}, {})
    assert values["MineralN_end"] == 7
    assert values["soil_output_timing"] == "end_of_simulation_no_harvest"


def test_zero_yield_uses_reported_harvest_stage_instead_of_later_rows(tmp_path):
    db = tmp_path / "zero_yield_harvest_stage.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            'CREATE TABLE Report('
            'Yield REAL, NO3kgHa REAL, NH4kgHa REAL, '
            '"Wheat.Phenology.CurrentStageName" TEXT, Date TEXT)'
        )
        connection.execute(
            "INSERT INTO Report VALUES (0, 8, 3, 'HarvestRipe', '2022-08-11')"
        )
        connection.execute(
            "INSERT INTO Report VALUES (0, 90, 9, 'Germinating', '2022-12-31')"
        )

    values, diagnosis = read_report(
        db,
        {"report_table": "Report", "yield_column": "Yield"},
        {},
    )

    assert values["yield_t_ha"] == 0
    assert values["run_status"] == "completed_without_yield"
    assert values["MineralN_end"] == 11
    assert values["soil_output_timing"] == "harvest_row"
    assert values["soil_output_date"] == "2022-08-11"
    assert diagnosis["selected_report_rowid"] == 1
    assert "harvest stage detected" in values["warning"]


def test_layer_indexed_nitrogen_outputs_are_summed(tmp_path):
    db = tmp_path / "indexed_outputs.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            'CREATE TABLE Report('
            'Yield REAL, MineralN REAL, NO3kgHa REAL, NH4kgHa REAL, '
            '"DenitrifiedN(1)" REAL, "DenitrifiedN(2)" REAL, '
            '"NAtmosphereLoss(1)" REAL, "NAtmosphereLoss(2)" REAL)'
        )
        connection.execute("INSERT INTO Report VALUES (4000, 12, 8, 4, 1.5, 2.5, 0.25, 0.75)")

    values, _ = read_report(
        db,
        {"report_table": "Report", "yield_column": "Yield"},
        {"additional_outputs": ["DenitrifiedN", "NAtmosphereLoss"]},
    )

    assert values["DenitrifiedN"] == 4.0
    assert values["DenitrifiedN_status"] == "available"
    assert values["NAtmosphereLoss"] == 1.0
    assert values["NAtmosphereLoss_status"] == "available"
    assert values["nitrogen_balance_outputs_status"] == "available"


def test_partly_null_layer_output_is_marked_partial(tmp_path):
    db = tmp_path / "partial_indexed_outputs.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            'CREATE TABLE Report('
            'Yield REAL, MineralN REAL, "DenitrifiedN(1)" REAL, "DenitrifiedN(2)" REAL)'
        )
        connection.execute("INSERT INTO Report VALUES (4000, 12, 1.5, NULL)")

    values, _ = read_report(
        db,
        {"report_table": "Report", "yield_column": "Yield"},
        {"additional_outputs": ["DenitrifiedN"]},
    )

    assert values["DenitrifiedN"] == 1.5
    assert values["DenitrifiedN_status"] == "partial"
    assert values["nitrogen_balance_outputs_status"] == "partial"
