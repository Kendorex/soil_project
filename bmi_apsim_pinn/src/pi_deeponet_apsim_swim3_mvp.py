
"""
MVP DeepONet surrogate for direct comparison with APSIM SWIM3.

Design goals
------------
This implementation intentionally separates:
  1) what is directly supported by the available APSIM/SWIM3 teacher data;
  2) what would require additional SWIM3 internal parameters/state.

The supplied example profile files contain:
  SW(1..L), DUL, SAT, AirDry, LL15, KS, Depth,
  ISoilWater.Infiltration, ISoilWater.Drainage,
  ISoilWater.Runoff, ISoilWater.Es, ISoilWater.Eos,
  PondDepth and WaterStored.

Therefore the MVP learns the daily operator

    [SW_n(z), static soil, layer geometry, forcing history, pond_n]
        -> [SW_{n+1}(z), q_top, q_bottom, runoff, Es, pond_{n+1}, sink_total]

and is trained with:
  * layer-storage-weighted state loss;
  * multi-day autoregressive rollout loss;
  * supervised SWIM3/APSIM boundary-flux losses when fields are available;
  * exact discrete water-balance loss using layer thicknesses;
  * surface partition loss rain ~= q_top + runoff + Es.

Important limitations
---------------------
The example files do NOT contain SWIM3 Kdul, microP, itbc/isbc/ibbc,
surface-conductance parameters or the internal Ross-transformed state p.
For that reason this MVP does NOT impose an approximate Campbell interior
Richards residual: doing so would regularize toward a different constitutive
model than the SWIM3 teacher. The available SWIM3/APSIM fluxes are used instead.

This is the preferred MVP for direct teacher-vs-surrogate comparison.
When Kdul/microP/boundary-mode/internal-step outputs become available, an exact
Watvar/SimpleK/Baleq residual can be added without changing the operator API.

Checkpoint policy
-----------------
Per request, the checkpoint stores the MODEL OBJECT itself, not state_dict.
Loading therefore requires this module/class definition to remain importable.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset


# ============================================================================
# 0. REPRODUCIBILITY
# ============================================================================

SEED = 42
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(SEED)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
EPS = 1.0e-8

# Full-model checkpoints use pickle. When this file is executed as a script,
# register a stable importable module name so the stored model object can later
# be loaded from a notebook via `import pi_deeponet_apsim_swim3_mvp`.
_STABLE_MODULE = "pi_deeponet_apsim_swim3_mvp"
if __name__ == "__main__":
    sys.modules.setdefault(_STABLE_MODULE, sys.modules[__name__])


# ============================================================================
# 1. CONFIGURATION
# ============================================================================

@dataclass
class HybridConfig:
    num_layers: int = 7

    # Units in the supplied example files:
    #   Depth, PondDepth, rain, KS, SWIM3 fluxes are in mm / mm/day.
    depth_to_m: float = 1e-3
    pond_to_m: float = 1e-3
    ks_to_m_day: float = 1e-3
    forcing_to_m_day: float = 1e-3
    flux_to_m_day: float = 1e-3

    dt_days: float = 1.0

    # The forcing window used to predict day n+1 ENDS on day n+1.
    # This is important: next-day rain must be known by the daily APSIM step.
    forcing_history_days: int = 7
    forcing_channels: int = 2  # rain, potential soil evaporation

    rollout_horizon: int = 5
    train_ratio: float = 0.70

    # Architecture.
    state_width: int = 96
    soil_width: int = 96
    forcing_width: int = 96
    surface_width: int = 32
    branch_width: int = 128
    branch_basis: int = 64
    trunk_width: int = 96

    # Training.
    batch_size: int = 128
    num_workers: int = 0
    pretrain_epochs: int = 150
    rollout_epochs: int = 200
    conservation_epochs: int = 75

    lr_pretrain: float = 2e-4
    lr_rollout: float = 1e-4
    lr_conservation: float = 5e-5
    weight_decay: float = 1e-5
    grad_clip: float = 1.0

    # Loss weights.
    field_weight: float = 1.0
    dynamic_weight: float = 0.15
    gradient_weight: float = 0.03
    flux_weight: float = 0.20

    rollout_weight: float = 1.0
    rollout_decay: float = 0.90

    mass_weight: float = 0.10
    surface_balance_weight: float = 0.05
    flux_nonnegative_weight: float = 0.005

    # Scale for the ponding state fed to the Branch.
    pond_scale_m: float = 0.05

    # Dataset paths.
    data_dir: str = "Data"
    weather_file: str = "weather_2004_2024.csv"
    model_dir: str = "Models"
    model_file: str = "pi_deeponet_swim3_mvp.pth"

    def validate(self):
        if self.num_layers < 2:
            raise ValueError("num_layers must be >= 2")
        if self.forcing_history_days < 1:
            raise ValueError("forcing_history_days must be >= 1")
        if self.rollout_horizon < 1:
            raise ValueError("rollout_horizon must be >= 1")
        if not 0.0 < self.train_ratio < 1.0:
            raise ValueError("train_ratio must be in (0,1)")
        if self.dt_days <= 0:
            raise ValueError("dt_days must be > 0")


CFG = HybridConfig()
CFG.validate()

PROFILE_PREFIXES = ("SW", "DUL", "SAT", "AirDry", "LL15", "KS")
DATE_CANDIDATES = (
    "date", "datetime", "timestamp", "time",
    "Date", "DATE", "Datetime", "DATE_TIME", "Clock.Today",
)

FLUX_NAMES = (
    "q_top",       # net surface flux into soil; may be negative under evaporation
    "q_bottom",    # drainage, positive downward
    "runoff",      # positive out of surface
    "evap_actual", # actual soil evaporation
    "pond",        # pond depth, metres (state rather than flux)
    "sink_total",  # closure sink: q_top-q_bottom-dW/dt
)
FLUX_INDEX = {k: i for i, k in enumerate(FLUX_NAMES)}


# ============================================================================
# 2. IO / DATE / COLUMN HELPERS
# ============================================================================

def cols(prefix: str, n: int) -> List[str]:
    return [f"{prefix}({i})" for i in range(1, n + 1)]


def find_column(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[str]:
    lookup = {str(c).strip().lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lookup:
            return lookup[c.lower()]
    return None


def require_columns(df: pd.DataFrame, required: Sequence[str], name: str):
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: missing columns {missing[:20]}")


def infer_date_series(df: pd.DataFrame, name: str) -> pd.Series:
    for c in DATE_CANDIDATES:
        if c in df.columns:
            s = pd.to_datetime(df[c], errors="coerce")
            if s.notna().sum() >= max(3, int(0.8 * len(df))):
                return s.dt.normalize()

    lower = {str(c).strip().lower(): c for c in df.columns}

    if all(x in lower for x in ("year", "month", "day")):
        s = pd.to_datetime(
            {
                "year": pd.to_numeric(df[lower["year"]], errors="coerce"),
                "month": pd.to_numeric(df[lower["month"]], errors="coerce"),
                "day": pd.to_numeric(df[lower["day"]], errors="coerce"),
            },
            errors="coerce",
        )
        return s.dt.normalize()

    # Common APSIM weather: year + day-of-year.
    if "year" in lower and "day" in lower:
        year = pd.to_numeric(df[lower["year"]], errors="coerce")
        doy = pd.to_numeric(df[lower["day"]], errors="coerce")
        s = pd.to_datetime(year.astype("Int64").astype(str), format="%Y", errors="coerce")
        s = s + pd.to_timedelta(doy - 1, unit="D")
        return pd.Series(s).dt.normalize()

    raise ValueError(f"{name}: could not identify a date column")


def load_weather(path_str: str, data_dir: str) -> pd.DataFrame:
    path = Path(path_str)
    if not path.exists():
        candidate = Path(data_dir) / path_str
        if candidate.exists():
            path = candidate
    if not path.exists():
        raise FileNotFoundError(path)

    df = pd.read_csv(path).reset_index(drop=True)
    df["__date__"] = infer_date_series(df, path.name)
    if df["__date__"].isna().any():
        raise ValueError(f"{path.name}: invalid dates")
    return (
        df.sort_values("__date__")
        .drop_duplicates("__date__", keep="last")
        .reset_index(drop=True)
    )


def load_profile_csvs(data_dir: str, cfg: HybridConfig):
    root = Path(data_dir)
    required = []
    for p in PROFILE_PREFIXES:
        required.extend(cols(p, cfg.num_layers))

    out = []
    for path in sorted(root.glob("*.csv")):
        try:
            df = pd.read_csv(path)
            require_columns(df, required, path.name)
        except Exception:
            # Weather and unrelated CSVs are allowed in the same directory.
            continue

        df = df.reset_index(drop=True).copy()
        df["__date__"] = infer_date_series(df, path.name)
        if df["__date__"].isna().any():
            raise ValueError(f"{path.name}: invalid dates")

        min_rows = cfg.forcing_history_days + cfg.rollout_horizon + 2
        if len(df) >= min_rows:
            out.append((path.name, df))

    if not out:
        raise ValueError(f"No usable APSIM profile CSV files in {root.resolve()}")
    return out


def split_timewise(datasets, ratio: float, cfg: HybridConfig):
    train, test = [], []
    min_rows = cfg.forcing_history_days + cfg.rollout_horizon + 2

    for name, df in datasets:
        cut = int(len(df) * ratio)
        if cut < min_rows or len(df) - cut < min_rows:
            continue

        # Strict split: no target row appears in both sets.
        train.append((name, df.iloc[:cut].reset_index(drop=True)))
        test.append((name, df.iloc[cut:].reset_index(drop=True)))

    if not train or not test:
        raise ValueError("No profile has enough rows for the requested time split")
    return train, test


def extract_geometry(df: pd.DataFrame, cfg: HybridConfig):
    """Return cumulative bottoms, thicknesses, centres, z_hat in metres."""
    depth_cols = cols("Depth", cfg.num_layers)
    if all(c in df.columns for c in depth_cols):
        bottom = df[depth_cols].iloc[0].to_numpy(np.float64) * cfg.depth_to_m
    else:
        raise ValueError(
            "MVP requires Depth(1..L) so storage and flux conservation use "
            "actual finite-volume thicknesses."
        )

    if np.any(~np.isfinite(bottom)) or np.any(np.diff(bottom) <= 0) or bottom[0] <= 0:
        raise ValueError("Depth columns must be finite, positive cumulative layer bottoms")

    # Require constant geometry inside one simulation/profile file.
    geom = df[depth_cols].to_numpy(np.float64) * cfg.depth_to_m
    if not np.allclose(geom, bottom[None, :], rtol=0, atol=1e-8):
        raise ValueError("Layer geometry changes within a profile file; unsupported in this MVP")

    top = np.concatenate([[0.0], bottom[:-1]])
    thickness = bottom - top
    centres = 0.5 * (top + bottom)
    z_hat = centres / bottom[-1]
    return (
        bottom.astype(np.float32),
        thickness.astype(np.float32),
        centres.astype(np.float32),
        z_hat.astype(np.float32),
    )


# ============================================================================
# 3. FORCING AND SWIM3/APSIM BOUNDARY TARGETS
# ============================================================================

def _weather_date_map(weather: pd.DataFrame):
    wd = pd.DatetimeIndex(weather["__date__"])
    return {pd.Timestamp(d): i for i, d in enumerate(wd)}


def forcing_for_profile(
    df: pd.DataFrame,
    weather: pd.DataFrame,
    cfg: HybridConfig,
) -> Tuple[np.ndarray, str]:
    """Return [N,2] = rain, potential soil evaporation in m/day.

    Evaporation priority:
      1) weather PET/ET0-like column if present;
      2) APSIM ISoilWater.Eos (potential soil evaporation);
      3) APSIM ISoilWater.Eo;
      4) zeros, with an explicit source label.
    """
    wmap = _weather_date_map(weather)
    rain_col = find_column(
        weather, ["rain", "precip", "precipitation", "rainfall", "P"]
    )
    if rain_col is None:
        raise ValueError("Weather must contain rain/precipitation")

    evap_weather_col = find_column(
        weather,
        ["evap", "et0", "eto", "pet", "potential_evaporation", "ETP"],
    )

    rain_weather = pd.to_numeric(weather[rain_col], errors="coerce").to_numpy(np.float64)
    evap_weather = (
        None
        if evap_weather_col is None
        else pd.to_numeric(weather[evap_weather_col], errors="coerce")
        .fillna(0.0)
        .to_numpy(np.float64)
    )

    dates = pd.DatetimeIndex(df["__date__"])
    idx = np.array([wmap.get(pd.Timestamp(d), -1) for d in dates], dtype=np.int64)
    if np.any(idx < 0):
        missing = dates[idx < 0][:5]
        raise ValueError(f"Missing weather for profile dates: {list(missing)}")

    rain = np.maximum(rain_weather[idx], 0.0) * cfg.forcing_to_m_day

    if evap_weather is not None:
        evap = np.maximum(evap_weather[idx], 0.0) * cfg.forcing_to_m_day
        source = f"weather:{evap_weather_col}"
    elif "ISoilWater.Eos" in df.columns:
        evap = (
            np.maximum(
                pd.to_numeric(df["ISoilWater.Eos"], errors="coerce")
                .fillna(0.0)
                .to_numpy(np.float64),
                0.0,
            )
            * cfg.flux_to_m_day
        )
        source = "profile:ISoilWater.Eos"
    elif "ISoilWater.Eo" in df.columns:
        evap = (
            np.maximum(
                pd.to_numeric(df["ISoilWater.Eo"], errors="coerce")
                .fillna(0.0)
                .to_numpy(np.float64),
                0.0,
            )
            * cfg.flux_to_m_day
        )
        source = "profile:ISoilWater.Eo"
    else:
        evap = np.zeros_like(rain)
        source = "zeros:no-PET-column"

    return np.stack([rain, evap], axis=1).astype(np.float32), source


def _first_numeric_column(df: pd.DataFrame, names: Sequence[str]):
    c = find_column(df, names)
    if c is None:
        return None
    return pd.to_numeric(df[c], errors="coerce").to_numpy(np.float64)


def boundary_targets_for_profile(
    df: pd.DataFrame,
    thickness_m: np.ndarray,
    cfg: HybridConfig,
):
    """Build physical teacher targets [N,6] and availability mask [N,6].

    sink_total is inferred from the exact storage balance when q_top/q_bottom
    and consecutive SW states are available:
        sink_total = q_top - q_bottom - dW/dt.
    Positive sink_total means water removed from the soil by an unrepresented
    process (e.g. plant uptake).
    """
    n = len(df)
    out = np.full((n, len(FLUX_NAMES)), np.nan, dtype=np.float64)

    qtop = _first_numeric_column(
        df, ["ISoilWater.Infiltration", "Infiltration"]
    )
    qbot = _first_numeric_column(
        df, ["ISoilWater.Drainage", "Drainage"]
    )
    runoff = _first_numeric_column(
        df, ["ISoilWater.Runoff", "Runoff"]
    )
    evap = _first_numeric_column(
        df, ["ISoilWater.Es", "Es"]
    )
    pond = _first_numeric_column(
        df, ["PondDepth", "ISoilWater.PondDepth"]
    )

    if qtop is not None:
        out[:, FLUX_INDEX["q_top"]] = qtop * cfg.flux_to_m_day
    if qbot is not None:
        out[:, FLUX_INDEX["q_bottom"]] = qbot * cfg.flux_to_m_day
    if runoff is not None:
        out[:, FLUX_INDEX["runoff"]] = runoff * cfg.flux_to_m_day
    if evap is not None:
        out[:, FLUX_INDEX["evap_actual"]] = evap * cfg.flux_to_m_day
    if pond is not None:
        out[:, FLUX_INDEX["pond"]] = pond * cfg.pond_to_m

    sw = df[cols("SW", cfg.num_layers)].to_numpy(np.float64)
    storage = np.sum(sw * thickness_m[None, :], axis=1)  # m water

    if qtop is not None and qbot is not None:
        qt = out[:, FLUX_INDEX["q_top"]]
        qb = out[:, FLUX_INDEX["q_bottom"]]
        dwdt = np.full(n, np.nan, dtype=np.float64)
        dwdt[1:] = np.diff(storage) / cfg.dt_days
        out[:, FLUX_INDEX["sink_total"]] = qt - qb - dwdt

    mask = np.isfinite(out)
    return out.astype(np.float32), mask


# ============================================================================
# 4. NORMALIZATION
# ============================================================================

@dataclass
class NormalizationStats:
    soil_mean: np.ndarray       # [6] DUL,SAT,AirDry,LL15,logKS,thickness
    soil_std: np.ndarray        # [6]
    forcing_mean: np.ndarray    # [2]
    forcing_std: np.ndarray     # [2]
    flux_mean: np.ndarray       # [6]
    flux_std: np.ndarray        # [6]
    delta_scale: np.ndarray     # [L]

    def to_dict(self):
        return {
            "soil_mean": self.soil_mean.tolist(),
            "soil_std": self.soil_std.tolist(),
            "forcing_mean": self.forcing_mean.tolist(),
            "forcing_std": self.forcing_std.tolist(),
            "flux_mean": self.flux_mean.tolist(),
            "flux_std": self.flux_std.tolist(),
            "delta_scale": self.delta_scale.tolist(),
        }

    @classmethod
    def from_dict(cls, d):
        return cls(**{
            k: np.asarray(v, dtype=np.float32)
            for k, v in d.items()
        })


def static_soil_array(df: pd.DataFrame, thickness_m: np.ndarray, cfg: HybridConfig):
    dul = df[cols("DUL", cfg.num_layers)].to_numpy(np.float64)
    sat = df[cols("SAT", cfg.num_layers)].to_numpy(np.float64)
    air = df[cols("AirDry", cfg.num_layers)].to_numpy(np.float64)
    ll15 = df[cols("LL15", cfg.num_layers)].to_numpy(np.float64)
    ks = df[cols("KS", cfg.num_layers)].to_numpy(np.float64) * cfg.ks_to_m_day
    logks = np.log10(np.clip(np.abs(ks), 1e-12, None))
    thick = np.broadcast_to(thickness_m[None, :], dul.shape)
    return np.stack([dul, sat, air, ll15, logks, thick], axis=-1).astype(np.float32)


def compute_train_stats(train_datasets, weather, cfg: HybridConfig):
    soil_rows = []
    forcing_rows = []
    flux_rows = []
    deltas = []

    evap_sources = set()

    for name, df in train_datasets:
        _, thickness, _, _ = extract_geometry(df, cfg)
        soil = static_soil_array(df, thickness, cfg)
        forcing, esrc = forcing_for_profile(df, weather, cfg)
        flux, mask = boundary_targets_for_profile(df, thickness, cfg)

        evap_sources.add(esrc)
        soil_rows.append(soil.reshape(-1, soil.shape[-1]))
        forcing_rows.append(forcing)

        for j in range(flux.shape[1]):
            vals = flux[:, j][mask[:, j]]
            if len(vals):
                flux_rows.append((j, vals))

        sw = df[cols("SW", cfg.num_layers)].to_numpy(np.float64)
        if len(sw) > 1:
            deltas.append(np.diff(sw, axis=0))

    soil_all = np.concatenate(soil_rows, axis=0)
    forcing_all = np.concatenate(forcing_rows, axis=0)

    sm = np.nanmean(soil_all, axis=0)
    ss = np.nanstd(soil_all, axis=0)
    fm = np.nanmean(forcing_all, axis=0)
    fs = np.nanstd(forcing_all, axis=0)

    flux_mean = np.zeros(len(FLUX_NAMES), dtype=np.float64)
    flux_std = np.ones(len(FLUX_NAMES), dtype=np.float64)
    for j in range(len(FLUX_NAMES)):
        chunks = [v for jj, v in flux_rows if jj == j]
        if chunks:
            v = np.concatenate(chunks)
            flux_mean[j] = np.nanmean(v)
            flux_std[j] = np.nanstd(v)

    delta_all = np.concatenate(deltas, axis=0)
    delta_scale = np.nanstd(delta_all, axis=0)

    ss = np.maximum(ss, 1e-6)
    fs = np.maximum(fs, 1e-8)
    flux_std = np.maximum(flux_std, 1e-6)
    delta_scale = np.maximum(delta_scale, 5e-4)

    print("Evaporation forcing sources:", sorted(evap_sources))

    return NormalizationStats(
        soil_mean=sm.astype(np.float32),
        soil_std=ss.astype(np.float32),
        forcing_mean=fm.astype(np.float32),
        forcing_std=fs.astype(np.float32),
        flux_mean=flux_mean.astype(np.float32),
        flux_std=flux_std.astype(np.float32),
        delta_scale=delta_scale.astype(np.float32),
    )


# ============================================================================
# 5. VECTORIZED ROLLOUT DATASET
# ============================================================================

class RolloutDataset(Dataset):
    """Tensor-backed rollout dataset; no per-sample pandas work in __getitem__."""

    def __init__(self, arrays: Dict[str, np.ndarray]):
        if not arrays or len(arrays["sw0"]) == 0:
            raise ValueError("Rollout dataset is empty")

        self.arrays = {}
        bool_keys = {"flux_mask"}

        for k, v in arrays.items():
            if k in bool_keys:
                self.arrays[k] = torch.from_numpy(np.asarray(v, dtype=np.bool_))
            else:
                self.arrays[k] = torch.from_numpy(np.asarray(v, dtype=np.float32))

    @classmethod
    def build(cls, datasets, weather, cfg: HybridConfig, stats: NormalizationStats):
        pieces = {
            "sw0": [],
            "soil": [],
            "forcing": [],
            "forcing_raw": [],
            "future_forcing": [],
            "future_forcing_raw": [],
            "targets": [],
            "flux_targets": [],
            "flux_targets_norm": [],
            "flux_mask": [],
            "z_hat": [],
            "z_phys": [],
            "thickness": [],
            "theta_s": [],
            "surface": [],
        }

        H = cfg.forcing_history_days
        R = cfg.rollout_horizon
        L = cfg.num_layers

        for dataset_id, (name, df) in enumerate(datasets):
            print(f"Building rollout dataset {dataset_id + 1}/{len(datasets)}: {name}")

            _, thickness, centres, z_hat = extract_geometry(df, cfg)
            forcing_raw_all, _ = forcing_for_profile(df, weather, cfg)
            flux_raw_all, flux_mask_all = boundary_targets_for_profile(df, thickness, cfg)

            sw_all = df[cols("SW", L)].to_numpy(np.float32)
            sat_all = df[cols("SAT", L)].to_numpy(np.float32)
            soil_raw_all = static_soil_array(df, thickness, cfg)

            n = len(df)

            # State is at end of day i. To predict end of day i+1, the forcing
            # window MUST end on day i+1.
            start_min = max(0, H - 2)
            starts = np.arange(start_min, n - R, dtype=np.int64)
            if len(starts) == 0:
                continue

            # H forcing rows: i-H+2 ... i+1.
            hist_offsets = np.arange(-H + 2, 2, dtype=np.int64)
            hist_idx = starts[:, None] + hist_offsets[None, :]

            target_idx = starts[:, None] + np.arange(1, R + 1, dtype=np.int64)[None, :]

            if R > 1:
                future_idx = starts[:, None] + np.arange(2, R + 1, dtype=np.int64)[None, :]
            else:
                future_idx = np.empty((len(starts), 0), dtype=np.int64)

            # Finite mask across all essential state/static/forcing/target rows.
            essential = np.concatenate(
                [
                    sw_all,
                    soil_raw_all.reshape(n, -1),
                    forcing_raw_all,
                ],
                axis=1,
            )
            finite_row = np.all(np.isfinite(essential), axis=1)

            valid = (
                np.all(finite_row[hist_idx], axis=1)
                & finite_row[starts]
                & np.all(finite_row[target_idx], axis=1)
            )
            if not np.any(valid):
                continue

            starts = starts[valid]
            hist_idx = hist_idx[valid]
            target_idx = target_idx[valid]
            future_idx = future_idx[valid] if R > 1 else future_idx

            sw0 = sw_all[starts]
            theta_s = sat_all[starts]
            soil_raw = soil_raw_all[starts]
            soil_norm = (soil_raw - stats.soil_mean[None, None, :]) / stats.soil_std[None, None, :]

            forcing_raw = forcing_raw_all[hist_idx]
            forcing = (
                forcing_raw - stats.forcing_mean[None, None, :]
            ) / stats.forcing_std[None, None, :]

            if R > 1:
                ff_raw = forcing_raw_all[future_idx]
                ff = (
                    ff_raw - stats.forcing_mean[None, None, :]
                ) / stats.forcing_std[None, None, :]
            else:
                ff_raw = np.empty((len(starts), 0, 2), dtype=np.float32)
                ff = ff_raw.copy()

            targets = sw_all[target_idx]
            flux_targets = flux_raw_all[target_idx]
            flux_mask = flux_mask_all[target_idx]
            flux_norm = (
                flux_targets - stats.flux_mean[None, None, :]
            ) / stats.flux_std[None, None, :]
            flux_norm = np.where(flux_mask, flux_norm, 0.0)

            # Surface state at current day n.
            pond0 = flux_raw_all[starts, FLUX_INDEX["pond"]]
            pond0 = np.where(np.isfinite(pond0), pond0, 0.0).reshape(-1, 1)

            pieces["sw0"].append(sw0)
            pieces["soil"].append(soil_norm.astype(np.float32))
            pieces["forcing"].append(forcing.astype(np.float32))
            pieces["forcing_raw"].append(forcing_raw.astype(np.float32))
            pieces["future_forcing"].append(ff.astype(np.float32))
            pieces["future_forcing_raw"].append(ff_raw.astype(np.float32))
            pieces["targets"].append(targets.astype(np.float32))
            pieces["flux_targets"].append(flux_targets.astype(np.float32))
            pieces["flux_targets_norm"].append(flux_norm.astype(np.float32))
            pieces["flux_mask"].append(flux_mask.astype(np.bool_))
            pieces["z_hat"].append(np.broadcast_to(z_hat, (len(starts), L)).copy())
            pieces["z_phys"].append(np.broadcast_to(centres, (len(starts), L)).copy())
            pieces["thickness"].append(np.broadcast_to(thickness, (len(starts), L)).copy())
            pieces["theta_s"].append(theta_s.astype(np.float32))
            pieces["surface"].append(pond0.astype(np.float32))

        arrays = {}
        for k, chunks in pieces.items():
            if not chunks:
                raise ValueError(f"No data produced for field {k}")
            arrays[k] = np.concatenate(chunks, axis=0)

        return cls(arrays)

    def __len__(self):
        return self.arrays["sw0"].shape[0]

    def __getitem__(self, i):
        return {k: v[i] for k, v in self.arrays.items()}


def make_loader(ds, cfg, shuffle):
    kwargs = dict(
        dataset=ds,
        batch_size=cfg.batch_size,
        shuffle=shuffle,
        drop_last=False,
        num_workers=cfg.num_workers,
        pin_memory=torch.cuda.is_available(),
    )
    if cfg.num_workers > 0:
        kwargs["persistent_workers"] = True
    return DataLoader(**kwargs)


# ============================================================================
# 6. MODEL
# ============================================================================

class StateEncoder(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(2 * cfg.num_layers, cfg.state_width),
            nn.GELU(),
            nn.Linear(cfg.state_width, cfg.state_width),
            nn.GELU(),
        )

    def forward(self, sw, sat, z_hat):
        se = torch.clamp(sw / torch.clamp(sat, min=1e-5), 0.0, 1.0)
        x = torch.cat([se, z_hat], dim=1)
        return self.net(x)


class SoilEncoder(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        # 6 normalized soil/geometry features + z_hat.
        self.net = nn.Sequential(
            nn.Linear((6 + 1) * cfg.num_layers, cfg.soil_width),
            nn.GELU(),
            nn.Linear(cfg.soil_width, cfg.soil_width),
            nn.GELU(),
        )

    def forward(self, soil, z_hat):
        x = torch.cat([soil, z_hat.unsqueeze(-1)], dim=-1)
        return self.net(x.reshape(x.shape[0], -1))


class ForcingEncoder(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        H = cfg.forcing_history_days
        W = cfg.forcing_width
        self.conv = nn.Sequential(
            nn.Conv1d(2, W, kernel_size=3, padding=1),
            nn.GELU(),
            nn.Conv1d(W, W, kernel_size=3, padding=1),
            nn.GELU(),
        )
        self.norm = nn.LayerNorm(W * H)
        self.proj = nn.Sequential(
            nn.Linear(W * H, W),
            nn.GELU(),
        )

    def forward(self, forcing):
        x = self.conv(forcing.transpose(1, 2))
        x = self.norm(x.flatten(1))
        return self.proj(x)


class SurfaceEncoder(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        self.scale = cfg.pond_scale_m
        self.net = nn.Sequential(
            nn.Linear(1, cfg.surface_width),
            nn.GELU(),
            nn.Linear(cfg.surface_width, cfg.surface_width),
            nn.GELU(),
        )

    def forward(self, pond):
        return self.net(pond / max(self.scale, 1e-6))


class BranchNet(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        self.state = StateEncoder(cfg)
        self.soil = SoilEncoder(cfg)
        self.forcing = ForcingEncoder(cfg)
        self.surface = SurfaceEncoder(cfg)

        in_dim = (
            cfg.state_width
            + cfg.soil_width
            + cfg.forcing_width
            + cfg.surface_width
        )

        self.fusion = nn.Sequential(
            nn.Linear(in_dim, cfg.branch_width),
            nn.GELU(),
            nn.Linear(cfg.branch_width, cfg.branch_width),
            nn.GELU(),
        )
        self.basis_head = nn.Linear(cfg.branch_width, cfg.branch_basis)
        self.flux_head = nn.Linear(cfg.branch_width, len(FLUX_NAMES))

    def forward(self, soil, forcing, sw, sat, z_hat, surface):
        h = torch.cat(
            [
                self.state(sw, sat, z_hat),
                self.soil(soil, z_hat),
                self.forcing(forcing),
                self.surface(surface),
            ],
            dim=1,
        )
        h = self.fusion(h)
        return self.basis_head(h), self.flux_head(h)


class TrunkNet(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(1, cfg.trunk_width),
            nn.Tanh(),
            nn.Linear(cfg.trunk_width, cfg.trunk_width),
            nn.Tanh(),
            nn.Linear(cfg.trunk_width, cfg.trunk_width),
            nn.Tanh(),
            nn.Linear(cfg.trunk_width, cfg.branch_basis),
        )

    def forward(self, z_hat):
        return self.net(z_hat)


class HybridPIDeepONet(nn.Module):
    """One-day DeepONet + supervised SWIM3/APSIM boundary-flux heads."""

    def __init__(self, cfg: HybridConfig, stats: NormalizationStats):
        super().__init__()
        self.cfg = cfg
        self.branch = BranchNet(cfg)
        self.trunk = TrunkNet(cfg)
        self.bias = nn.Parameter(torch.zeros(1))

        self.register_buffer(
            "flux_mean",
            torch.tensor(stats.flux_mean, dtype=torch.float32),
        )
        self.register_buffer(
            "flux_std",
            torch.tensor(stats.flux_std, dtype=torch.float32),
        )

    def denormalize_flux(self, flux_norm):
        return flux_norm * self.flux_std + self.flux_mean

    def forward(self, soil, forcing, sw0, theta_s, z_hat, surface):
        """
        soil     [B,L,6] normalized static soil/geometry features
        forcing  [B,H,2] normalized, ending on the TARGET day
        sw0      [B,L] current end-of-day water content
        theta_s  [B,L]
        z_hat    [B,L] layer-centre coordinate / profile depth
        surface  [B,1] current pond depth [m]
        """
        basis, flux_norm = self.branch(
            soil, forcing, sw0, theta_s, z_hat, surface
        )

        B, L = z_hat.shape
        trunk = self.trunk(z_hat.reshape(-1, 1)).reshape(B, L, -1)
        raw = (basis[:, None, :] * trunk).sum(-1) + self.bias

        # Residual update in relative-saturation logit space.
        # Bounds are 0..SAT, matching the SWIM3 retention curve's dry limit,
        # not AirDry (the supplied data can contain SW below AirDry).
        se0 = torch.clamp(
            sw0 / torch.clamp(theta_s, min=1e-5),
            1e-3,
            1.0 - 1e-3,
        )
        logit0 = torch.log(se0) - torch.log1p(-se0)
        se1 = torch.sigmoid(logit0 + raw)
        theta_next = theta_s * se1

        flux_phys = self.denormalize_flux(flux_norm)
        return theta_next, flux_norm, flux_phys


# ============================================================================
# 7. LOSSES
# ============================================================================

def state_loss(
    pred,
    target,
    sw_prev,
    thickness,
    z_phys,
    delta_scale,
    cfg,
):
    """Storage-aware field + scaled dynamics + profile-gradient loss."""
    w = thickness / torch.clamp(thickness.sum(dim=1, keepdim=True), min=EPS)

    field_point = F.smooth_l1_loss(pred, target, reduction="none")
    field = (field_point * w).sum(dim=1).mean()

    # Unlike pred-sw0 vs target-sw0 without scaling, this term changes the
    # geometry of the objective by scaling each layer by its typical daily
    # change. It emphasizes dynamically quiet/deep layers appropriately.
    ds = delta_scale[None, :]
    dyn = F.smooth_l1_loss(
        (pred - sw_prev) / ds,
        (target - sw_prev) / ds,
    )

    dz = torch.clamp(z_phys[:, 1:] - z_phys[:, :-1], min=1e-6)
    gp = (pred[:, 1:] - pred[:, :-1]) / dz
    gt = (target[:, 1:] - target[:, :-1]) / dz
    grad = F.smooth_l1_loss(gp, gt)

    total = (
        cfg.field_weight * field
        + cfg.dynamic_weight * dyn
        + cfg.gradient_weight * grad
    )
    return total, field, dyn, grad


def masked_flux_loss(pred_norm, target_norm, mask):
    """Huber loss over only teacher flux/state fields present in the CSV."""
    if not torch.any(mask):
        return pred_norm.new_zeros(())
    point = F.smooth_l1_loss(pred_norm, target_norm, reduction="none")
    weight = mask.to(point.dtype)
    return (point * weight).sum() / torch.clamp(weight.sum(), min=1.0)


def water_balance_loss(
    theta_prev,
    theta_next,
    flux_phys,
    thickness,
    dt_days,
):
    """
    Exact finite-volume daily balance for the outputs available in the sample:
        dW/dt = q_top - q_bottom - sink_total
    """
    dWdt = torch.sum((theta_next - theta_prev) * thickness, dim=1) / dt_days

    qtop = flux_phys[:, FLUX_INDEX["q_top"]]
    qbot = flux_phys[:, FLUX_INDEX["q_bottom"]]
    sink = flux_phys[:, FLUX_INDEX["sink_total"]]

    residual = dWdt - (qtop - qbot - sink)

    # Scale in m/day; 1 mm/day is a useful numerical reference.
    scale = 1e-3
    return F.smooth_l1_loss(
        residual / scale,
        torch.zeros_like(residual),
    )


def surface_partition_loss(flux_phys, forcing_raw):
    """
    In the supplied no-plant SWIM3/APSIM data:
        rain = net_infiltration + runoff + actual_soil_evap
    to numerical precision.
    """
    rain = forcing_raw[:, -1, 0]
    qtop = flux_phys[:, FLUX_INDEX["q_top"]]
    runoff = flux_phys[:, FLUX_INDEX["runoff"]]
    evap = flux_phys[:, FLUX_INDEX["evap_actual"]]
    residual = rain - (qtop + runoff + evap)
    return F.smooth_l1_loss(
        residual / 1e-3,
        torch.zeros_like(residual),
    )


def nonnegative_flux_loss(flux_phys):
    idx = [
        FLUX_INDEX["q_bottom"],
        FLUX_INDEX["runoff"],
        FLUX_INDEX["evap_actual"],
        FLUX_INDEX["pond"],
    ]
    x = flux_phys[:, idx]
    return torch.mean(F.relu(-x).square())


# ============================================================================
# 8. TRAINING / ROLLOUT
# ============================================================================

def move_batch(batch, device=DEVICE):
    return {
        k: v.to(device, non_blocking=True) if torch.is_tensor(v) else v
        for k, v in batch.items()
    }


def _step(
    model,
    soil,
    forcing,
    forcing_raw,
    sw,
    theta_s,
    z_hat,
    surface,
):
    pred, flux_norm, flux_phys = model(
        soil, forcing, sw, theta_s, z_hat, surface
    )
    # Update surface state for next autoregressive step.
    pond_next = torch.clamp(
        flux_phys[:, FLUX_INDEX["pond"]:FLUX_INDEX["pond"] + 1],
        min=0.0,
    )
    return pred, flux_norm, flux_phys, pond_next


@torch.no_grad()
def evaluate_one_step(model, loader, cfg):
    model.eval()
    mse = 0.0
    mae = 0.0
    n = 0
    for batch in loader:
        batch = move_batch(batch)
        pred, _, _, _ = _step(
            model,
            batch["soil"],
            batch["forcing"],
            batch["forcing_raw"],
            batch["sw0"],
            batch["theta_s"],
            batch["z_hat"],
            batch["surface"],
        )
        target = batch["targets"][:, 0]
        bs = pred.shape[0]
        mse += F.mse_loss(pred, target).item() * bs
        mae += torch.mean(torch.abs(pred - target)).item() * bs
        n += bs
    return mse / max(n, 1), mae / max(n, 1)


@torch.no_grad()
def evaluate_rollout(model, loader, cfg):
    model.eval()
    mse = 0.0
    mae = 0.0
    n = 0

    for batch in loader:
        batch = move_batch(batch)
        sw = batch["sw0"]
        forcing = batch["forcing"]
        forcing_raw = batch["forcing_raw"]
        surface = batch["surface"]

        for k in range(cfg.rollout_horizon):
            pred, _, _, surface = _step(
                model,
                batch["soil"],
                forcing,
                forcing_raw,
                sw,
                batch["theta_s"],
                batch["z_hat"],
                surface,
            )
            target = batch["targets"][:, k]
            bs = pred.shape[0]
            mse += F.mse_loss(pred, target).item() * bs
            mae += torch.mean(torch.abs(pred - target)).item() * bs
            n += bs

            sw = pred

            if k + 1 < cfg.rollout_horizon:
                nxt = batch["future_forcing"][:, k:k + 1]
                nxt_raw = batch["future_forcing_raw"][:, k:k + 1]
                forcing = torch.cat([forcing[:, 1:], nxt], dim=1)
                forcing_raw = torch.cat([forcing_raw[:, 1:], nxt_raw], dim=1)

    return mse / max(n, 1), mae / max(n, 1)


def train_pretrain(model, train_loader, val_loader, stats, cfg, history):
    opt = torch.optim.AdamW(
        model.parameters(),
        lr=cfg.lr_pretrain,
        weight_decay=cfg.weight_decay,
    )
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt,
        T_max=max(cfg.pretrain_epochs, 1),
        eta_min=cfg.lr_pretrain * 0.05,
    )
    delta_scale = torch.tensor(stats.delta_scale, device=DEVICE)

    for epoch in range(1, cfg.pretrain_epochs + 1):
        model.train()
        sums = np.zeros(6, dtype=np.float64)
        count = 0

        for batch in train_loader:
            batch = move_batch(batch)
            pred, flux_norm, _, _ = _step(
                model,
                batch["soil"],
                batch["forcing"],
                batch["forcing_raw"],
                batch["sw0"],
                batch["theta_s"],
                batch["z_hat"],
                batch["surface"],
            )

            target = batch["targets"][:, 0]
            sl, field, dyn, grad = state_loss(
                pred,
                target,
                batch["sw0"],
                batch["thickness"],
                batch["z_phys"],
                delta_scale,
                cfg,
            )
            fl = masked_flux_loss(
                flux_norm,
                batch["flux_targets_norm"][:, 0],
                batch["flux_mask"][:, 0],
            )
            total = sl + cfg.flux_weight * fl

            opt.zero_grad(set_to_none=True)
            total.backward()
            nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
            opt.step()

            bs = pred.shape[0]
            count += bs
            sums += np.array(
                [total.item(), field.item(), dyn.item(), grad.item(), fl.item(), 0.0]
            ) * bs

        sched.step()
        vmse, vmae = evaluate_one_step(model, val_loader, cfg)
        m = sums / max(count, 1)
        history["total"].append(float(m[0]))
        history["state"].append(float(m[1]))
        history["dynamic"].append(float(m[2]))
        history["gradient"].append(float(m[3]))
        history["flux"].append(float(m[4]))
        history["physics"].append(0.0)
        history["val"].append(float(vmse))

        if epoch == 1 or epoch % 25 == 0:
            print(
                f"[pretrain] {epoch:4d} "
                f"total={m[0]:.4e} state={m[1]:.4e} "
                f"dyn={m[2]:.4e} flux={m[4]:.4e} "
                f"val_mse={vmse:.4e} val_mae={vmae:.4e}"
            )


def train_rollout(model, train_loader, val_loader, stats, cfg, history):
    opt = torch.optim.AdamW(
        model.parameters(),
        lr=cfg.lr_rollout,
        weight_decay=cfg.weight_decay,
    )
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt,
        T_max=max(cfg.rollout_epochs, 1),
        eta_min=cfg.lr_rollout * 0.05,
    )
    delta_scale = torch.tensor(stats.delta_scale, device=DEVICE)
    norm = sum(cfg.rollout_decay ** k for k in range(cfg.rollout_horizon))

    for epoch in range(1, cfg.rollout_epochs + 1):
        model.train()
        total_sum = state_sum = flux_sum = 0.0
        count = 0

        for batch in train_loader:
            batch = move_batch(batch)
            sw = batch["sw0"]
            forcing = batch["forcing"]
            forcing_raw = batch["forcing_raw"]
            surface = batch["surface"]

            roll_state = sw.new_zeros(())
            roll_flux = sw.new_zeros(())

            for k in range(cfg.rollout_horizon):
                pred, flux_norm, _, surface = _step(
                    model,
                    batch["soil"],
                    forcing,
                    forcing_raw,
                    sw,
                    batch["theta_s"],
                    batch["z_hat"],
                    surface,
                )

                sl, _, _, _ = state_loss(
                    pred,
                    batch["targets"][:, k],
                    sw,
                    batch["thickness"],
                    batch["z_phys"],
                    delta_scale,
                    cfg,
                )
                fl = masked_flux_loss(
                    flux_norm,
                    batch["flux_targets_norm"][:, k],
                    batch["flux_mask"][:, k],
                )

                wk = cfg.rollout_decay ** k
                roll_state = roll_state + wk * sl
                roll_flux = roll_flux + wk * fl

                sw = pred
                if k + 1 < cfg.rollout_horizon:
                    forcing = torch.cat(
                        [forcing[:, 1:], batch["future_forcing"][:, k:k + 1]],
                        dim=1,
                    )
                    forcing_raw = torch.cat(
                        [
                            forcing_raw[:, 1:],
                            batch["future_forcing_raw"][:, k:k + 1],
                        ],
                        dim=1,
                    )

            roll_state = roll_state / norm
            roll_flux = roll_flux / norm
            total = cfg.rollout_weight * roll_state + cfg.flux_weight * roll_flux

            opt.zero_grad(set_to_none=True)
            total.backward()
            nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
            opt.step()

            bs = batch["sw0"].shape[0]
            count += bs
            total_sum += total.item() * bs
            state_sum += roll_state.item() * bs
            flux_sum += roll_flux.item() * bs

        sched.step()
        vmse, vmae = evaluate_rollout(model, val_loader, cfg)
        history["total"].append(total_sum / max(count, 1))
        history["state"].append(state_sum / max(count, 1))
        history["dynamic"].append(0.0)
        history["gradient"].append(0.0)
        history["flux"].append(flux_sum / max(count, 1))
        history["physics"].append(0.0)
        history["val"].append(vmse)

        if epoch == 1 or epoch % 25 == 0:
            print(
                f"[rollout] {epoch:4d} "
                f"total={total_sum/max(count,1):.4e} "
                f"state={state_sum/max(count,1):.4e} "
                f"flux={flux_sum/max(count,1):.4e} "
                f"val_mse={vmse:.4e} val_mae={vmae:.4e}"
            )


def train_conservation(model, train_loader, val_loader, stats, cfg, history):
    """Fine-tune with measured SWIM3/APSIM boundary fluxes + exact storage."""
    opt = torch.optim.AdamW(
        model.parameters(),
        lr=cfg.lr_conservation,
        weight_decay=cfg.weight_decay,
    )
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt,
        T_max=max(cfg.conservation_epochs, 1),
        eta_min=cfg.lr_conservation * 0.1,
    )
    delta_scale = torch.tensor(stats.delta_scale, device=DEVICE)

    for epoch in range(1, cfg.conservation_epochs + 1):
        model.train()
        sums = np.zeros(5, dtype=np.float64)
        count = 0

        ramp = min(1.0, epoch / max(1, cfg.conservation_epochs // 2))

        for batch in train_loader:
            batch = move_batch(batch)
            pred, flux_norm, flux_phys, _ = _step(
                model,
                batch["soil"],
                batch["forcing"],
                batch["forcing_raw"],
                batch["sw0"],
                batch["theta_s"],
                batch["z_hat"],
                batch["surface"],
            )

            sl, _, _, _ = state_loss(
                pred,
                batch["targets"][:, 0],
                batch["sw0"],
                batch["thickness"],
                batch["z_phys"],
                delta_scale,
                cfg,
            )
            fl = masked_flux_loss(
                flux_norm,
                batch["flux_targets_norm"][:, 0],
                batch["flux_mask"][:, 0],
            )
            mass = water_balance_loss(
                batch["sw0"],
                pred,
                flux_phys,
                batch["thickness"],
                cfg.dt_days,
            )
            surf = surface_partition_loss(
                flux_phys,
                batch["forcing_raw"],
            )
            nonneg = nonnegative_flux_loss(flux_phys)

            total = (
                sl
                + cfg.flux_weight * fl
                + ramp * cfg.mass_weight * mass
                + ramp * cfg.surface_balance_weight * surf
                + cfg.flux_nonnegative_weight * nonneg
            )

            opt.zero_grad(set_to_none=True)
            total.backward()
            nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
            opt.step()

            bs = pred.shape[0]
            count += bs
            sums += np.array(
                [total.item(), sl.item(), fl.item(), mass.item(), surf.item()]
            ) * bs

        sched.step()
        vmse, vmae = evaluate_one_step(model, val_loader, cfg)
        m = sums / max(count, 1)

        history["total"].append(float(m[0]))
        history["state"].append(float(m[1]))
        history["dynamic"].append(0.0)
        history["gradient"].append(0.0)
        history["flux"].append(float(m[2]))
        history["physics"].append(float(m[3] + m[4]))
        history["val"].append(float(vmse))

        if epoch == 1 or epoch % 10 == 0:
            print(
                f"[conservation] {epoch:4d} total={m[0]:.4e} "
                f"state={m[1]:.4e} flux={m[2]:.4e} "
                f"mass={m[3]:.4e} surface={m[4]:.4e} "
                f"val_mse={vmse:.4e} val_mae={vmae:.4e}"
            )


# ============================================================================
# 9. TRAIN / SAVE
# ============================================================================

def train(cfg: HybridConfig = CFG):
    cfg.validate()
    print(f"Device: {DEVICE}")

    datasets = load_profile_csvs(cfg.data_dir, cfg)
    train_data, test_data = split_timewise(datasets, cfg.train_ratio, cfg)
    weather = load_weather(cfg.weather_file, cfg.data_dir)

    stats = compute_train_stats(train_data, weather, cfg)
    train_ds = RolloutDataset.build(train_data, weather, cfg, stats)
    test_ds = RolloutDataset.build(test_data, weather, cfg, stats)

    print("Train samples:", len(train_ds))
    print("Test samples :", len(test_ds))

    train_loader = make_loader(train_ds, cfg, shuffle=True)
    val_loader = make_loader(test_ds, cfg, shuffle=False)

    model = HybridPIDeepONet(cfg, stats).to(DEVICE)

    history = {
        "total": [],
        "state": [],
        "dynamic": [],
        "gradient": [],
        "flux": [],
        "physics": [],
        "val": [],
    }

    if cfg.pretrain_epochs > 0:
        train_pretrain(model, train_loader, val_loader, stats, cfg, history)

    if cfg.rollout_epochs > 0:
        train_rollout(model, train_loader, val_loader, stats, cfg, history)

    if cfg.conservation_epochs > 0:
        train_conservation(model, train_loader, val_loader, stats, cfg, history)

    Path(cfg.model_dir).mkdir(parents=True, exist_ok=True)
    path = Path(cfg.model_dir) / cfg.model_file

    # Explicitly requested checkpoint format: store the model OBJECT.
    checkpoint = {
        "model": model,
        "config": asdict(cfg),
        "normalization": stats.to_dict(),
        "history": history,
        "seed": SEED,
        "mvp_notes": {
            "interior_richards_residual": False,
            "reason": (
                "Teacher files do not expose Kdul/microP/BC mode/internal Ross-p "
                "state; using approximate Campbell physics would mismatch SWIM3."
            ),
            "boundary_flux_supervision": True,
        },
    }
    torch.save(checkpoint, path)
    print("Saved:", path.resolve())

    return model, history, train_data, test_data, weather, stats


# ============================================================================
# 10. APSIM / SWIM3 ADAPTER
# ============================================================================

class APSIMDeepONetAdapter:
    def __init__(self, model, cfg, stats, device=DEVICE):
        self.model = model.to(device).eval()
        self.cfg = cfg
        self.stats = stats
        self.device = device

    @classmethod
    def from_checkpoint(cls, checkpoint_path: str, device=DEVICE):
        ckpt = torch.load(
            checkpoint_path,
            map_location=device,
            weights_only=False,
        )
        model = ckpt["model"].to(device).eval()
        cfg_dict = dict(ckpt["config"])
        cfg = HybridConfig(**cfg_dict)
        stats = NormalizationStats.from_dict(ckpt["normalization"])
        return cls(model, cfg, stats, device)

    def _soil_tensor(
        self,
        dul,
        sat,
        airdry,
        ll15,
        ks,
        depth_mm,
    ):
        bottom = np.asarray(depth_mm, dtype=np.float32) * self.cfg.depth_to_m
        top = np.concatenate([[0.0], bottom[:-1]])
        thickness = bottom - top
        centres = 0.5 * (top + bottom)
        z_hat = centres / bottom[-1]

        ks_m = np.asarray(ks, dtype=np.float32) * self.cfg.ks_to_m_day
        soil_raw = np.stack(
            [
                np.asarray(dul, np.float32),
                np.asarray(sat, np.float32),
                np.asarray(airdry, np.float32),
                np.asarray(ll15, np.float32),
                np.log10(np.clip(np.abs(ks_m), 1e-12, None)),
                thickness.astype(np.float32),
            ],
            axis=-1,
        )
        soil = (soil_raw - self.stats.soil_mean) / self.stats.soil_std
        return (
            soil.astype(np.float32),
            z_hat.astype(np.float32),
            thickness.astype(np.float32),
        )

    @torch.no_grad()
    def predict_next_day(
        self,
        sw: Sequence[float],
        dul: Sequence[float],
        sat: Sequence[float],
        airdry: Sequence[float],
        ll15: Sequence[float],
        ks: Sequence[float],
        depth_mm: Sequence[float],
        rain_history: Sequence[float],
        evap_history: Sequence[float],
        pond_depth_mm: float = 0.0,
    ):
        """
        rain_history/evap_history contain H days ending on the TARGET day.
        Inputs are in the same raw units as the supplied APSIM tables:
          rain/evap/KS in mm/day, Depth/PondDepth in mm.
        """
        L = self.cfg.num_layers
        arrays = [sw, dul, sat, airdry, ll15, ks, depth_mm]
        arrays = [np.asarray(x, dtype=np.float32).reshape(-1) for x in arrays]
        if any(len(x) != L for x in arrays):
            raise ValueError(f"All profile arrays must have length {L}")

        rh = np.asarray(rain_history, dtype=np.float32).reshape(-1)
        eh = np.asarray(evap_history, dtype=np.float32).reshape(-1)
        H = self.cfg.forcing_history_days
        if len(rh) != H or len(eh) != H:
            raise ValueError(f"Need exactly {H} forcing values ending on target day")

        soil, z_hat, _ = self._soil_tensor(
            arrays[1], arrays[2], arrays[3], arrays[4], arrays[5], arrays[6]
        )

        forcing_raw = np.stack(
            [rh, eh], axis=1
        ) * self.cfg.forcing_to_m_day
        forcing = (
            forcing_raw - self.stats.forcing_mean
        ) / self.stats.forcing_std

        def t(x):
            return torch.tensor(x, dtype=torch.float32, device=self.device)

        theta, _, flux = self.model(
            t(soil).unsqueeze(0),
            t(forcing).unsqueeze(0),
            t(arrays[0]).unsqueeze(0),
            t(arrays[2]).unsqueeze(0),
            t(z_hat).unsqueeze(0),
            t([[pond_depth_mm * self.cfg.pond_to_m]]),
        )

        flux = flux[0].cpu().numpy()
        return {
            "sw": theta[0].cpu().numpy(),
            "q_top_m_day": float(flux[FLUX_INDEX["q_top"]]),
            "q_bottom_m_day": float(flux[FLUX_INDEX["q_bottom"]]),
            "runoff_m_day": float(flux[FLUX_INDEX["runoff"]]),
            "evap_actual_m_day": float(flux[FLUX_INDEX["evap_actual"]]),
            "pond_m": float(flux[FLUX_INDEX["pond"]]),
            "sink_total_m_day": float(flux[FLUX_INDEX["sink_total"]]),
        }


# ============================================================================
# 11. DIRECT 10-DAY SWIM3/APSIM COMPARISON
# ============================================================================

@torch.no_grad()
def compare_rollout(
    adapter: APSIMDeepONetAdapter,
    df: pd.DataFrame,
    weather: pd.DataFrame,
    start_index: int,
    days: int = 10,
):
    """Autoregressive neural rollout against APSIM/SWIM3 teacher rows."""
    cfg = adapter.cfg
    H = cfg.forcing_history_days

    df = df.reset_index(drop=True).copy()
    if "__date__" not in df.columns:
        df["__date__"] = infer_date_series(df, "comparison profile")

    forcing_all, _ = forcing_for_profile(df, weather, cfg)

    if start_index < max(0, H - 2):
        raise ValueError("start_index too early for forcing history")
    if start_index + days >= len(df):
        raise ValueError("Not enough future rows")

    depth = df[cols("Depth", cfg.num_layers)].iloc[start_index].to_numpy(np.float32)
    current = {
        p.lower(): df[cols(p, cfg.num_layers)].iloc[start_index].to_numpy(np.float32)
        for p in PROFILE_PREFIXES
    }

    pond = (
        float(df["PondDepth"].iloc[start_index])
        if "PondDepth" in df.columns
        else 0.0
    )

    pred_profiles = [current["sw"].copy()]
    true_profiles = [current["sw"].copy()]
    flux_rows = []

    # Initial forcing history ends on the FIRST TARGET DAY.
    first_target = start_index + 1
    idx0 = np.arange(first_target - H + 1, first_target + 1)
    if np.any(idx0 < 0):
        raise ValueError("Not enough forcing history")

    forcing_hist = forcing_all[idx0].copy()

    for k in range(days):
        target_row = start_index + k + 1

        # Adapter accepts raw mm/day values, while forcing_all is m/day.
        pred = adapter.predict_next_day(
            sw=current["sw"],
            dul=current["dul"],
            sat=current["sat"],
            airdry=current["airdry"],
            ll15=current["ll15"],
            ks=current["ks"],
            depth_mm=depth,
            rain_history=forcing_hist[:, 0] / cfg.forcing_to_m_day,
            evap_history=forcing_hist[:, 1] / cfg.forcing_to_m_day,
            pond_depth_mm=pond,
        )

        pred_profiles.append(pred["sw"].copy())
        true_sw = df[cols("SW", cfg.num_layers)].iloc[target_row].to_numpy(np.float32)
        true_profiles.append(true_sw)

        true_flux, true_mask = boundary_targets_for_profile(
            df.iloc[max(0, target_row - 1):target_row + 1].reset_index(drop=True),
            np.diff(np.concatenate([[0.0], depth * cfg.depth_to_m])).astype(np.float32),
            cfg,
        )
        tf = true_flux[-1]

        flux_rows.append({
            "step": k + 1,
            "date": pd.Timestamp(df["__date__"].iloc[target_row]),
            **{f"pred_SW({j+1})": float(pred["sw"][j]) for j in range(cfg.num_layers)},
            **{f"true_SW({j+1})": float(true_sw[j]) for j in range(cfg.num_layers)},
            "pred_q_top_mm_day": 1000.0 * pred["q_top_m_day"],
            "true_q_top_mm_day": 1000.0 * float(tf[FLUX_INDEX["q_top"]]),
            "pred_q_bottom_mm_day": 1000.0 * pred["q_bottom_m_day"],
            "true_q_bottom_mm_day": 1000.0 * float(tf[FLUX_INDEX["q_bottom"]]),
            "pred_runoff_mm_day": 1000.0 * pred["runoff_m_day"],
            "true_runoff_mm_day": 1000.0 * float(tf[FLUX_INDEX["runoff"]]),
            "pred_Es_mm_day": 1000.0 * pred["evap_actual_m_day"],
            "true_Es_mm_day": 1000.0 * float(tf[FLUX_INDEX["evap_actual"]]),
        })

        current["sw"] = pred["sw"]
        pond = max(0.0, pred["pond_m"] / cfg.pond_to_m)

        if k + 1 < days:
            next_forcing = forcing_all[target_row + 1]
            forcing_hist = np.concatenate(
                [forcing_hist[1:], next_forcing[None, :]],
                axis=0,
            )

    return (
        np.stack(true_profiles),
        np.stack(pred_profiles),
        pd.DataFrame(flux_rows),
    )


# Make full-model checkpoints importable even when training was launched as
# `python pi_deeponet_apsim_swim3_mvp.py`.
for _checkpoint_cls in (
    StateEncoder,
    SoilEncoder,
    ForcingEncoder,
    SurfaceEncoder,
    BranchNet,
    TrunkNet,
    HybridPIDeepONet,
):
    _checkpoint_cls.__module__ = _STABLE_MODULE


# ============================================================================
# 12. CLI
# ============================================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--weather", default=None)
    ap.add_argument("--save", default=None)
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--pretrain-epochs", type=int, default=None)
    ap.add_argument("--rollout-epochs", type=int, default=None)
    ap.add_argument("--conservation-epochs", type=int, default=None)
    args = ap.parse_args()

    cfg = HybridConfig(**asdict(CFG))
    if args.data_dir is not None:
        cfg.data_dir = args.data_dir
    if args.weather is not None:
        cfg.weather_file = args.weather
    if args.save is not None:
        cfg.model_file = args.save
    if args.batch_size is not None:
        cfg.batch_size = args.batch_size
    if args.pretrain_epochs is not None:
        cfg.pretrain_epochs = args.pretrain_epochs
    if args.rollout_epochs is not None:
        cfg.rollout_epochs = args.rollout_epochs
    if args.conservation_epochs is not None:
        cfg.conservation_epochs = args.conservation_epochs

    train(cfg)


if __name__ == "__main__":
    main()
