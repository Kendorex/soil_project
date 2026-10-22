"""Diagnostic contract tests; no APSIM or optimizer is executed."""
import copy
import unittest
from unittest.mock import patch

from src.carbon_diagnostics import diagnose_carbon, render_soil_diagnostics


def run_snapshot(end=110):
    return {
        "evaluation_years": [2020], "period_start": "2019-12-31",
        "period_end": "2020-12-31", "context": {"weather_sha256": "same"},
        "fields": {"Field1": {
            "area_ha": 0.2, "profile_depth_mm": 1800, "soil_sha256": "same",
            "rotation_crops": "Wheat", "carbon_start_kg_ha": 100,
            "carbon_end_kg_ha": end, "carbon_change_kg_ha": end - 100,
            "annual_end_kg_ha": {"2020": end}, "report": "Report1",
        }},
    }


def diagnose(baseline=None):
    return diagnose_carbon("model", "database", "weather", [2020],
                           {"Field1": {"area_ha": 0.2}}, baseline)


class DiagnosticsTests(unittest.TestCase):
    @patch("src.carbon_diagnostics.read_carbon_run", return_value=run_snapshot())
    def test_reference_has_values_but_no_feasibility_decision(self, reader):
        result = diagnose()
        self.assertEqual(result["status"], "checked")
        self.assertEqual(result["comparison"]["status"], "reference_run")
        self.assertFalse(result["affects_objectives_or_constraints"])
        self.assertEqual(result["normative_humus_assessment"], "not_assessed")

    @patch("src.carbon_diagnostics.read_carbon_run", return_value=run_snapshot(109))
    def test_comparison_reports_loss_without_rejecting_solution(self, reader):
        baseline = {"F": [-1, -2, 0], "G": [0], "carbon_diagnostics": {
            "status": "checked", "run": run_snapshot()}}
        original = copy.deepcopy(baseline)
        result = diagnose(baseline)
        comparison = result["comparison"]
        self.assertEqual(comparison["status"], "checked")
        self.assertEqual(comparison["fields"][0]["end_difference_kg_ha"], -1)
        self.assertNotIn("passes_carbon_rule", comparison)
        self.assertNotIn("passed", comparison["fields"][0])
        self.assertNotIn("G", result)
        self.assertNotIn("F", result)
        self.assertEqual(baseline, original)

    @patch("src.carbon_diagnostics.read_carbon_run", return_value=run_snapshot())
    def test_legacy_baseline_is_not_treated_as_zero(self, reader):
        result = diagnose({"F": [-1, -2, 0], "G": [0]})
        self.assertEqual(result["status"], "checked")
        self.assertEqual(result["comparison"]["status"], "not_assessed")
        self.assertIn("--with-carbon", result["comparison"]["reason"])

    @patch("src.carbon_diagnostics.read_carbon_run", return_value=run_snapshot())
    def test_incompatible_reference_does_not_hide_current_values(self, reader):
        reference = run_snapshot()
        reference["context"]["weather_sha256"] = "other"
        result = diagnose({"carbon_diagnostics": {"status": "checked", "run": reference}})
        self.assertEqual(result["status"], "checked")
        self.assertEqual(result["run"]["fields"]["Field1"]["carbon_end_kg_ha"], 110)
        self.assertEqual(result["comparison"]["status"], "not_assessed")

    @patch("src.carbon_diagnostics.read_carbon_run", side_effect=ValueError("missing dates"))
    def test_missing_carbon_is_explicit_not_a_pass(self, reader):
        result = diagnose()
        self.assertEqual(result["status"], "not_assessed")
        self.assertEqual(result["reason"], "missing dates")
        self.assertNotIn("run", result)

    def test_report_preserves_unassessed_status_and_reason(self):
        report = render_soil_diagnostics({"carbon_diagnostics": {
            "status": "not_assessed", "reason": "missing carbon report"}})
        self.assertIn("missing carbon report", report)
        self.assertIn("не проверены", report)
        self.assertNotIn("0.000", report)


if __name__ == "__main__":
    unittest.main()
