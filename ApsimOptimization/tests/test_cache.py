from src.cache import ResultCache
import sqlite3


def test_cached_technology_scenario_result_is_reused(tmp_path):
    row = {
        "technology_id": "T1",
        "scenario_id": "s3_norm",
        "weather_file": "data/weather/weather_s3_norm.met",
        "cultivar": "TestCultivar",
        "fertilizer_scheme_id": "F0_no_fertilizer",
        "sowing_date": "15-aug..30-oct",
        "population": 120,
        "depth": 30,
        "row": 250,
    }
    db = tmp_path / "result.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE Report(Yield REAL)")
    connection.execute("INSERT INTO Report VALUES (1000)")
    connection.commit()
    connection.close()
    cache = ResultCache(tmp_path / "cache")
    result = {"technology_id": "T1", "scenario_id": "s3_norm", "profit": 10}
    cache.save(row, db, result)

    loaded = cache.load(dict(row))

    assert loaded == result
