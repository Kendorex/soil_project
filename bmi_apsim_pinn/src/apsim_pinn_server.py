import os
import json
import threading
import subprocess
from collections import deque
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import zmq
import msgpack

# Импорты из соседних файлов в src/ — относительные,
# чтобы работало и как пакет (pymt, tests), и из корня проекта.
from .pinn_inference import APSIMPINNBridge
from .prepare_pinn_input import parse_apsimx, build_apsim_soil_data


# =========================================================
# КОНФИГ
# =========================================================
APSIM_MODE    = None      # "windows" | "wsl"
APSIM_WIN_BIN = None
WSL_DISTRO    = None
WSL_USER      = None
APSIM_WSL_BIN = None

# Пути указывают на корень проекта (на уровень выше src/)
BASE_DIR = Path(__file__).resolve().parent.parent

APSIMX_WIN = BASE_DIR / "config" / "test_field_2021.apsimx"
MODEL_PATH = BASE_DIR / "src" / "pi_deeponet_swim3_mvp.pth"
OUT_DIR    = BASE_DIR / "run_output"

ZMQ_PORT = 5555
ZMQ_BIND = f"tcp://*:{ZMQ_PORT}"

ZMQ_HOST_IP = None
ZMQ_IDENT   = None

MODE = "replace"

# --- Имена переменных APSIM ---
VAR_SW        = "[Soil].Water.Volumetric"
VAR_PSI_SWIM3 = "[Soil].Swim3.PSI"
VAR_PSI_READ  = "[Soil].Swim3.PSI"
VAR_RAIN      = "[Weather].Rain"
VAR_RADN      = "[Weather].Radn"
VAR_MAXT      = "[Weather].MaxT"
VAR_MINT      = "[Weather].MinT"
VAR_ES        = "[ISoilWater].Es"
VAR_EO        = "[ISoilWater].Eo"
VAR_EOS       = "[ISoilWater].Eos"
VAR_POND      = "[Soil].Swim3.Pond"
VAR_TODAY     = "Clock.Today"

PSI_DUL_MM = -100.0

STOP_AFTER_DAYS  = None
RCV_TIMEOUT_MS   = 60000
DRAIN_TIMEOUT_MS = 50


# =========================================================
# ПРИМЕНЕНИЕ APSIM-КОНФИГА ИЗ JSON
# =========================================================
def apply_apsim_config(cfg: dict) -> None:
    """
    Обновляет глобальные APSIM_* из словаря конфига (bmi_config.json).
    Вызывается один раз в начале initialize()/main() ДО создания PinnServer
    и ДО патча .apsimx (потому что ZMQ_IDENT пишется в .apsimx).

    Ожидаемые ключи в cfg:
      "apsim_mode":        "windows" | "wsl"
      "apsim_windows_bin": путь к Models.exe (для windows)
      "apsim_wsl": {
          "distro": "Ubuntu",
          "user":   "user",
          "bin":    "/home/user/ApsimX/bin/Release/net8.0/Models",
      }
      "weather_met": "weather/weather_2021_normalized.met"  (опционально)
    """
    global APSIM_MODE, APSIM_WIN_BIN
    global WSL_DISTRO, WSL_USER, APSIM_WSL_BIN
    global ZMQ_HOST_IP, ZMQ_IDENT

    if not cfg:
        raise ValueError("apply_apsim_config: пустой конфиг (bmi_config.json не прочитан)")

    apsim_mode = cfg.get("apsim_mode")
    if apsim_mode not in ("windows", "wsl"):
        raise ValueError(
            f"В bmi_config.json ключ 'apsim_mode' должен быть "
            f"'windows' или 'wsl', получено {apsim_mode!r}"
        )
    APSIM_MODE = apsim_mode

    if APSIM_MODE == "windows":
        win_bin = cfg.get("apsim_windows_bin")
        if not win_bin:
            raise ValueError(
                "В bmi_config.json не задан 'apsim_windows_bin' "
                "(путь к Models.exe для Windows)"
            )
        if not Path(win_bin).exists():
            raise FileNotFoundError(
                f"Models.exe не найден по пути из bmi_config.json: {win_bin!r}"
            )
        APSIM_WIN_BIN = win_bin
    else:  # wsl
        wsl = cfg.get("apsim_wsl") or {}
        missing = [k for k in ("distro", "user", "bin") if not wsl.get(k)]
        if missing:
            raise ValueError(
                f"В bmi_config.json в 'apsim_wsl' не заданы поля: {missing}"
            )
        WSL_DISTRO    = wsl["distro"]
        WSL_USER      = wsl["user"]
        APSIM_WSL_BIN = wsl["bin"]

    ZMQ_HOST_IP = "127.0.0.1" if APSIM_MODE == "windows" else "172.30.64.1"
    ZMQ_IDENT   = f"{ZMQ_HOST_IP}:{ZMQ_PORT}"

    print(f"[config] APSIM_MODE={APSIM_MODE}")
    if APSIM_MODE == "windows":
        print(f"[config] WIN_BIN={APSIM_WIN_BIN!r}")
    else:
        print(f"[config] WSL: distro={WSL_DISTRO!r} "
              f"user={WSL_USER!r} bin={APSIM_WSL_BIN!r}")
    print(f"[config] ZMQ_IDENT={ZMQ_IDENT}")


def _require_apsim_config() -> None:
    """Проверка, что apply_apsim_config() уже был вызван."""
    if APSIM_MODE is None or ZMQ_IDENT is None:
        raise RuntimeError(
            "APSIM-конфиг не инициализирован. "
            "Сначала вызовите apply_apsim_config(cfg) с содержимым bmi_config.json."
        )
    if APSIM_MODE == "windows" and not APSIM_WIN_BIN:
        raise RuntimeError("APSIM_MODE=windows, но APSIM_WIN_BIN не задан.")
    if APSIM_MODE == "wsl" and not (WSL_DISTRO and WSL_USER and APSIM_WSL_BIN):
        raise RuntimeError("APSIM_MODE=wsl, но WSL_* параметры не заданы.")


def _resolve_weather_met(cfg: dict) -> Path | None:
    """
    Достаёт weather_met из cfg и возвращает абсолютный Windows-путь (Path).
    Если weather_met не задан — возвращает None (патч погоды не выполняется).
    """
    rel = cfg.get("weather_met")
    if not rel:
        return None
    p = Path(rel)
    if not p.is_absolute():
        p = BASE_DIR / p
    p = p.resolve()
    if not p.exists():
        raise FileNotFoundError(f"weather_met не найден: {p}")
    return p


# =========================================================
# APSIM-эквивалент: SimpleTheta + Suction
# =========================================================
PSI_LL15 = -15000.0
PSI_0    = -0.6e7
PSI_SAT  = -1.0


def setup_theta_curve(psi_dul, ll15, dul, sat):
    ll15 = np.asarray(ll15, dtype=np.float64)
    dul  = np.asarray(dul,  dtype=np.float64)
    sat  = np.asarray(sat,  dtype=np.float64)
    n    = len(sat)

    psid = np.full(n, float(psi_dul))

    log_psid   = np.log10(-psid)
    log_psiSat = np.log10(-PSI_SAT)
    log_ll15   = np.log10(-PSI_LL15)
    log_psi0   = np.log10(-PSI_0)

    delk = np.zeros((n, 4))
    delk[:, 0] = (dul - sat) / (log_psid - log_psiSat)
    delk[:, 1] = (ll15 - dul) / (log_ll15 - log_psid)
    delk[:, 2] = -ll15 / (log_psi0 - log_ll15)
    delk[:, 3] = delk[:, 2]

    mk = np.zeros((n, 4))
    mk[:, 0] = 0.0
    mk[:, 1] = (delk[:, 0] + delk[:, 1]) / 2.0
    mk[:, 2] = (delk[:, 1] + delk[:, 2]) / 2.0
    mk[:, 3] = delk[:, 3]

    with np.errstate(divide='ignore', invalid='ignore'):
        alpha = mk[:, 0] / delk[:, 0]
        beta  = mk[:, 1] / delk[:, 0]
        phi = alpha - (2.0*alpha + beta - 3.0)**2 / (3.0*(alpha + beta - 2.0))
    bad = phi <= 0
    if np.any(bad):
        with np.errstate(divide='ignore', invalid='ignore'):
            tau = 3.0 / np.sqrt(alpha[bad]**2 + beta[bad]**2)
        mk[bad, 0] = tau * alpha[bad] * delk[bad, 0]
        mk[bad, 1] = tau * beta[bad]  * delk[bad, 0]

    y0 = np.zeros((n, 5)); y1 = np.zeros((n, 5))
    m0 = np.zeros((n, 5)); m1 = np.zeros((n, 5))

    y0[:, 0] = sat;  y1[:, 0] = sat
    y0[:, 1] = sat;  y1[:, 1] = dul
    y0[:, 2] = dul;  y1[:, 2] = ll15
    y0[:, 3] = ll15; y1[:, 3] = 0.0
    y0[:, 4] = 0.0;  y1[:, 4] = 0.0

    m0[:, 0] = 0.0;  m1[:, 0] = 0.0
    m0[:, 1] = mk[:, 0] * (log_psid - log_psiSat)
    m1[:, 1] = mk[:, 1] * (log_psid - log_psiSat)
    m0[:, 2] = mk[:, 1] * (log_ll15 - log_psid)
    m1[:, 2] = mk[:, 2] * (log_ll15 - log_psid)
    m0[:, 3] = mk[:, 2] * (log_psi0 - log_ll15)
    m1[:, 3] = mk[:, 3] * (log_psi0 - log_ll15)
    m0[:, 4] = 0.0;  m1[:, 4] = 0.0

    return {"y0": y0, "y1": y1, "m0": m0, "m1": m1, "psid": psid}


def simple_theta_scalar(psi, layer, coeffs):
    psid = coeffs["psid"][layer]

    if psi >= PSI_SAT:
        return coeffs["y0"][layer, 0]

    if psi > psid:
        i = 1
        t = (np.log10(-psi) - np.log10(-PSI_SAT)) / (np.log10(-psid) - np.log10(-PSI_SAT))
    elif psi > PSI_LL15:
        i = 2
        t = (np.log10(-psi) - np.log10(-psid)) / (np.log10(-PSI_LL15) - np.log10(-psid))
    elif psi > PSI_0:
        i = 3
        t = (np.log10(-psi) - np.log10(-PSI_LL15)) / (np.log10(-PSI_0) - np.log10(-PSI_LL15))
        return (1.0 - t) * coeffs["y0"][layer, 3]
    else:
        return 0.0

    t2 = t * t
    t3 = t2 * t
    return ((2*t3 - 3*t2 + 1) * coeffs["y0"][layer, i]
          + (t3 - 2*t2 + t)   * coeffs["m0"][layer, i]
          + (-2*t3 + 3*t2)    * coeffs["y1"][layer, i]
          + (t3 - t2)         * coeffs["m1"][layer, i])


def suction_scalar(theta_target, layer, coeffs, max_iter=1000, tol=1e-9):
    psid = coeffs["psid"][layer]

    if theta_target >= coeffs["y0"][layer, 0]:
        return 0.0

    if theta_target > coeffs["y1"][layer, 1]:
        est1 = np.log10(-PSI_SAT)
        est2 = np.log10(-psid)
    elif theta_target > coeffs["y1"][layer, 2]:
        est1 = np.log10(-psid)
        est2 = np.log10(-PSI_LL15)
    else:
        est1 = np.log10(-PSI_LL15)
        est2 = np.log10(-PSI_0)

    best_est = est2
    best_err = np.inf

    for _ in range(max_iter):
        psi1 = -10.0 ** est1
        psi2 = -10.0 ** est2
        Y1 = simple_theta_scalar(psi1, layer, coeffs) - theta_target
        Y2 = simple_theta_scalar(psi2, layer, coeffs) - theta_target

        denom = Y2 - Y1
        if abs(denom) < 1e-30:
            break

        est3 = est2 - Y2 * (est2 - est1) / denom
        psi3 = -10.0 ** est3
        Y3 = simple_theta_scalar(psi3, layer, coeffs) - theta_target

        if abs(Y3) < tol:
            return psi3
        if abs(Y3) < best_err:
            best_err = abs(Y3)
            best_est = est3

        est1 = est2
        est2 = est3

    return -10.0 ** best_est


def compute_psi_mm_apsim(sw_values, soil_static, psi_dul=PSI_DUL_MM):
    sat  = np.asarray(soil_static["SAT"],  dtype=np.float64)
    dul  = np.asarray(soil_static["DUL"],  dtype=np.float64)
    ll15 = np.asarray(soil_static["LL15"], dtype=np.float64)
    sw   = np.asarray(sw_values,           dtype=np.float64)

    coeffs = setup_theta_curve(psi_dul, ll15, dul, sat)

    psi_out = np.zeros_like(sw)
    for layer in range(len(sw)):
        psi_out[layer] = suction_scalar(sw[layer], layer, coeffs)
    return psi_out.tolist()


# =========================================================
# УТИЛИТЫ
# =========================================================
def today_to_str(today) -> str:
    if today is None:
        return "?"
    if hasattr(today, "seconds"):
        try:
            return datetime.fromtimestamp(
                int(today.seconds), tz=timezone.utc
            ).strftime("%Y-%m-%d")
        except Exception:
            return str(today)[:10]
    s = str(today)
    return s[:10] if len(s) >= 10 else s


def clip_sw_to_valid_range(sw_values, lower, upper, eps=1e-6):
    sw = np.asarray(sw_values, dtype=np.float64)
    lo = np.asarray(lower, dtype=np.float64) + eps
    hi = np.asarray(upper, dtype=np.float64) - eps
    if np.any(hi <= lo):
        raise ValueError("Invalid soil-water bounds")
    n_below = int(np.sum(sw < lo))
    n_above = int(np.sum(sw > hi))
    return np.clip(sw, lo, hi).tolist(), n_below, n_above


def win_to_wsl_path(win_path: Path) -> str:
    """C:\\Users\\user\\file.apsimx -> /mnt/c/Users/user/file.apsimx"""
    p = str(win_path).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        p = f"/mnt/{drive}{p[2:]}"
    return p


def resolve_apsim_path(patched: Path) -> str:
    """
    Возвращает путь к .apsimx в формате, понятном выбранному APSIM:
      - windows: 'C:\\...\\patched.apsimx'
      - wsl:     '/mnt/c/.../patched.apsimx'
    """
    _require_apsim_config()
    if APSIM_MODE == "windows":
        return str(patched)
    return win_to_wsl_path(patched)


# =========================================================
# ПАТЧ .APSIMX
# =========================================================
def patch_apsimx_weather(src: Path, dst: Path,
                         identifier: str,
                         weather_met_abs: Path | None):
    """
    Копирует .apsimx в dst, подменяя:
      - Synchroniser.Identifier = identifier
      - Weather.FileName        = weather_met_abs в формате текущей ОС

    weather_met_abs — абсолютный Windows-путь (Path) или None.
    """
    _require_apsim_config()

    # Формат пути к погоде для APSIM, в зависимости от режима
    if weather_met_abs is not None:
        if APSIM_MODE == "windows":
            weather_path_for_apsim = str(weather_met_abs)          # C:\...\weather_2021_normalized.met
        elif APSIM_MODE == "wsl":
            weather_path_for_apsim = win_to_wsl_path(weather_met_abs)  # /mnt/c/.../weather_2021_normalized.met
        else:
            raise ValueError(f"Неизвестный APSIM_MODE: {APSIM_MODE}")
    else:
        weather_path_for_apsim = None

    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)

    found_sync = {"value": False}
    found_weather = {"value": False}

    def walk(node):
        if isinstance(node, dict):
            # 1) Synchroniser.Identifier
            if node.get("Name") == "Synchroniser" and "Manager" in node.get("$type", ""):
                for p in node.get("Parameters", []):
                    if p.get("Key") == "Identifier":
                        p["Value"] = identifier
                        found_sync["value"] = True

            # 2) Weather.FileName (первый найденный Weather-узел с FileName)
            if weather_path_for_apsim is not None \
                    and not found_weather["value"] \
                    and "Weather" in node.get("$type", "") \
                    and "FileName" in node:
                node["FileName"] = weather_path_for_apsim
                found_weather["value"] = True

            for ch in node.get("Children", []) or []:
                walk(ch)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)

    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    if found_sync["value"]:
        print(f"[patch] {dst.name}: Identifier = {identifier}")
    else:
        print(f"[patch] {dst.name}: WARNING — Synchroniser.Identifier не найден")

    if weather_path_for_apsim is not None:
        if found_weather["value"]:
            print(f"[patch] {dst.name}: Weather.FileName = {weather_path_for_apsim}")
        else:
            print(f"[patch] {dst.name}: WARNING — Weather.FileName не найден")


# =========================================================
# ЗАПУСК APSIM
# =========================================================
def start_apsim(apsimx_path: str) -> subprocess.Popen:
    _require_apsim_config()

    if APSIM_MODE == "windows":
        cmd = [APSIM_WIN_BIN, apsimx_path, "--csv"]
    elif APSIM_MODE == "wsl":
        cmd = [
            "wsl", "-d", WSL_DISTRO, "-u", WSL_USER, "--",
            APSIM_WSL_BIN, apsimx_path, "--csv",
        ]
    else:
        raise ValueError(f"Неизвестный APSIM_MODE: {APSIM_MODE}")

    print(f"[apsim] Запуск ({APSIM_MODE}): {' '.join(cmd)}")
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1)


def pipe_apsim_output(proc, log_path: Path):
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            for line in proc.stdout:
                f.write(line); f.flush()
    except Exception as e:
        print(f"[apsim-log] {e}")


# =========================================================
# ZMQ СЕРВЕР
# =========================================================
class PinnServer:
    def __init__(self, bridge, soil_static, sw_init, mode="replace"):
        self.bridge = bridge
        self.soil_static = soil_static
        self.day_idx = 0
        self.history = []
        self.mode = mode
        self._model_sw = list(sw_init)
        self._model_pond_mm = 0.0
        self._rain_history = deque(maxlen=bridge.history_days)
        self._evap_history = deque(maxlen=bridge.history_days)

        n = len(soil_static["thickness"])

        self.latest = {
            "soil_water_volumetric": np.zeros(n, dtype=np.float64),
            "pinn_soil_water_volumetric": np.zeros(n, dtype=np.float64),
            "soil_psi":              np.zeros(n, dtype=np.float64),
            "soil_psi_read":         np.zeros(n, dtype=np.float64),
            "rain":          0.0,
            "radiation":     0.0,
            "temp_max":      0.0,
            "temp_min":      0.0,
            "evap_soil":     0.0,
            "evap_potential": 0.0,
            "evap_potential_soil": 0.0,
            "pond_depth": 0.0,
            "pinn_q_top": 0.0,
            "pinn_q_bottom": 0.0,
            "pinn_runoff": 0.0,
            "pinn_evap_actual": 0.0,
            "pinn_pond_depth": 0.0,
            "pinn_sink_total": 0.0,
            "date":          0,
        }
        self.step_done = threading.Event()
        self.step_event = threading.Event()
        self._stop_flag = threading.Event()
        self._zmq_lock = threading.Lock()
        self._thread = None
        self._pending_psi = None
        self._pending_lock = threading.Lock()

        self.ctx = zmq.Context()
        self.sock = self.ctx.socket(zmq.DEALER)
        self.sock.setsockopt(zmq.RCVTIMEO, RCV_TIMEOUT_MS)
        self.sock.bind(ZMQ_BIND)
        print(f"[zmq] Bind {ZMQ_BIND} (mode={mode})")

    def stop(self, timeout: float = 2.0):
        self._stop_flag.set()
        try: self.step_done.set()
        except Exception: pass
        try: self.step_event.set()
        except Exception: pass

        if self._thread is not None:
            try:
                self._thread.join(timeout=timeout)
            except Exception:
                pass

        try: self.sock.close(linger=0)
        except Exception: pass
        try: self.ctx.term()
        except Exception: pass

    def set_pending_psi(self, psi_values):
        with self._pending_lock:
            self._pending_psi = list(psi_values)

    def _consume_pending_psi(self):
        with self._pending_lock:
            val = self._pending_psi
            self._pending_psi = None
        return val

    def _drain(self):
        old = self.sock.getsockopt(zmq.RCVTIMEO)
        self.sock.setsockopt(zmq.RCVTIMEO, DRAIN_TIMEOUT_MS)
        n = 0
        try:
            while True:
                try:
                    self.sock.recv_multipart(); n += 1
                except zmq.Again:
                    break
        finally:
            self.sock.setsockopt(zmq.RCVTIMEO, old)
        return n

    def _recv(self):
        try:
            return self.sock.recv_multipart()
        except zmq.Again:
            raise TimeoutError(f"[zmq] Таймаут ({RCV_TIMEOUT_MS} мс)")

    def _send(self, *frames):
        self.sock.send_multipart([f if isinstance(f, bytes) else str(f).encode()
                                  for f in frames])

    def get_var(self, name):
        with self._zmq_lock:
            self._drain()
            self._send("get", msgpack.packb(name))
            return msgpack.unpackb(self._recv()[0])

    def put_var(self, name, value):
        with self._zmq_lock:
            self._drain()
            self._send("put", msgpack.packb(name), msgpack.packb(value))
            ok = self._recv()[0].decode()
            if ok != "ok":
                raise RuntimeError(f"put('{name}') failed: {ok}")

    def _get(self, name): return self.get_var(name)
    def _put(self, name, value): return self.put_var(name, value)
    def _resume(self, cmd): self._send(cmd)

    def run(self):
        print("[zmq] Ожидание APSIM...")
        try:
            with self._zmq_lock:
                cmd = self._recv()[0].decode()
        except (TimeoutError, zmq.ZMQError):
            print("[zmq] Нет подключения APSIM, выход.")
            return

        if cmd != "connect":
            raise RuntimeError(f"Ожидался connect, получено {cmd}")
        self._send("ok")
        print("[zmq] APSIM подключился")

        while not self._stop_flag.is_set():
            try:
                with self._zmq_lock:
                    cmd = self._recv()[0].decode()
            except TimeoutError as e:
                print(str(e)); break
            except zmq.ZMQError:
                break
            except UnicodeDecodeError:
                continue

            if cmd == "paused1":
                self._handle_paused1()
            elif cmd == "paused":
                self._handle_paused()
            elif cmd == "finished":
                try:
                    self._send("ok")
                except Exception:
                    pass
                print("[zmq] APSIM завершил")
                break
            else:
                print(f"[zmq] Предупреждение: неожидаемая команда '{cmd}'")

            if STOP_AFTER_DAYS and self.day_idx >= STOP_AFTER_DAYS:
                print(f"[zmq] Лимит {STOP_AFTER_DAYS} дней")
                break

        self._save_history()

    def _handle_paused1(self):
        self.step_event.set()

        sw       = self.get_var(VAR_SW)
        rain     = self.get_var(VAR_RAIN)
        radn     = self.get_var(VAR_RADN)
        maxt     = self.get_var(VAR_MAXT)
        mint     = self.get_var(VAR_MINT)
        es       = self.get_var(VAR_ES)
        eo       = self.get_var(VAR_EO)
        eos      = self.get_var(VAR_EOS)
        pond     = self.get_var(VAR_POND)
        today    = self.get_var(VAR_TODAY)
        psi_old  = self.get_var(VAR_PSI_READ)

        sw_list      = [float(x) for x in sw]
        psi_old_list = [float(x) for x in psi_old]
        date_str     = today_to_str(today)

        # paused1 follows today's water movement. The operator's input SW is
        # yesterday's end-of-day state; today's rain/Eos end on the target day.
        soil_data = build_apsim_soil_data(self.soil_static, self._model_sw)
        self._rain_history.append(max(0.0, float(rain)))
        self._evap_history.append(max(0.0, float(eos)))
        h = self.bridge.history_days
        rain_history = [0.0] * (h - len(self._rain_history)) + list(self._rain_history)
        evap_history = [0.0] * (h - len(self._evap_history)) + list(self._evap_history)
        result = self.bridge.predict_next_state(
            apsim_soil_data=soil_data,
            target_time_days=1.0,
            rain_history=rain_history,
            evap_history=evap_history,
            pond_depth_mm=self._model_pond_mm,
        )
        sw_pinn = [float(x) for x in result["SW"]]

        sw_clip, n_below, n_above = clip_sw_to_valid_range(
            sw_pinn, np.zeros(len(sw_pinn)), self.soil_static["SAT"]
        )

        psi_new = compute_psi_mm_apsim(sw_clip, self.soil_static, PSI_DUL_MM)

        print(f"\n[day {self.day_idx:4d}] {date_str}")
        print(f"  P={float(rain):.3f} Radn={float(radn):.2f} "
              f"Tmax={float(maxt):.2f} Tmin={float(mint):.2f} "
              f"Es={float(es):.4f} Eos={float(eos):.4f} Eo={float(eo):.4f} "
              f"Pond={float(pond):.4f}")
        print(f"  SW_apsim  ={np.round(sw_list, 4).tolist()}")
        print(f"  SW_pinn   ={np.round(sw_pinn, 4).tolist()}")
        print(f"  SW_clip   ={np.round(sw_clip, 4).tolist()}  (below={n_below}, above={n_above})")
        print(f"  psi_old   ={np.round(psi_old_list, 1).tolist()}")
        print(f"  psi_new   ={np.round(psi_new, 1).tolist()}")

        psi_check_list = psi_old_list
        if self.mode == "replace":
            pending = self._consume_pending_psi()
            psi_to_write = pending if pending is not None else psi_new

            self.put_var(VAR_PSI_SWIM3, psi_to_write)
            print(f"  [put] {VAR_PSI_SWIM3}  "
                  f"(pending={'yes' if pending is not None else 'no'})")

            psi_check = self.get_var(VAR_PSI_READ)
            sw_check  = self.get_var(VAR_SW)
            psi_check_list = [float(x) for x in psi_check]
            sw_check_list  = [float(x) for x in sw_check]
            print(f"  [check] psi_after ={np.round(psi_check_list, 1).tolist()}")
            print(f"  [check] SW_after  ={np.round(sw_check_list, 4).tolist()}")

        self.history.append({
            "day": self.day_idx, "date": date_str,
            "SW_apsim": sw_list, "SW_pinn": sw_pinn, "SW_clip": sw_clip,
            "psi_old": psi_old_list, "psi_new": psi_new,
            "n_below": n_below, "n_above": n_above,
            "P": float(rain), "Radn": float(radn),
            "Tmax": float(maxt), "Tmin": float(mint),
            "E_s": float(es), "E_p": float(eo),
            "Eos": float(eos), "Pond": float(pond),
            **{k: result[k] for k in (
                "q_top_m_day", "q_bottom_m_day", "runoff_m_day",
                "evap_actual_m_day", "pond_m", "sink_total_m_day",
            )},
        })
        self.day_idx += 1

        self.latest["soil_water_volumetric"][:] = np.asarray(sw_list, dtype=np.float64)
        self.latest["pinn_soil_water_volumetric"][:] = np.asarray(sw_clip, dtype=np.float64)
        self.latest["soil_psi"][:]              = np.asarray(psi_new, dtype=np.float64)
        self.latest["soil_psi_read"][:]         = np.asarray(psi_check_list, dtype=np.float64)
        self.latest["rain"]           = float(rain)
        self.latest["radiation"]      = float(radn)
        self.latest["temp_max"]       = float(maxt)
        self.latest["temp_min"]       = float(mint)
        self.latest["evap_soil"]      = float(es)
        self.latest["evap_potential"] = float(eo)
        self.latest["evap_potential_soil"] = float(eos)
        self.latest["pond_depth"] = float(pond)
        for name, key in (
            ("pinn_q_top", "q_top_m_day"),
            ("pinn_q_bottom", "q_bottom_m_day"),
            ("pinn_runoff", "runoff_m_day"),
            ("pinn_evap_actual", "evap_actual_m_day"),
            ("pinn_pond_depth", "pond_m"),
            ("pinn_sink_total", "sink_total_m_day"),
        ):
            self.latest[name] = float(result[key])
        try:
            self.latest["date"] = int(date_str.replace("-", ""))
        except Exception:
            self.latest["date"] = 0

        self.step_done.set()

        try:
            self._resume("resume1")
        except Exception as e:
            print(f"[resume1] {e}")

    def _handle_paused(self):
        if self._stop_flag.is_set():
            return
        try:
            today = self.get_var(VAR_TODAY)
            sw = self.get_var(VAR_SW)
            pond = self.get_var(VAR_POND)
            self._model_sw = [float(x) for x in sw]
            self._model_pond_mm = float(pond)
            print(f"[eod] {today_to_str(today)}  "
                  f"SW={np.round([float(x) for x in sw], 4).tolist()}")
        except Exception as e:
            msg = str(e).lower()
            if "terminated" in msg or "context" in msg or "socket" in msg:
                return
            print(f"[eod] ошибка: {e}")
        try:
            self._resume("resume")
        except Exception:
            pass

    def _save_history(self):
        if not self.history:
            print("[save] История пуста.")
            return
        rows = []
        n_layers = len(self.history[0]["SW_apsim"])
        for h in self.history:
            row = {
                "day": h["day"], "date": h["date"],
                "P": h["P"], "Radn": h["Radn"],
                "Tmax": h["Tmax"], "Tmin": h["Tmin"],
                "E_s": h["E_s"], "E_p": h["E_p"],
                "Eos": h["Eos"], "Pond": h["Pond"],
                "n_below": h["n_below"], "n_above": h["n_above"],
            }
            for key in (
                "q_top_m_day", "q_bottom_m_day", "runoff_m_day",
                "evap_actual_m_day", "pond_m", "sink_total_m_day",
            ):
                row[key] = h[key]
            for i in range(n_layers):
                row[f"SWapsim_{i+1}"] = h["SW_apsim"][i]
                row[f"SWpinn_{i+1}"]  = h["SW_pinn"][i]
                row[f"SWclip_{i+1}"]  = h["SW_clip"][i]
                row[f"psiold_{i+1}"]  = h["psi_old"][i]
                row[f"psinew_{i+1}"]  = h["psi_new"][i]
            rows.append(row)
        df = pd.DataFrame(rows)
        out = OUT_DIR / f"pinn_run_{datetime.now():%Y%m%d_%H%M%S}.csv"
        df.to_csv(out, index=False)
        print(f"[OK] История: {out}")


# =========================================================
# MAIN (только для прямого запуска модуля)
# =========================================================
def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Читаем общий конфиг (bmi_config.json) — теперь это
    # единственное место, где задан APSIM_MODE и пути к APSIM.
    cfg_file = BASE_DIR / "config" / "bmi_config.json"
    if not cfg_file.exists():
        raise FileNotFoundError(
            f"Не найден {cfg_file}. "
            f"Создайте его (пример структуры — в README или bmi_apsim_pinn.setup())."
        )
    with open(cfg_file, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    apply_apsim_config(cfg)
    weather_met_abs = _resolve_weather_met(cfg)

    patched = OUT_DIR / "test_field_2021_patched.apsimx"
    patch_apsimx_weather(APSIMX_WIN, patched, ZMQ_IDENT, weather_met_abs)

    apsim_path = resolve_apsim_path(patched)
    print(f"[main] APSIM_MODE={APSIM_MODE}, путь: {apsim_path}")

    print("[main] Парсинг .apsimx...")
    apsim_meta = parse_apsimx(str(APSIMX_WIN))
    soil_static = apsim_meta["soil_static"]
    sw_init     = apsim_meta["sw_initial"]

    print("[main] Загрузка PINN...")
    bridge = APSIMPINNBridge(model_path=str(MODEL_PATH), device="cpu")

    proc = start_apsim(apsim_path)
    threading.Thread(
        target=pipe_apsim_output,
        args=(proc, OUT_DIR / "apsim_stdout.log"),
        daemon=True
    ).start()

    server = PinnServer(bridge, soil_static, sw_init, mode=MODE)
    server._thread = threading.Thread(target=server.run, daemon=True)
    server._thread.start()

    try:
        server._thread.join()
    except KeyboardInterrupt:
        print("\n[main] Прервано")
    except Exception as e:
        print(f"\n[main] Ошибка: {e}")
        import traceback; traceback.print_exc()
    finally:
        try:
            proc.terminate(); proc.wait(timeout=5)
        except Exception:
            try: proc.kill()
            except Exception: pass
        try: server.stop()
        except Exception: pass
        try: server._save_history()
        except Exception: pass
        print("[main] APSIM остановлен")


if __name__ == "__main__":
    main()
