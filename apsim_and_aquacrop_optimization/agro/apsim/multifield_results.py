"""Compatibility facade for result readers and objective calculations."""
from agro.adapters.apsim_results import finite, table_rows, read_results
from agro.evaluation.legacy import score, aggregate_scenario_scores
