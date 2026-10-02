import sqlite3

import pytest

from src.results_reader import ResultsError, read_report


CONFIG = {"report_table": "Report", "yield_column": "Yield"}
STRICT_QUALITY = {
    "require_sowing": True,
    "require_harvest": True,
    "allow_zero_yield": False,
    "minimum_sowing_events": 1,
    "minimum_harvest_events": 1,
}


def make_db(path, columns="Yield REAL", rows=()):
    connection = sqlite3.connect(path)
    connection.execute(f"CREATE TABLE Report({columns})")
    for row in rows:
        placeholders = ",".join("?" for _ in row)
        connection.execute(f"INSERT INTO Report VALUES ({placeholders})", row)
    connection.commit()
    connection.close()


def test_yield_gt_100_is_converted_from_kg_ha(tmp_path):
    db = tmp_path / "result.db"
    make_db(db, rows=[(0,), (5120,)])
    values, _diagnosis = read_report(db, CONFIG)
    assert values["yield_raw"] == 5120
    assert values["yield_t_ha"] == 5.12
    assert values["yield_status"] == "yield_read_kg_ha_converted_to_t_ha"


def test_explicit_kg_ha_unit_converts_small_nonzero_yield(tmp_path):
    db = tmp_path / "small_yield.db"
    make_db(db, rows=[(8.5,)])
    values, _ = read_report(db, {**CONFIG, "yield_source_unit": "kg_per_ha"})
    assert values["yield_raw"] == 8.5
    assert values["yield_t_ha"] == 0.0085
    assert values["yield_status"] == "yield_read_kg_ha_converted_to_t_ha"


def test_yield_lte_30_is_t_ha(tmp_path):
    db = tmp_path / "result.db"
    make_db(db, rows=[(4.7,)])
    values, _diagnosis = read_report(db, CONFIG)
    assert values["yield_raw"] == 4.7
    assert values["yield_t_ha"] == 4.7
    assert values["yield_status"] == "yield_read_t_ha"


def test_last_nonzero_yield_is_used(tmp_path):
    db = tmp_path / "result.db"
    make_db(db, rows=[(1200,), (0,), (2300,), (None,)])
    values, _diagnosis = read_report(db, CONFIG)
    assert values["yield_raw"] == 2300
    assert values["yield_t_ha"] == 2.3


def test_exact_harvest_stage_precedes_later_unfinished_crop_yield(tmp_path):
    db = tmp_path / "two_cycles.db"
    make_db(
        db,
        columns='Yield REAL, "Wheat.Phenology.CurrentStageName" TEXT, Date TEXT',
        rows=[
            (2500, "HarvestRipe", "2021-09-01"),
            (0, "Sowing", "2022-05-01"),
            (1200, "", "2022-12-31"),
        ],
    )

    values, diagnosis = read_report(db, CONFIG)

    assert values["yield_raw"] == 2500
    assert values["yield_t_ha"] == 2.5
    assert values["soil_output_timing"] == "harvest_row"
    assert values["soil_output_date"] == "2021-09-01"
    assert diagnosis["selected_report_rowid"] == 1


def test_all_zero_yield_is_completed_without_yield(tmp_path):
    db = tmp_path / "result.db"
    make_db(db, rows=[(0,), (0,)])
    values, _diagnosis = read_report(db, CONFIG)
    assert values["yield_t_ha"] == 0.0
    assert values["yield_raw"] == 0.0
    assert values["yield_status"] == "no_yield_or_no_harvest"
    assert values["run_status"] == "completed_without_yield"
    assert "no usable nonzero values" in values["warning"]


def test_all_null_yield_is_completed_without_yield(tmp_path):
    db = tmp_path / "result.db"
    make_db(db, rows=[(None,), (None,)])
    values, _diagnosis = read_report(db, CONFIG)
    assert values["yield_t_ha"] == 0.0
    assert values["yield_status"] == "no_yield_or_no_harvest"


def test_report_missing_raises_results_error(tmp_path):
    db = tmp_path / "result.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE Other(Yield REAL)")
    connection.commit()
    connection.close()
    with pytest.raises(ResultsError):
        read_report(db, CONFIG)


def test_yield_column_missing_raises_results_error(tmp_path):
    db = tmp_path / "result.db"
    make_db(db, columns="Biomass REAL", rows=[(1.0,)])
    with pytest.raises(ResultsError):
        read_report(db, CONFIG)


def test_explicit_grain_weight_fallback_is_supported(tmp_path):
    db = tmp_path / "result.db"
    make_db(db, columns="GrainWt REAL", rows=[(4200,)])
    values, _ = read_report(db, {**CONFIG, "yield_fallback_columns": ["GrainWt"]})
    assert values["yield_t_ha"] == 4.2


def test_no_yield_records_final_crop_state_when_reported(tmp_path):
    db = tmp_path / "no_yield_state.db"
    make_db(
        db,
        columns=(
            'Yield REAL, "Wheat.Phenology.CurrentStageName" TEXT, '
            '"Wheat.Phenology.Zadok.Stage" REAL, "Wheat.LAI" REAL'
        ),
        rows=[(0, "Vegetative", 21, 0.35), (0, "Ended", 30, 0.0)],
    )

    values, _ = read_report(db, CONFIG)

    assert values["crop_stage_end"] == "Ended"
    assert values["zadok_stage_end"] == 30
    assert values["lai_end"] == 0
    assert "final_stage=Ended" in values["warning"]


def test_strict_quality_distinguishes_crop_not_sown_from_zero_yield(tmp_path):
    db = tmp_path / "not_sown.db"
    make_db(
        db,
        columns=(
            'Yield REAL, "Wheat.Phenology.CurrentStageName" TEXT, '
            '"Wheat.LAI" REAL, "Wheat.AboveGround.Wt" REAL, Date TEXT'
        ),
        rows=[(0, "", 0, 0, "2023-05-01"), (0, "", 0, 0, "2023-12-31")],
    )

    values, diagnosis = read_report(db, CONFIG, quality_config=STRICT_QUALITY)

    assert values["run_status"] == "invalid_not_sown"
    assert values["crop_sown"] is False
    assert values["harvest_reached"] is False
    assert values["peak_lai"] == 0
    assert diagnosis["lifecycle"]["sowing_event_count"] == 0


def test_strict_quality_rejects_sown_crop_without_harvest(tmp_path):
    db = tmp_path / "no_harvest.db"
    make_db(
        db,
        columns=(
            'Yield REAL, "Wheat.Phenology.CurrentStageName" TEXT, '
            '"Wheat.LAI" REAL, "Wheat.AboveGround.Wt" REAL, Date TEXT'
        ),
        rows=[
            (0, "Sowing", 0.0, 0.0, "2023-05-03"),
            (0, "Vegetative", 0.4, 50.0, "2023-06-01"),
        ],
    )

    values, _ = read_report(db, CONFIG, quality_config=STRICT_QUALITY)

    assert values["run_status"] == "invalid_no_harvest"
    assert values["crop_sown"] is True
    assert values["sowing_event_count"] == 1
    assert values["harvest_reached"] is False


def test_strict_quality_flags_zero_yield_at_harvest(tmp_path):
    db = tmp_path / "zero_at_harvest.db"
    make_db(
        db,
        columns=(
            'Yield REAL, "Wheat.Phenology.CurrentStageName" TEXT, '
            '"Wheat.LAI" REAL, "Wheat.AboveGround.Wt" REAL, Date TEXT'
        ),
        rows=[
            (0, "Sowing", 0.0, 0.0, "2023-05-03"),
            (0, "HarvestRipe", 0.0, 5.2, "2023-08-10"),
        ],
    )

    values, _ = read_report(db, CONFIG, quality_config=STRICT_QUALITY)

    assert values["run_status"] == "rejected_zero_yield"
    assert values["harvest_reached"] is True
    assert values["harvest_event_count"] == 1
    assert values["peak_biomass"] == pytest.approx(5.2)


def test_strict_quality_accepts_positive_yield_with_sowing_and_harvest(tmp_path):
    db = tmp_path / "valid.db"
    make_db(
        db,
        columns=(
            'Yield REAL, "Wheat.Phenology.CurrentStageName" TEXT, '
            '"Wheat.LAI" REAL, "Wheat.AboveGround.Wt" REAL, Date TEXT'
        ),
        rows=[
            (0, "Sowing", 0.0, 0.0, "2023-05-03"),
            (2100, "HarvestRipe", 0.0, 300.0, "2023-08-10"),
        ],
    )

    values, _ = read_report(db, CONFIG, quality_config=STRICT_QUALITY)

    assert values["run_status"] == "completed"
    assert values["yield_t_ha"] == pytest.approx(2.1)
    assert values["sowing_detected_date"] == "2023-05-03"
    assert values["harvest_detected_date"] == "2023-08-10"
