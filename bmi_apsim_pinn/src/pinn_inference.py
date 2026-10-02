import os
import torch
import torch.nn as nn
import numpy as np
import pandas as pd

# ==========================================
# 1. КОНСТАНТЫ И МАСШТАБЫ МОДЕЛИ
# ==========================================
Z_SCALE = 1.0     # Метры (глубина профиля)
T_SCALE = 10.0    # Сутки (масштаб времени)
NUM_SENSORS = 20  # Количество точек начального состояния h_0


# ==========================================
# 2. АРХИТЕКТУРА NEURAL NETWORK & PHYSICS
# ==========================================
def theta_from_h(
    h: torch.Tensor,
    h_e: torch.Tensor,
    b: torch.Tensor,
    theta_s: torch.Tensor,
    theta_r: torch.Tensor
) -> torch.Tensor:
    """Модель Кэмпбелла со сглаживанием Хатсона-Касса (Hutson & Cass, 1987)"""
    h_e_safe = -torch.abs(h_e) - 1e-6
    b_safe = torch.clamp(b, min=0.1)

    h_i = h_e_safe * torch.pow((2.0 * b_safe) / (2.0 * b_safe + 1.0), -b_safe)

    th_sat = theta_s
    parabolic_factor = (h ** 2) / ((2.0 * b_safe + 1.0) * (h_i ** 2) + 1e-10)
    th_smooth = theta_r + (theta_s - theta_r) * (1.0 - parabolic_factor)

    h_safe = torch.clamp(h, max=-1e-10)
    campbell_ratio = torch.clamp(h_e_safe / h_safe, max=1.0)
    th_campbell = theta_r + (theta_s - theta_r) * torch.pow(campbell_ratio, 1.0 / b_safe)

    return torch.where(h >= 0.0, th_sat, torch.where(h >= h_i, th_smooth, th_campbell))


def h_from_theta_campbell(theta: float, theta_s: float, theta_r: float, h_e: float, b: float) -> float:
    """Обратный пересчет влажности theta -> напор h [м] для формирования h_0"""
    se = (theta - theta_r) / (theta_s - theta_r)
    se = np.clip(se, 1e-4, 1.0)
    if se >= 0.999:
        return 0.0
    return float(h_e * (se ** (-b)))


class BranchNet(nn.Module):
    def __init__(self, in_dim: int, hidden_dim: int, p: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, p)
        )

    def forward(self, u_in):
        return self.net(u_in)


class TrunkNet(nn.Module):
    def __init__(self, in_dim: int = 2, hidden_dim: int = 64, p: int = 64):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.Tanh(),
            nn.Linear(hidden_dim, p)
        )

    def forward(self, coords_hat):
        return self.net(coords_hat)


class PIDeepONet(nn.Module):
    def __init__(self, branch_in_dim: int = 28, hidden_dim: int = 64, p: int = 32):
        super().__init__()
        self.branch = BranchNet(branch_in_dim, hidden_dim, p)
        self.trunk = TrunkNet(2, hidden_dim, p)
        self.b0 = nn.Parameter(torch.zeros(1))

    def forward(self, u_in, coords_hat):
        b_out = self.branch(u_in)
        if coords_hat.dim() == 2:
            t_out = self.trunk(coords_hat)
            return torch.matmul(b_out, t_out.T) + self.b0
        elif coords_hat.dim() == 3:
            B, N, _ = coords_hat.shape
            t_out = self.trunk(coords_hat.reshape(-1, 2)).reshape(B, N, -1)
            raw_out = torch.sum(b_out.unsqueeze(1) * t_out, dim=-1, keepdim=True) + self.b0
            return -15.0 + 15.15 * torch.sigmoid(raw_out / 5.0)


# ==========================================
# 3. КЛАСС ИНТЕГРАЦИИ И АДАПТЕРА APSIM -> PINN
# ==========================================
class APSIMPINNBridge:
    def __init__(self, model_path: str, device: str = 'cpu'):
        self.device = device
        self.model = PIDeepONet(branch_in_dim=28, hidden_dim=64, p=32).to(self.device)

        if os.path.exists(model_path):
            loaded = torch.load(model_path, weights_only=False, map_location=self.device)

            if hasattr(loaded, "state_dict") and not isinstance(loaded, dict):
                self.model.load_state_dict(loaded.state_dict())
                print(f"[OK] Загружен объект модели из {model_path}")
            elif isinstance(loaded, dict):
                if "state_dict" in loaded:
                    self.model.load_state_dict(loaded["state_dict"])
                else:
                    self.model.load_state_dict(loaded)
                print(f"[OK] Загружен state_dict из {model_path}")
            else:
                raise RuntimeError(f"Неизвестный формат файла {model_path}: {type(loaded)}")

            self.model.eval()
        else:
            print(f"[WARN] Файл модели {model_path} не найден. Используются случайные веса для теста.")

    def convert_apsim_to_campbell(self, dul: float, ll15: float, sat: float) -> tuple[float, float]:
        psi_dul = -0.33
        psi_ll15 = -15.0
        b = float(np.log(psi_ll15 / psi_dul) / np.log(dul / ll15))
        h_e = float(psi_dul * ((dul / sat) ** b))
        return b, h_e

    def predict_next_state(
        self,
        apsim_soil_data: dict,
        climate_data: dict,
        target_time_days: float = 1.0
    ) -> dict:
        # 1. Извлечение параметров слоев APSIM
        thickness = np.array(apsim_soil_data["thickness"], dtype=np.float32)
        sw_current = np.array(apsim_soil_data["SW"], dtype=np.float32)
        sat = float(np.mean(apsim_soil_data["SAT"]))
        dul = float(np.mean(apsim_soil_data["DUL"]))
        ll15 = float(np.mean(apsim_soil_data["LL15"]))
        KS_mm_day = float(np.mean(apsim_soil_data["KS"]))

        # 2. Перевод в СИ
        thickness_m = thickness / 1000.0
        depth_nodes_m = np.cumsum(thickness_m) - thickness_m / 2.0
        K_s = KS_mm_day / 1000.0
        theta_s = sat
        theta_r = float(np.mean(apsim_soil_data.get("AirDry", ll15 * 0.5)))

        b, h_e = self.convert_apsim_to_campbell(dul, ll15, sat)

        # 3. Профиль h_0
        z_sensors_m = np.linspace(0.0, Z_SCALE, NUM_SENSORS)
        h_sensors = []
        for z_s in z_sensors_m:
            idx = int(np.argmin(np.abs(depth_nodes_m - z_s)))
            sw_val = sw_current[idx]
            h_val = h_from_theta_campbell(sw_val, theta_s, theta_r, h_e, b)
            h_sensors.append(h_val)

        h_sensors_arr = np.array(h_sensors, dtype=np.float32)

        # 4. Климат
        E_p = float(climate_data.get("E_p", 0.0)) / 1000.0
        E_s = float(climate_data.get("E_s", 0.0)) / 1000.0
        P   = float(climate_data.get("P", 0.0))   / 1000.0

        # 5. Входной вектор
        u_in_np = np.concatenate([
            h_sensors_arr,
            np.array([E_p, E_s, P], dtype=np.float32),
            np.array([K_s, theta_r, theta_s, h_e, b], dtype=np.float32)
        ]).reshape(1, -1)

        u_in_tensor = torch.tensor(u_in_np, dtype=torch.float32, device=self.device)

        # 6. Предсказание
        t_hat_eval = target_time_days / T_SCALE
        predicted_sw = []
        layer_top_m = 0.0

        with torch.no_grad():
            for i, layer_thick in enumerate(thickness_m):
                layer_bot_m = layer_top_m + layer_thick
                z_subpoints_m = np.linspace(layer_top_m, layer_bot_m, 10)
                z_subpoints_hat = z_subpoints_m / Z_SCALE

                coords_eval = torch.tensor(
                    np.stack([z_subpoints_hat, np.full_like(z_subpoints_hat, t_hat_eval)], axis=-1),
                    dtype=torch.float32, device=self.device
                )

                pred_h = self.model(u_in_tensor, coords_eval).squeeze(0)

                he_t = torch.tensor(h_e, device=self.device)
                b_t  = torch.tensor(b, device=self.device)
                ts_t = torch.tensor(theta_s, device=self.device)
                tr_t = torch.tensor(theta_r, device=self.device)

                pred_theta = theta_from_h(pred_h, he_t, b_t, ts_t, tr_t)
                avg_sw_layer = float(torch.mean(pred_theta).cpu().numpy())
                predicted_sw.append(round(avg_sw_layer, 4))
                layer_top_m = layer_bot_m

        # 7. Результат
        apsim_output = {
            "thickness": apsim_soil_data["thickness"],
            "SW": predicted_sw,
            "SW_mm": [round(sw * dl, 2) for sw, dl in zip(predicted_sw, apsim_soil_data["thickness"])],
            "target_time_days": target_time_days,
            "status": "SUCCESS"
        }
        return apsim_output