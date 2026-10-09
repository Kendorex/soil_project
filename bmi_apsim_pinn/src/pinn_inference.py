"""Adapter between APSIM's daily values and the SWIM3 DeepONet checkpoint."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

from . import pi_deeponet_apsim_swim3_mvp as mvp


# The training script saves a full model object under this import name.
sys.modules["pi_deeponet_apsim_swim3_mvp"] = mvp
_MAIN_CLASSES = (
    "HybridConfig", "HybridPIDeepONet", "StateEncoder", "SoilEncoder",
    "ForcingEncoder", "SurfaceEncoder", "BranchNet", "TrunkNet",
)
_MISSING = object()


class APSIMPINNBridge:
    def __init__(self, model_path: str, device: str = "cpu"):
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"SWIM3 DeepONet checkpoint not found: {path}. "
                "Train the MVP model and set 'model' in bmi_config.json."
            )

        host_main = sys.modules["__main__"]
        previous = {name: getattr(host_main, name, _MISSING) for name in _MAIN_CLASSES}
        try:
            for name in _MAIN_CLASSES:
                setattr(host_main, name, getattr(mvp, name))
            checkpoint = torch.load(path, map_location=device, weights_only=False)
        finally:
            for name, value in previous.items():
                if value is _MISSING:
                    delattr(host_main, name)
                else:
                    setattr(host_main, name, value)
        if not isinstance(checkpoint, dict) or not isinstance(
            checkpoint.get("model"), mvp.HybridPIDeepONet
        ):
            raise ValueError(
                f"{path} is not a pi_deeponet_apsim_swim3_mvp checkpoint"
            )
        if "config" not in checkpoint or "normalization" not in checkpoint:
            raise ValueError(f"{path} lacks config or normalization statistics")

        cfg = mvp.HybridConfig(**checkpoint["config"])
        cfg.validate()
        stats = mvp.NormalizationStats.from_dict(checkpoint["normalization"])
        self.adapter = mvp.APSIMDeepONetAdapter(
            checkpoint["model"], cfg, stats, device=device
        )
        self.history_days = cfg.forcing_history_days
        self.num_layers = cfg.num_layers

    def predict_next_state(
        self,
        apsim_soil_data: dict,
        target_time_days: float = 1.0,
        *,
        rain_history,
        evap_history,
        pond_depth_mm: float = 0.0,
    ) -> dict:
        if target_time_days != self.adapter.cfg.dt_days:
            raise ValueError(
                f"Checkpoint predicts {self.adapter.cfg.dt_days} day steps; "
                f"requested {target_time_days}"
            )
        thickness = np.asarray(apsim_soil_data["thickness"], dtype=np.float64)
        if len(thickness) != self.num_layers or np.any(thickness <= 0):
            raise ValueError(f"Expected {self.num_layers} positive soil layer thicknesses")
        depth_mm = np.cumsum(thickness)

        prediction = self.adapter.predict_next_day(
            sw=apsim_soil_data["SW"],
            dul=apsim_soil_data["DUL"],
            sat=apsim_soil_data["SAT"],
            airdry=apsim_soil_data["AirDry"],
            ll15=apsim_soil_data["LL15"],
            ks=apsim_soil_data["KS"],
            depth_mm=depth_mm,
            rain_history=rain_history,
            evap_history=evap_history,
            pond_depth_mm=pond_depth_mm,
        )
        sw = np.asarray(prediction.pop("sw"), dtype=np.float64)
        if not np.all(np.isfinite(sw)):
            raise ValueError("SWIM3 DeepONet returned non-finite soil water")
        if any(not np.isfinite(value) for value in prediction.values()):
            raise ValueError("SWIM3 DeepONet returned non-finite boundary outputs")
        return {
            "thickness": thickness.tolist(),
            "SW": sw.tolist(),
            "SW_mm": (sw * thickness).tolist(),
            "target_time_days": target_time_days,
            "status": "SUCCESS",
            **prediction,
        }
