"""Synthetic SQLite tests; do not execute APSIM, optimizers or use external data."""
import copy
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager

from src.carbon_diagnostics import CARBON_EXPRESSION, read_carbon_run


@contextmanager
def connect_db(path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "candidate.db"
        self.model = self.root / "candidate.apsimx"
        self.weather = self.root / "weather.met"
        self.weather.write_text("test weather", encoding="utf-8")
        def node(kind, name, **kwargs):
            return {"$type": kind + ", Models", "Name": name, **kwargs}
        soil = node("Models.Soils.Soil", "Soil", Children=[
            node("Models.Soils.Physical", "Physical", Thickness=[1800], BD=[1.2]),
            node("Models.Soils.Organic", "Organic", Thickness=[1800], Carbon=[1.0])])
        report = node("Models.Report", "Report1", VariableNames=[CARBON_EXPRESSION],
                      EventNames=["[Clock].EndOfDay"])
        zone = node("Models.Core.Zone", "Field1", Children=[soil, report, {
            "Name": "Sow using a variable rule", "Parameters": [
                {"Key": "RotationCrops", "Value": "Wheat,Wheat"}]}])
        model = {"Children": [node("Models.Core.Simulation", "Wheat", Children=[
            node("Models.Clock", "Clock", Start="2019-01-01", End="2020-12-31"), zone])]}
        self.model.write_text(json.dumps(model), encoding="utf-8")
        self.fields = {"Field1": {"area_ha": 0.2}}
        with connect_db(self.database) as db:
            db.executescript('''
                CREATE TABLE _Simulations(ID,Name);
                INSERT INTO _Simulations VALUES(1,'Wheat');
                CREATE TABLE _Checkpoints(ID,Name);
                INSERT INTO _Checkpoints VALUES(1,'Current');
                CREATE TABLE _Messages(SimulationID,CheckpointID,MessageType);
                CREATE TABLE _InitialConditions(SimulationID,CheckpointID,ModelPath,Name,Value);
                CREATE TABLE Report1(SimulationID,CheckpointID,"Clock.Today",OrganicC,Zone);
            ''')
            for path, name, value in (
                (".Wheat", "APSIM version", "test version"),
                ("Field1.Soil.Physical", "BD", "1.2"),
                ("Field1.Soil.Physical", "Depth", "0-1800"),
                ("Field1.Soil.Organic", "Carbon", "1"),
                ("Field1.Soil.Organic", "Depth", "0-1800"),
                ("Field1.Report1", "VariableNames", CARBON_EXPRESSION),
                ("Field1.Report1", "EventNames", "[Clock].EndOfDay"),
            ):
                db.execute("INSERT INTO _InitialConditions VALUES(1,1,?,?,?)", (path, name, value))
            begin = dt.date(2019, 12, 31)
            for i in range(367):  # leap year plus previous year-end
                db.execute("INSERT INTO Report1 VALUES(1,1,?,?,?)",
                           ((begin + dt.timedelta(days=i)).isoformat(), 100 + i, "Field1"))

    def read(self):
        return read_carbon_run(self.model, self.database, self.weather, [2020], self.fields)

    def test_reads_exact_period_boundary_and_area(self):
        original_database = self.database.read_bytes()
        row = self.read()["fields"]["Field1"]
        self.assertEqual(row["carbon_change_kg_ha"], 366)
        self.assertEqual(row["carbon_start_kg_ha"], 100)
        self.assertEqual(row["area_ha"], 0.2)
        self.assertEqual(self.database.read_bytes(), original_database)

    def test_database_layer_depth_mismatch_rejected(self):
        with connect_db(self.database) as db:
            db.execute("UPDATE _InitialConditions SET Value='0-300' WHERE Name='Depth'")
        with self.assertRaisesRegex(ValueError, "layer depths"):
            self.read()

    def test_missing_duplicate_null_nan_or_wrong_zone_rejected(self):
        mutations = [
            'DELETE FROM Report1 WHERE "Clock.Today"=\'2020-07-01\'',
            'INSERT INTO Report1 SELECT * FROM Report1 LIMIT 1',
            'UPDATE Report1 SET OrganicC=NULL WHERE "Clock.Today"=\'2020-07-01\'',
            'UPDATE Report1 SET OrganicC=\'NaN\' WHERE "Clock.Today"=\'2020-07-01\'',
            'UPDATE Report1 SET Zone=\'Field2\'',
        ]
        original = self.database.read_bytes()
        for sql in mutations:
            with self.subTest(sql=sql):
                self.database.write_bytes(original)
                with connect_db(self.database) as db:
                    db.execute(sql)
                with self.assertRaises(ValueError):
                    self.read()

    def test_only_current_checkpoint_is_read(self):
        with connect_db(self.database) as db:
            db.execute('INSERT INTO Report1 VALUES(1,2,\'2020-12-31\',99999,\'Field1\')')
        self.assertEqual(self.read()["fields"]["Field1"]["carbon_end_kg_ha"], 466)

    def test_errors_and_model_database_mismatch_rejected(self):
        with connect_db(self.database) as db:
            db.execute("INSERT INTO _Messages VALUES(1,1,0)")
        with self.assertRaisesRegex(ValueError, "APSIM errors"):
            self.read()
        with connect_db(self.database) as db:
            db.execute("DELETE FROM _Messages")
            db.execute("UPDATE _InitialConditions SET Value='2' WHERE Name='Carbon'")
        with self.assertRaisesRegex(ValueError, "database/model mismatch"):
            self.read()

    def test_missing_database_is_not_created(self):
        missing = self.root / "missing.db"
        with self.assertRaises(FileNotFoundError):
            read_carbon_run(self.model, missing, self.weather, [2020], self.fields)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
