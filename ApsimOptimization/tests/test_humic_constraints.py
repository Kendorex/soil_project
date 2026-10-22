"""Real constraint integration contracts, with no APSIM/optimization execution."""
import copy
import unittest

from src.fertility_constraints import (
    evaluate_humic_period, inequality_constraint_count, validate_config,
)
from src.multiyear_rotation_optimizer import summarize_period


def settings(mode="hard"):
    return {
        "mode": "objective_only",
        "mineral_n_end": {"enabled": True, "reference": "fixed_map_baseline",
                          "min_fraction_of_reference": 1.0},
        "humic_carbon": {"mode": mode, "reference": "period_start", "scope": "whole_profile"},
        "unassessed_indicators": ["mobile_phosphorus", "normative_humus_content"],
    }


def period(values=None):
    return {"status": "checked", "period_start": "2019-12-31", "period_end": "2023-12-31",
            "fields": values or {"Field1": {"start_kg_ha": 100, "end_kg_ha": 110}}}


class HumicConstraintTests(unittest.TestCase):
    def test_hard_rule_rejects_one_field_despite_gain_on_another(self):
        result = evaluate_humic_period(period({
            "Field1": {"start_kg_ha": 100, "end_kg_ha": 99},
            "Field2": {"start_kg_ha": 100, "end_kg_ha": 200},
        }), settings(), ["Field1", "Field2"], [2020, 2021, 2022, 2023])
        self.assertFalse(result["is_feasible"])
        self.assertAlmostEqual(result["aggregate_g"], .01)
        self.assertEqual(result["failed_fields"], ["Field1"])

    def test_rule_uses_own_start_not_another_run(self):
        result = evaluate_humic_period(period(), settings(), ["Field1"], [2020, 2021, 2022, 2023])
        self.assertTrue(result["is_feasible"])
        self.assertEqual(result["aggregate_g"], 0)
        self.assertEqual(result["checks"][0]["threshold_kg_ha"], 100)
        self.assertEqual(result["normative_humus_assessment"], "not_assessed")

    def test_missing_field_data_or_wrong_period_fails_closed(self):
        wrong_period = period()
        wrong_period["period_start"] = "2018-09-01"
        for value, fields in ((None, ["Field1"]), (period(), ["Field1", "Field2"]),
                              (wrong_period, ["Field1"]), (period(), [])):
            with self.subTest(value=value, fields=fields):
                result = evaluate_humic_period(value, settings(), fields, [2020, 2021, 2022, 2023])
                self.assertFalse(result["is_feasible"])
                self.assertGreater(result["aggregate_g"], 0)
                self.assertEqual(result["status"], "missing_data")

    def test_nonfinite_or_negative_stock_cannot_pass(self):
        for value in (float("nan"), float("inf"), -1, True, None):
            result = evaluate_humic_period(period({"Field1": {"start_kg_ha": 100, "end_kg_ha": value}}),
                                           settings(), ["Field1"], [2020, 2021, 2022, 2023])
            self.assertFalse(result["is_feasible"])
            self.assertEqual(result["status"], "missing_data")

    def test_disabled_restores_legacy_constraint_count(self):
        result = evaluate_humic_period(None, settings("disabled"), [], [])
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(inequality_constraint_count(settings("disabled")), 1)
        self.assertEqual(inequality_constraint_count(settings()), 2)

    def test_invalid_humic_settings_rejected_even_when_n_is_disabled(self):
        for key, value in (("mode", "objective_only"), ("reference", "baseline"), ("scope", "topsoil")):
            config = settings()
            config["mineral_n_end"]["enabled"] = False
            config["humic_carbon"][key] = value
            self.assertTrue(validate_config(config))


class PeriodSummaryTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "evaluation_years": [2023],
            "economics": {"crop_price_per_kg_dm": {"spring_wheat": 1},
                          "application_cost_per_ha": 0, "other_cost_per_ha_year": 0},
            "fertilizer": {"forms": {"urea": {"apsim_type": "UreaN", "price_per_kg_n": 1}}},
            "fertility_constraints": settings(),
        }
        self.results = {2023: {"fields": {"Field1": {
            "area_ha": 2, "yield_dm_kg_ha": {"Wheat": 1000}, "mineral_n_end_kg_ha": 40}}}}
        self.patch = {"fields": {"Field1": {"2023": {
            "state_id": "spring_wheat", "primary_yield_components": ["Wheat"]}}}}
        self.policies = {"spring_wheat": {"fertilizer": {
            "name": "N0", "dose_kg_n_ha": 0, "price_per_kg_n": 0}}}
        self.reference = {"2023": {"Field1": 50}}

    def summarize(self, humic):
        return summarize_period(self.results, self.patch, self.config, self.policies,
                                self.reference, humic_period=humic)

    def test_new_constraint_changes_g_and_leaves_three_objectives_unchanged(self):
        humic = period({"Field1": {"start_kg_ha": 100, "end_kg_ha": 99}})
        humic["period_start"] = "2022-12-31"
        constrained = self.summarize(humic)
        self.config["fertility_constraints"]["humic_carbon"]["mode"] = "disabled"
        legacy = self.summarize(None)
        self.assertEqual(constrained["F"], legacy["F"])
        self.assertEqual(legacy["G"], [0])
        self.assertEqual(len(constrained["G"]), 2)
        self.assertGreater(constrained["G"][1], 0)
        self.assertFalse(constrained["fertility_constraints"]["is_feasible"])

    def test_missing_verified_humic_cannot_silently_become_feasible(self):
        result = self.summarize(None)
        self.assertEqual(result["G"][0], 0)
        self.assertGreater(result["G"][1], 0)
        self.assertEqual(result["fertility_constraints"]["humic_carbon"]["status"], "missing_data")

    def test_soft_n_violation_does_not_reject_when_humic_passes(self):
        humic = period()
        humic["period_start"] = "2022-12-31"
        result = self.summarize(humic)
        self.assertGreater(result["F"][2], 0)
        self.assertEqual(result["G"], [0, 0])
        self.assertTrue(result["fertility_constraints"]["is_feasible"])

    def test_n_hard_mode_remains_a_separate_constraint(self):
        self.config["fertility_constraints"]["mode"] = "hard"
        humic = period()
        humic["period_start"] = "2022-12-31"
        result = self.summarize(humic)
        self.assertGreater(result["G"][0], 0)
        self.assertEqual(result["G"][1], 0)
        self.assertFalse(result["fertility_constraints"]["is_feasible"])


if __name__ == "__main__":
    unittest.main()
