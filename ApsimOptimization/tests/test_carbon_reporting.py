"""Report-only changes, pool accounting and safe user-run workflow tests."""
import copy
from contextlib import closing
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.carbon_reporting import (
    DETAIL_EXPRESSIONS, DETAIL_VARIABLES, add_carbon_reports,
    compare_carbon_layers, read_carbon_layers,
    summarize_carbon_pools,
)


def simple_model():
    return {"Children": [{"$type": "Models.Core.Simulation, Models", "Name": "Wheat", "Children": [
        {"$type": "Models.Core.Zone, Models", "Name": "Field1", "Children": [
            {"$type": "Models.Soils.Soil, Models", "Name": "Soil", "marker": [1, 2]},
            {"$type": "Models.Manager, Models", "Name": "Manager", "CodeArray": ["unchanged"]},
        ]}]}]}


class ReportingTests(unittest.TestCase):
    def test_only_new_report_changes_and_input_is_untouched(self):
        model = simple_model()
        original = copy.deepcopy(model)
        instrumented, changes = add_carbon_reports(model)
        self.assertEqual(model, original)
        self.assertEqual(len(changes), 1)
        instrumented["Children"][0]["Children"][0]["Children"].pop()
        self.assertEqual(instrumented, original)

    def test_idempotent_and_refuses_conflicting_report(self):
        model, _ = add_carbon_reports(simple_model())
        again, changes = add_carbon_reports(model)
        self.assertEqual(again, model)
        self.assertEqual(changes, [])
        model["Children"][0]["Children"][0]["Children"][-1]["VariableNames"] = []
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            add_carbon_reports(model)

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        model, _ = add_carbon_reports(simple_model())
        self.zone = model["Children"][0]["Children"][0]
        self.initial = {
            ("Field1.OptCarbon_Field1", "VariableNames"): ",".join(DETAIL_VARIABLES),
            ("Field1.OptCarbon_Field1", "EventNames"): "[Clock].EndOfDay",
        }
        columns = [f'"{key}({i})" REAL' for i in (1, 2) for key in DETAIL_EXPRESSIONS]
        self.db.execute('CREATE TABLE OptCarbon_Field1 (SimulationID,CheckpointID,Date,Zone,'
                        + ",".join(columns) + ")")
        self.carbon = {}
        begin = dt.date(2019, 12, 31)
        for i in range(367):
            day = (begin + dt.timedelta(days=i)).isoformat()
            layers = [
                {"CHumic": 40 - .01*i, "CMicrobial": 5, "CInert": 50, "CFresh": 5+.03*i,
                 "COrganic": 100+.02*i, "N_NO3": 1, "N_NH4": .5},
                {"CHumic": 80, "CMicrobial": 4, "CInert": 16, "CFresh": 0,
                 "COrganic": 100, "N_NO3": 2, "N_NH4": 1},
            ]
            values = [v[key] for v in layers for key in DETAIL_EXPRESSIONS]
            row = [1, 1, day, "Field1", *values]
            self.db.execute("INSERT INTO OptCarbon_Field1 VALUES(" + ",".join("?" for _ in row) + ")", row)
            self.carbon[day] = 200 + .02*i

    def read(self):
        return read_carbon_layers(self.db, 1, 1, self.initial, self.zone,
                                  [150, 150], self.carbon, [2020])

    def test_layer_and_pool_changes_separate_fresh_from_other_carbon(self):
        result = self.read()
        self.assertEqual(result["status"], "checked")
        layer = result["layers"][0]
        self.assertEqual((layer["top_mm"], layer["bottom_mm"]), (0, 150))
        self.assertAlmostEqual(layer["change"]["COrganic"], 7.32)
        self.assertAlmostEqual(layer["change"]["CHumic"], -3.66)
        self.assertAlmostEqual(layer["change"]["CFresh"], 10.98)
        self.assertAlmostEqual(layer["change"]["CWithoutFresh"], -3.66)
        comparison = compare_carbon_layers(result, result)
        self.assertEqual(comparison["layers"][0]["end_difference"]["CFresh"], 0)

    def test_incorrect_pool_or_profile_sum_is_rejected(self):
        self.db.execute('UPDATE OptCarbon_Field1 SET "CFresh(1)"="CFresh(1)"+1 WHERE Date="2020-12-31"')
        with self.assertRaisesRegex(ValueError, "pool sum"):
            self.read()
        self.db.execute('UPDATE OptCarbon_Field1 SET "CFresh(1)"="CFresh(1)"-1 WHERE Date="2020-12-31"')
        self.carbon["2020-12-31"] += 1
        with self.assertRaisesRegex(ValueError, "layer sum"):
            self.read()

    def test_missing_dates_are_not_interpolated(self):
        self.db.execute('DELETE FROM OptCarbon_Field1 WHERE Date="2020-07-01"')
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.read()

    def test_legacy_model_has_explicit_unassessed_details(self):
        self.zone["Children"].pop()
        self.assertEqual(self.read()["status"], "not_assessed")

    def test_pool_summary_does_not_confuse_total_gain_with_humic_gain(self):
        fields = {"Field1": {"area_ha": .2, "details": self.read()}}
        result = summarize_carbon_pools(fields, fields)
        self.assertGreater(result["farm"]["COrganic"]["change_kg"], 0)
        self.assertEqual(result["farm"]["COrganic"]["fields_below_period_start"], [])
        self.assertEqual(result["farm"]["CHumic"]["fields_below_period_start"], ["Field1"])
        self.assertAlmostEqual(result["farm"]["CHumic"]["change_kg"], -.732)
        self.assertEqual(result["farm"]["CHumic"]["end_difference_from_baseline_kg"], 0)
        self.assertEqual(result["normative_humus_assessment"], "not_assessed")

    def test_pool_summary_never_replaces_missing_field_with_zero(self):
        fields = {"Field1": {"area_ha": .2, "details": self.read()},
                  "Field2": {"area_ha": .2, "details": {"status": "not_assessed"}}}
        result = summarize_carbon_pools(fields)
        self.assertEqual(result["status"], "not_assessed")
        self.assertNotIn("farm", result)
