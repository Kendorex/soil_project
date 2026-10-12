import os
import sys
import json
import time
import threading
from pathlib import Path
from datetime import datetime, timezone

import numpy as np

# Импортируем модуль целиком, чтобы видеть актуальные значения
# глобальных переменных после apply_apsim_config().
from . import apsim_pinn_server as aps
from .pinn_inference import APSIMPINNBridge
from .prepare_pinn_input import parse_apsimx


# =========================================================
# МАППИНГ ИМЁН BMI <-> APSIM
# =========================================================
BMI_TO_APSIM = {
    "soil_water_volumetric": aps.VAR_SW,
    "pinn_soil_water_volumetric": "PINN.SW",
    "soil_psi":              aps.VAR_PSI_SWIM3,
    "soil_psi_read":         aps.VAR_PSI_READ,
    "rain":                  aps.VAR_RAIN,
    "radiation":             aps.VAR_RADN,
    "temp_max":              aps.VAR_MAXT,
    "temp_min":              aps.VAR_MINT,
    "evap_soil":             aps.VAR_ES,
    "evap_potential":        aps.VAR_EO,
    "evap_potential_soil":   aps.VAR_EOS,
    "pond_depth":            aps.VAR_POND,
    "pinn_q_top":            "PINN.q_top",
    "pinn_q_bottom":         "PINN.q_bottom",
    "pinn_runoff":           "PINN.runoff",
    "pinn_evap_actual":      "PINN.evap_actual",
    "pinn_pond_depth":       "PINN.pond",
    "pinn_sink_total":       "PINN.sink_total",
    "date":                  aps.VAR_TODAY,
}

# --- CSDMS-алиасы (стандартные имена для pymt) ---
CSDMS_ALIASES = {
    "soil_water__volume_fraction":         "soil_water_volumetric",
    "soil_water__matric_potential":        "soil_psi",
    "soil_water__matric_potential_read":   "soil_psi_read",
    "atmosphere_water__rainfall_flux":     "rain",
    "atmosphere_shortwave__radiation_flux":"radiation",
    "atmosphere_air__max_temperature":     "temp_max",
    "atmosphere_air__min_temperature":     "temp_min",
    "land_surface_water__evaporation_flux":"evap_soil",
    "land_surface_water__potential_evaporation_flux": "evap_potential",
    "model__time":                         "date",
}

# Обратный словарь (APSIM-имя -> список алиасов)
_ALIAS_FOR = {}
for alias, base in CSDMS_ALIASES.items():
    _ALIAS_FOR.setdefault(base, []).append(alias)


INPUT_VARS = [
    "soil_water_volumetric",
    "rain", "radiation", "temp_max", "temp_min",
    "evap_soil", "evap_potential", "date",
    "evap_potential_soil", "pond_depth",
    "soil_psi_read",
]

OUTPUT_VARS = [
    "soil_psi",
    "pinn_soil_water_volumetric",
    "pinn_q_top", "pinn_q_bottom", "pinn_runoff",
    "pinn_evap_actual", "pinn_pond_depth", "pinn_sink_total",
]

VAR_UNITS = {
    "soil_water_volumetric": "mm/mm",
    "pinn_soil_water_volumetric": "mm/mm",
    "soil_psi":              "mm",
    "soil_psi_read":         "mm",
    "rain":                  "mm",
    "radiation":             "MJ/m2",
    "temp_max":              "degC",
    "temp_min":              "degC",
    "evap_soil":             "mm",
    "evap_potential":        "mm",
    "evap_potential_soil":   "mm/d",
    "pond_depth":            "mm",
    "pinn_q_top":            "m/d",
    "pinn_q_bottom":         "m/d",
    "pinn_runoff":           "m/d",
    "pinn_evap_actual":      "m/d",
    "pinn_pond_depth":       "m",
    "pinn_sink_total":       "m/d",
    "date":                  "d",
}

VAR_TYPE = {name: "float64" for name in BMI_TO_APSIM}
VAR_TYPE["date"] = "int64"

ARRAY_VARS = [
    "soil_water_volumetric",
    "pinn_soil_water_volumetric",
    "soil_psi",
    "soil_psi_read",
]
SCALAR_VARS = [
    "rain", "radiation", "temp_max", "temp_min",
    "evap_soil", "evap_potential",
    "evap_potential_soil", "pond_depth",
    "pinn_q_top", "pinn_q_bottom", "pinn_runoff",
    "pinn_evap_actual", "pinn_pond_depth", "pinn_sink_total",
]


# =========================================================
# КЛАСС BMI
# =========================================================
class BmiApsimPinn:
    METADATA = "meta/ApsimPinn"
    _NAME = "APSIM-PINN Hybrid Model"
    _TIME_UNITS = "d"
    _TIME_STEP = 1.0

    def __init__(self):
        self._initialized = False
        self._current_time = 0.0
        self._start_time = 0.0
        self._end_time = 0.0

        self._values = {}

        self._proc = None
        self._server = None
        self._server_thread = None
        self._bridge = None
        self._soil_static = None
        self._n_layers = 0

        self._out_dir = None

    # =========================================================
    # 0. PYMT SETUP
    # =========================================================
    def setup(self, cfg_dir: str | None = None, **kwargs):
        """
        pymt ожидает, что setup() вернёт (config_file, config_dir).
        Создаёт дефолтный конфиг, если его нет.
        """
        if cfg_dir is None:
            cfg_dir = Path(__file__).resolve().parent.parent / "config"
        cfg_dir = Path(cfg_dir)
        cfg_dir.mkdir(parents=True, exist_ok=True)

        cfg_file = cfg_dir / "bmi_config.json"
        if not cfg_file.exists():
            default_cfg = {
                "apsimx":  "config/test_field_2021.apsimx",
                "model":   "pi_deeponet_swim3_mvp.pth",
                "out_dir": "run_output",
                "mode":    "replace",

                "apsim_mode": "windows",
                "apsim_windows_bin": r"C:\Program Files\APSIM2026.9.8115.0\bin\Models.exe",
                "apsim_wsl": {
                    "distro": "Ubuntu",
                    "user":   "user",
                    "bin":    "/home/user/ApsimX/bin/Release/net8.0/Models",
                },

                "weather_met": "weather/weather_2021_normalized.met",
            }
            with open(cfg_file, "w", encoding="utf-8") as f:
                json.dump(default_cfg, f, indent=2, ensure_ascii=False)

        return str(cfg_file), str(cfg_dir)

    # =========================================================
    # 1. ЖИЗНЕННЫЙ ЦИКЛ
    # =========================================================
    def initialize(self, config_file: str) -> None:
        if self._initialized:
            print("[initialize] Модель уже инициализирована, пропускаем.")
            return

        PROJECT_ROOT = Path(__file__).resolve().parent.parent

        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        # Применяем APSIM-конфиг к глобалам apsim_pinn_server.
        # Делаем это ДО patch_apsimx_weather и PinnServer.
        aps.apply_apsim_config(cfg)

        def resolve(p: str) -> Path:
            path = Path(p)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            return path

        apsimx_path = resolve(cfg["apsimx"])
        model_path  = resolve(cfg.get("model", str(aps.MODEL_PATH)))
        out_dir     = resolve(cfg.get("out_dir", aps.OUT_DIR.name))
        mode        = cfg.get("mode", aps.MODE)

        out_dir.mkdir(parents=True, exist_ok=True)
        self._out_dir = out_dir

        apsim_meta = parse_apsimx(str(apsimx_path))
        self._soil_static = apsim_meta["soil_static"]
        self._n_layers = len(self._soil_static["thickness"])

        self._bridge = APSIMPINNBridge(model_path=str(model_path), device="cpu")

        # Путь к погодному .met — из конфига, относительно корня проекта
        weather_met_abs = aps._resolve_weather_met(cfg)

        # Патчим Identifier и Weather.FileName в копии .apsimx,
        # затем запускаем APSIM (windows или wsl — решает resolve_apsim_path)
        patched = out_dir / "test_field_2021_patched.apsimx"
        aps.patch_apsimx_weather(apsimx_path, patched,
                                 aps.ZMQ_IDENT, weather_met_abs)

        apsim_path = aps.resolve_apsim_path(patched)
        print(f"[initialize] APSIM_MODE={aps.APSIM_MODE}, путь: {apsim_path}")

        self._proc = aps.start_apsim(apsim_path)
        threading.Thread(
            target=aps.pipe_apsim_output,
            args=(self._proc, out_dir / "apsim_stdout.log"),
            daemon=True,
        ).start()

        self._server = aps.PinnServer(
            self._bridge, self._soil_static,
            apsim_meta["sw_initial"], mode=mode,
        )
        self._server_thread = threading.Thread(
            target=self._server.run, daemon=True,
        )
        self._server._thread = self._server_thread
        self._server_thread.start()

        self._init_buffers()

        start_dt = datetime.strptime(apsim_meta["start_date"], "%Y-%m-%d")
        end_dt   = datetime.strptime(apsim_meta["end_date"],   "%Y-%m-%d")
        self._start_time = 0.0
        self._end_time = float((end_dt - start_dt).days)
        self._current_time = 0.0

        self._server.step_done.wait(timeout=30)
        self._server.step_done.clear()
        self._apply_latest()

        self._initialized = True

    def finalize(self) -> None:
        # Идемпотентность: повторный вызов не должен падать
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try: self._proc.kill()
                except Exception: pass
            finally:
                self._proc = None

        time.sleep(0.5)

        if self._server is not None:
            try:
                self._server.stop()
            except Exception as e:
                print(f"[finalize] server.stop: {e}")
            finally:
                self._server = None

        self._server_thread = None
        self._initialized = False

    def update(self) -> None:
        if self._server is None:
            return

        self._server.step_done.wait(timeout=60)
        self._server.step_done.clear()

        self._apply_latest()
        self._current_time += self._TIME_STEP

    def update_until(self, time: float) -> None:
        if time > self._end_time:
            raise ValueError(
                f"time={time} превышает end_time={self._end_time}"
            )
        while self._current_time < time:
            self.update()

    # =========================================================
    # 2. ИНФОРМАЦИЯ О МОДЕЛИ
    # =========================================================
    def get_component_name(self): return self._NAME
    def get_input_item_count(self): return len(INPUT_VARS)
    def get_output_item_count(self): return len(OUTPUT_VARS)
    def get_input_var_names(self):  return tuple(INPUT_VARS)
    def get_output_var_names(self): return tuple(OUTPUT_VARS)

    def get_var_names(self):
        # Возвращаем и базовые имена, и CSDMS-алиасы
        names = list(BMI_TO_APSIM.keys())
        names.extend(CSDMS_ALIASES.keys())
        return tuple(names)

    # =========================================================
    # 3. ВРЕМЯ
    # =========================================================
    def get_current_time(self): return self._current_time
    def get_start_time(self):   return self._start_time
    def get_end_time(self):     return self._end_time
    def get_time_units(self):   return self._TIME_UNITS
    def get_time_step(self):    return self._TIME_STEP

    # =========================================================
    # 4. ЗНАЧЕНИЯ
    # =========================================================
    def get_value(self, name: str, out=None) -> np.ndarray:
        """
        BMI v2:
          get_value(name)        -> ndarray (копия)
          get_value(name, out)   -> записывает в out, возвращает out
        """
        name = self._resolve_name(name)
        self._check_var(name)

        if out is None:
            return self._values[name].copy()

        arr = np.asarray(out)
        arr[...] = self._values[name]
        return arr

    def set_value(self, name: str, value) -> None:
        name = self._resolve_name(name)
        self._check_var(name)
        arr = np.asarray(value, dtype=self._values[name].dtype)

        if arr.shape != self._values[name].shape:
            if self._values[name].size == 1 and arr.size == 1:
                arr = arr.reshape(self._values[name].shape)
            else:
                raise ValueError(
                    f"{name}: ожидалось {self._values[name].shape}, "
                    f"получено {arr.shape}"
                )

        self._values[name][:] = arr

        if name == "soil_psi" and self._server is not None:
            self._server.set_pending_psi(arr.tolist())

    # =========================================================
    # 5. МЕТАДАННЫЕ
    # =========================================================
    def get_var_type(self, name):
        name = self._resolve_name(name); self._check_var(name)
        return VAR_TYPE[name]
    def get_var_units(self, name):
        name = self._resolve_name(name); self._check_var(name)
        return VAR_UNITS[name]
    def get_var_itemsize(self, name):
        name = self._resolve_name(name); self._check_var(name)
        return self._values[name].dtype.itemsize
    def get_var_nbytes(self, name):
        name = self._resolve_name(name); self._check_var(name)
        return self._values[name].nbytes
    def get_var_location(self, name):
        name = self._resolve_name(name); self._check_var(name)
        return "node"
    def get_var_grid(self, name):
        name = self._resolve_name(name); self._check_var(name)
        return 0

    # =========================================================
    # 6. СЕТКА (1D)
    # =========================================================
    def get_grid_rank(self, grid_id):    return 1
    def get_grid_size(self, grid_id):    return self._n_layers
    def get_grid_type(self, grid_id):    return "rectilinear"
    def get_grid_shape(self, grid_id):   return (self._n_layers,)
    def get_grid_spacing(self, grid_id):
        return np.asarray(self._soil_static["thickness"], dtype=np.float64) / 1000.0
    def get_grid_origin(self, grid_id):  return np.array([0.0])

    def get_grid_x(self, grid_id):
        thickness_m = np.asarray(self._soil_static["thickness"]) / 1000.0
        return np.cumsum(thickness_m) - thickness_m / 2.0

    def get_grid_node_count(self, grid_id): return self._n_layers
    def get_grid_y(self, grid_id):          return np.array([])
    def get_grid_z(self, grid_id):          return np.array([])
    def get_grid_edge_count(self, grid_id): return 0
    def get_grid_face_count(self, grid_id): return 0
    def get_grid_edge_nodes(self, grid_id):      return np.array([], dtype=np.int32)
    def get_grid_face_edges(self, grid_id):      return np.array([], dtype=np.int32)
    def get_grid_face_nodes(self, grid_id):      return np.array([], dtype=np.int32)
    def get_grid_nodes_per_face(self, grid_id):  return np.array([], dtype=np.int32)

    # =========================================================
    # 7. УКАЗАТЕЛИ И ИНДЕКСЫ
    # =========================================================
    def get_value_ptr(self, name):
        """
        pymt предпочитает numpy-вид (view), а не ctypes-указатель.
        """
        name = self._resolve_name(name)
        self._check_var(name)
        return self._values[name]   # view, не копия

    def get_value_at_indices(self, name: str, indices, out=None):
        """
        BMI v2:
          get_value_at_indices(name, inds)        -> ndarray (копия)
          get_value_at_indices(name, inds, out)   -> записывает в out
        """
        name = self._resolve_name(name)
        self._check_var(name)
        idx = np.asarray(indices, dtype=np.int64)

        if out is None:
            return self._values[name][idx].copy()

        arr = np.asarray(out)
        arr[...] = self._values[name][idx]
        return arr

    def set_value_at_indices(self, name: str, indices, values) -> None:
        name = self._resolve_name(name)
        self._check_var(name)
        idx = np.asarray(indices, dtype=np.int64)
        self._values[name][idx] = values
        if name == "soil_psi" and self._server is not None:
            self._server.set_pending_psi(self._values[name].tolist())

    # =========================================================
    # ВНУТРЕННИЕ ХЕЛПЕРЫ
    # =========================================================
    def _resolve_name(self, name: str) -> str:
        """Переводит CSDMS-алиас в базовое имя."""
        return CSDMS_ALIASES.get(name, name)

    def _init_buffers(self) -> None:
        n = self._n_layers
        for name in ARRAY_VARS:
            self._values[name] = np.zeros(n, dtype=np.float64)
        for name in SCALAR_VARS:
            self._values[name] = np.zeros(1, dtype=np.float64)
        self._values["date"] = np.zeros(1, dtype=np.int64)

    def _apply_latest(self) -> None:
        if self._server is None:
            return
        latest = getattr(self._server, "latest", None)
        if not latest:
            return
        for name, val in latest.items():
            if name not in self._values:
                continue
            buf = self._values[name]
            try:
                arr = np.asarray(val, dtype=buf.dtype)

                if arr.shape == buf.shape:
                    buf[:] = arr
                elif arr.size == buf.size:
                    buf[:] = arr.reshape(buf.shape)
                elif buf.size == 1:
                    if arr.size == 0:
                        continue
                    buf[0] = arr.flat[0]
                else:
                    print(f"[apply_latest] {name}: shape mismatch "
                          f"{arr.shape} vs {buf.shape}")
            except Exception as e:
                print(f"[apply_latest] {name}: {e}")

    def _check_var(self, name: str) -> None:
        if name not in self._values:
            raise KeyError(f"Неизвестная переменная: {name}")
