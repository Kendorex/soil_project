"""Minimal BMI-style adapter for the multi-field APSIM NSGA-II workflow.

The optimization is a batch calculation rather than a daily time-stepped model.
Consequently one ``update()`` performs one complete optimization run and advances
the adapter from time 0 to time 1.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import numpy as np

from .multifield_model import inventory
from .multifield_optimizer import configuration_errors, load_config, optimize


PARETO_OUTPUTS = (
    "annual_yield_dm_kg",
    "net_profit",
    "soil_interval_violation",
)
SCALAR_OUTPUTS = (
    "evaluated_candidate_count",
    "failed_candidate_count",
    "pareto_solution_count",
)
OUTPUT_VARS = PARETO_OUTPUTS + SCALAR_OUTPUTS

VAR_UNITS = {
    "annual_yield_dm_kg": "kg",
    "net_profit": "currency_unit",
    "soil_interval_violation": "1",
    "evaluated_candidate_count": "1",
    "failed_candidate_count": "1",
    "pareto_solution_count": "1",
}
VAR_DTYPES = {
    "annual_yield_dm_kg": np.dtype("float64"),
    "net_profit": np.dtype("float64"),
    "soil_interval_violation": np.dtype("float64"),
    "evaluated_candidate_count": np.dtype("int64"),
    "failed_candidate_count": np.dtype("int64"),
    "pareto_solution_count": np.dtype("int64"),
}


class BmiApsimOptimization:
    """Expose the existing optimizer through a small BMI-compatible surface."""

    _NAME = "APSIM Multifield NSGA-II Optimization"
    _TIME_UNITS = "optimization_run"
    _TIME_STEP = 1.0

    def __init__(self) -> None:
        self._initialized = False
        self._current_time = 0.0
        self._start_time = 0.0
        self._end_time = 1.0
        self._config: dict[str, Any] | None = None
        self._meta: dict[str, Any] | None = None
        self._result: dict[str, Any] | None = None
        self._output_dir: Path | None = None
        self._var_units = dict(VAR_UNITS)
        self._values: dict[str, np.ndarray] = {}
        self._reset_values()

    def setup(self, cfg_dir: str | Path | None = None, **kwargs):
        """Return the configuration file and directory expected by pymt-style clients."""
        explicit = kwargs.get("config_file")
        if explicit is not None:
            config_file = Path(explicit).resolve()
        elif cfg_dir is not None:
            base = Path(cfg_dir).resolve()
            config_file = base if base.is_file() else base / "multifield.yaml"
        else:
            config_file = Path(__file__).resolve().parents[1] / "configs" / "multifield.yaml"
        if not config_file.is_file():
            raise FileNotFoundError(f"BMI configuration does not exist: {config_file}")
        return str(config_file), str(config_file.parent)

    def initialize(self, config_file: str | Path) -> None:
        """Load and validate a multi-field optimization configuration."""
        if self._initialized:
            raise RuntimeError("BMI model is already initialized; call finalize() first")
        config = load_config(config_file)
        meta = inventory(config)
        errors = configuration_errors(config, meta)
        if errors:
            raise ValueError("\n".join(errors))

        self._config = config
        self._meta = meta
        self._output_dir = Path(config["output_dir"])
        self._result = None
        self._current_time = self._start_time
        self._reset_values()
        if config.get("currency"):
            self._var_units["net_profit"] = str(config["currency"])
        self._initialized = True

    def update(self) -> None:
        """Run one complete NSGA-II optimization and expose its Pareto front."""
        self._require_initialized()
        if self._current_time >= self._end_time:
            return
        assert self._config is not None
        assert self._meta is not None
        assert self._output_dir is not None
        self._result = optimize(self._config, self._meta, self._output_dir)
        self._apply_result(self._result)
        self._current_time = self._end_time

    def update_until(self, time: float) -> None:
        target = float(time)
        if target < self._current_time:
            raise ValueError("BMI optimization cannot move backwards in time")
        if target > self._end_time:
            raise ValueError(f"time={target} exceeds end_time={self._end_time}")
        if target > self._current_time:
            self.update()

    def run(self, config_file: str | Path | None = None) -> dict[str, Any]:
        """Convenience extension: initialize if needed, run, and return the result."""
        if config_file is not None:
            self.initialize(config_file)
        self._require_initialized()
        self.update()
        return self.get_result()

    def finalize(self) -> None:
        """Release adapter state; APSIM subprocesses already finish inside optimize()."""
        self._config = None
        self._meta = None
        self._result = None
        self._output_dir = None
        self._var_units = dict(VAR_UNITS)
        self._current_time = self._start_time
        self._reset_values()
        self._initialized = False

    def get_component_name(self):
        return self._NAME

    def get_input_item_count(self):
        return 0

    def get_output_item_count(self):
        return len(OUTPUT_VARS)

    def get_input_var_names(self):
        return ()

    def get_output_var_names(self):
        return OUTPUT_VARS

    def get_var_names(self):
        return OUTPUT_VARS

    def get_current_time(self):
        return self._current_time

    def get_start_time(self):
        return self._start_time

    def get_end_time(self):
        return self._end_time

    def get_time_units(self):
        return self._TIME_UNITS

    def get_time_step(self):
        return self._TIME_STEP

    def get_value(self, name: str, out=None) -> np.ndarray:
        self._check_var(name)
        if out is None:
            return self._values[name].copy()
        target = np.asarray(out)
        if target.shape != self._values[name].shape:
            raise ValueError(
                f"{name}: expected output shape {self._values[name].shape}, got {target.shape}")
        target[...] = self._values[name]
        return target

    def get_value_ptr(self, name: str) -> np.ndarray:
        self._check_var(name)
        return self._values[name]

    def get_value_at_indices(self, name: str, indices, out=None):
        self._check_var(name)
        selected = self._values[name][np.asarray(indices, dtype=np.int64)]
        if out is None:
            return selected.copy()
        target = np.asarray(out)
        if target.shape != selected.shape:
            raise ValueError(f"{name}: expected output shape {selected.shape}, got {target.shape}")
        target[...] = selected
        return target

    def set_value(self, name: str, value) -> None:
        raise KeyError(f"{name}: this batch optimizer has no BMI input variables")

    def set_value_at_indices(self, name: str, indices, values) -> None:
        raise KeyError(f"{name}: this batch optimizer has no BMI input variables")

    def get_var_type(self, name):
        self._check_var(name)
        return VAR_DTYPES[name].name

    def get_var_units(self, name):
        self._check_var(name)
        return self._var_units[name]

    def get_var_itemsize(self, name):
        self._check_var(name)
        return VAR_DTYPES[name].itemsize

    def get_var_nbytes(self, name):
        self._check_var(name)
        return self._values[name].nbytes

    def get_var_location(self, name):
        self._check_var(name)
        return "node"

    def get_var_grid(self, name):
        self._check_var(name)
        return 0 if name in PARETO_OUTPUTS else 1

    def get_grid_rank(self, grid_id):
        self._check_grid(grid_id)
        return 1

    def get_grid_size(self, grid_id):
        self._check_grid(grid_id)
        return len(self._values[PARETO_OUTPUTS[0]]) if grid_id == 0 else 1

    def get_grid_type(self, grid_id):
        self._check_grid(grid_id)
        return "points" if grid_id == 0 else "scalar"

    def get_grid_shape(self, grid_id):
        return (self.get_grid_size(grid_id),)

    def get_grid_spacing(self, grid_id):
        self._check_grid(grid_id)
        return np.array([1.0], dtype=np.float64)

    def get_grid_origin(self, grid_id):
        self._check_grid(grid_id)
        return np.array([0.0], dtype=np.float64)

    def get_grid_x(self, grid_id):
        return np.arange(self.get_grid_size(grid_id), dtype=np.float64)

    def get_grid_y(self, grid_id):
        self._check_grid(grid_id)
        return np.array([], dtype=np.float64)

    def get_grid_z(self, grid_id):
        self._check_grid(grid_id)
        return np.array([], dtype=np.float64)

    def get_grid_node_count(self, grid_id):
        return self.get_grid_size(grid_id)

    def get_grid_edge_count(self, grid_id):
        self._check_grid(grid_id)
        return 0

    def get_grid_face_count(self, grid_id):
        self._check_grid(grid_id)
        return 0

    def get_grid_edge_nodes(self, grid_id):
        self._check_grid(grid_id)
        return np.array([], dtype=np.int32)

    def get_grid_face_edges(self, grid_id):
        self._check_grid(grid_id)
        return np.array([], dtype=np.int32)

    def get_grid_face_nodes(self, grid_id):
        self._check_grid(grid_id)
        return np.array([], dtype=np.int32)

    def get_grid_nodes_per_face(self, grid_id):
        self._check_grid(grid_id)
        return np.array([], dtype=np.int32)

    def get_result(self) -> dict[str, Any]:
        """Return a defensive copy of the native optimizer payload."""
        self._require_initialized()
        if self._result is None:
            raise RuntimeError("Optimization has not been run; call update() first")
        return copy.deepcopy(self._result)

    def get_status(self) -> str:
        if not self._initialized:
            return "not_initialized"
        if self._result is None:
            return "initialized"
        return str(self._result.get("status", "unknown"))

    def get_output_directory(self) -> str | None:
        """Return the configured result directory as a public convenience method."""
        return str(self._output_dir) if self._output_dir is not None else None

    def _reset_values(self) -> None:
        self._values = {
            name: np.empty(0, dtype=VAR_DTYPES[name]) for name in PARETO_OUTPUTS
        }
        self._values.update({
            name: np.zeros(1, dtype=VAR_DTYPES[name]) for name in SCALAR_OUTPUTS
        })

    def _apply_result(self, result: dict[str, Any]) -> None:
        pareto = list(result.get("pareto") or [])

        def objective(row: dict[str, Any], key: str, index: int, sign: float = 1.0):
            value = row.get(key)
            if value is None:
                values = row.get("F") or []
                if len(values) <= index:
                    raise ValueError(f"Pareto solution lacks {key} and F[{index}]")
                value = sign * float(values[index])
            return float(value)

        self._values["annual_yield_dm_kg"] = np.asarray(
            [objective(row, "yield_dm_kg", 0, -1.0) for row in pareto], dtype=np.float64)
        self._values["net_profit"] = np.asarray(
            [objective(row, "net_profit", 1, -1.0) for row in pareto], dtype=np.float64)
        self._values["soil_interval_violation"] = np.asarray(
            [objective(row, "soil_deviation", 2) for row in pareto], dtype=np.float64)
        self._values["evaluated_candidate_count"][0] = int(result.get("evaluated", 0))
        self._values["failed_candidate_count"][0] = int(result.get("failed", 0))
        self._values["pareto_solution_count"][0] = len(pareto)

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("BMI model is not initialized")

    def _check_var(self, name: str) -> None:
        if name not in self._values:
            raise KeyError(f"Unknown BMI variable: {name}")

    @staticmethod
    def _check_grid(grid_id) -> None:
        if grid_id not in (0, 1):
            raise KeyError(f"Unknown BMI grid: {grid_id}")
