"""Checks for the new checkpoint contract and daily APSIM hand-off."""

from dataclasses import asdict
from pathlib import Path
from threading import Event
import tempfile
import sys

import numpy as np
import pytest
import torch

from src import pi_deeponet_apsim_swim3_mvp as mvp
from src import apsim_pinn_server as aps
from src.pinn_inference import APSIMPINNBridge
from src.prepare_pinn_input import parse_apsimx, build_apsim_soil_data


SOIL = parse_apsimx(
    str(Path(__file__).resolve().parent.parent / "config" / "test_field_2021.apsimx")
)


def test_bridge_loads_full_model_and_uses_layer_geometry():
    cfg = mvp.HybridConfig()
    stats = mvp.NormalizationStats(
        soil_mean=np.zeros(6, np.float32),
        soil_std=np.ones(6, np.float32),
        forcing_mean=np.zeros(2, np.float32),
        forcing_std=np.ones(2, np.float32),
        flux_mean=np.zeros(6, np.float32),
        flux_std=np.ones(6, np.float32),
        delta_scale=np.ones(cfg.num_layers, np.float32),
    )
    model = mvp.HybridPIDeepONet(cfg, stats)
    with tempfile.NamedTemporaryFile(
        suffix=".pth", dir=Path(__file__).resolve().parent.parent, delete=False
    ) as output:
        checkpoint = Path(output.name)
    try:
        torch.save(
            {"model": model, "config": asdict(cfg), "normalization": stats.to_dict()},
            checkpoint,
        )
        bridge = APSIMPINNBridge(str(checkpoint))
    finally:
        checkpoint.unlink()
    soil = build_apsim_soil_data(SOIL["soil_static"], SOIL["sw_initial"])
    result = bridge.predict_next_state(
        soil,
        rain_history=[0, 0, 0, 0, 0, 0, 2],
        evap_history=[0, 0, 0, 0, 0, 0, 0.4],
    )

    assert len(result["SW"]) == cfg.num_layers
    assert np.all(np.isfinite(result["SW"]))
    assert np.all(np.asarray(result["SW"]) > 0)
    assert np.all(np.asarray(result["SW"]) < soil["SAT"])
    np.testing.assert_allclose(
        result["SW_mm"], np.asarray(result["SW"]) * soil["thickness"]
    )
    assert all(np.isfinite(result[k]) for k in (
        "q_top_m_day", "q_bottom_m_day", "runoff_m_day",
        "evap_actual_m_day", "pond_m", "sink_total_m_day",
    ))


def test_bridge_requires_new_checkpoint():
    with pytest.raises(FileNotFoundError, match="checkpoint not found"):
        APSIMPINNBridge(str(Path(__file__).resolve().parent.parent / "missing.pth"))


def test_bridge_loads_checkpoint_saved_by_training_script():
    """Direct execution pickles model classes as __main__."""
    cfg = mvp.HybridConfig()
    stats = mvp.NormalizationStats(
        np.zeros(6, np.float32), np.ones(6, np.float32),
        np.zeros(2, np.float32), np.ones(2, np.float32),
        np.zeros(6, np.float32), np.ones(6, np.float32),
        np.ones(cfg.num_layers, np.float32),
    )
    names = (
        "HybridConfig", "HybridPIDeepONet", "StateEncoder", "SoilEncoder",
        "ForcingEncoder", "SurfaceEncoder", "BranchNet", "TrunkNet",
    )
    main = sys.modules["__main__"]
    previous = {name: getattr(main, name, None) for name in names}
    with tempfile.NamedTemporaryFile(
        suffix=".pth", dir=Path(__file__).resolve().parent.parent, delete=False
    ) as output:
        checkpoint = Path(output.name)
    try:
        for name in names:
            cls = getattr(mvp, name)
            cls.__module__ = "__main__"
            setattr(main, name, cls)
        torch.save(
            {"model": mvp.HybridPIDeepONet(cfg, stats),
             "config": asdict(cfg), "normalization": stats.to_dict()},
            checkpoint,
        )
    finally:
        for name in names:
            getattr(mvp, name).__module__ = mvp.__name__
            if previous[name] is None:
                delattr(main, name)
            else:
                setattr(main, name, previous[name])
    try:
        assert isinstance(APSIMPINNBridge(str(checkpoint)).adapter.model,
                          mvp.HybridPIDeepONet)
    finally:
        checkpoint.unlink()


def test_swim3_water_range_survives_psi_conversion():
    soil = SOIL["soil_static"]
    values = np.asarray(soil["LL15"]) * 0.5
    values[0] = (soil["DUL"][0] + soil["SAT"][0]) * 0.5
    clipped, _, _ = aps.clip_sw_to_valid_range(
        values, np.zeros(7), soil["SAT"]
    )
    np.testing.assert_allclose(clipped, values)
    psi = aps.compute_psi_mm_apsim(clipped, soil)
    assert np.all(np.isfinite(psi))


def test_server_uses_previous_end_of_day_state_and_current_forcing():
    calls = []

    class Bridge:
        history_days = 7

        def predict_next_state(self, apsim_soil_data, **kwargs):
            calls.append((apsim_soil_data, kwargs))
            return {
                "SW": [0.25] * 7,
                "q_top_m_day": 0.001,
                "q_bottom_m_day": 0.002,
                "runoff_m_day": 0.003,
                "evap_actual_m_day": 0.004,
                "pond_m": 0.005,
                "sink_total_m_day": 0.006,
            }

    server = object.__new__(aps.PinnServer)
    server.bridge = Bridge()
    server.soil_static = SOIL["soil_static"]
    server._model_sw = SOIL["sw_initial"]
    server._model_pond_mm = 0.0
    from collections import deque
    server._rain_history = deque(maxlen=7)
    server._evap_history = deque(maxlen=7)
    server.day_idx = 0
    server.mode = "compare"
    server.history = []
    server.latest = {"soil_water_volumetric": np.zeros(7),
                     "pinn_soil_water_volumetric": np.zeros(7),
                     "soil_psi": np.zeros(7), "soil_psi_read": np.zeros(7)}
    server.step_event = Event()
    server.step_done = Event()
    server._stop_flag = Event()
    server._resume = lambda _: None

    values = {
        aps.VAR_SW: [0.2] * 7,
        aps.VAR_RAIN: 2.0,
        aps.VAR_RADN: 10.0,
        aps.VAR_MAXT: 20.0,
        aps.VAR_MINT: 10.0,
        aps.VAR_ES: 0.3,
        aps.VAR_EO: 0.5,
        aps.VAR_EOS: 0.4,
        aps.VAR_POND: 0.0,
        aps.VAR_TODAY: "2021-01-01",
        aps.VAR_PSI_READ: [-100.0] * 7,
    }
    server.get_var = values.__getitem__
    server._handle_paused1()
    assert calls[0][0]["SW"] == SOIL["sw_initial"]
    assert calls[0][1]["rain_history"] == [0, 0, 0, 0, 0, 0, 2.0]
    assert calls[0][1]["evap_history"] == [0, 0, 0, 0, 0, 0, 0.4]
    np.testing.assert_allclose(server.latest["soil_water_volumetric"], 0.2)
    np.testing.assert_allclose(server.latest["pinn_soil_water_volumetric"], 0.25)

    values[aps.VAR_SW] = [0.21] * 7
    values[aps.VAR_POND] = 3.0
    server._handle_paused()
    values[aps.VAR_RAIN] = 5.0
    values[aps.VAR_EOS] = 0.6
    server._handle_paused1()
    assert calls[1][0]["SW"] == [0.21] * 7
    assert calls[1][1]["pond_depth_mm"] == 3.0
    assert calls[1][1]["rain_history"] == [0, 0, 0, 0, 0, 2.0, 5.0]
