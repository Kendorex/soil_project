"""
prepare_pinn_input.py
Парсит .apsimx, .met и (опционально) .db, готовит словари
для APSIMPINNBridge.predict_next_state().
"""
import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
import numpy as np


# =========================================================
# 1. ПАРСЕР .APSIMX
# =========================================================
def _clean_type(type_str: str) -> str:
    """Очищает строку типа от лишних данных (например, версии сборки)."""
    if not type_str:
        return ""
    return type_str.split(",")[0].strip()


def _find_child(node, type_name: str):
    """Рекурсивно ищет первого ребёнка с $type, соответствующим type_name."""
    if not isinstance(node, dict):
        return None
    for ch in node.get("Children", []) or []:
        if _clean_type(ch.get("$type", "")) == type_name:
            return ch
    for ch in node.get("Children", []) or []:
        found = _find_child(ch, type_name)
        if found is not None:
            return found
    return None


def _find_by_name(node, name: str):
    """Рекурсивно ищет узел с Name == name."""
    if not isinstance(node, dict):
        return None
    if node.get("Name") == name:
        return node
    for ch in node.get("Children", []) or []:
        found = _find_by_name(ch, name)
        if found is not None:
            return found
    return None


def parse_apsimx(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:   # utf-8-sig — на случай BOM
        root = json.load(f)

    # --- Clock ---
    clock = _find_child(root, "Models.Clock")
    start_date = clock["Start"][:10] if clock else None
    end_date   = clock["End"][:10]   if clock else None
    print(f"[parse_apsimx] Clock: {start_date} .. {end_date}")

    # --- Soil ---
    soil = _find_child(root, "Models.Soils.Soil")
    if soil is None:
        # Диагностика: покажем все типы, содержащие 'Soil', чтобы понять, что не так
        def collect_types(n, acc):
            if isinstance(n, dict):
                t = _clean_type(n.get("$type", ""))
                if "Soil" in t:
                    acc.add(t)
                for ch in n.get("Children", []) or []:
                    collect_types(ch, acc)
        acc = set()
        collect_types(root, acc)
        raise RuntimeError(
            "Не найден узел Models.Soils.Soil. "
            f"Найдены типы с 'Soil': {sorted(acc)}"
        )

    physical = _find_child(soil, "Models.Soils.Physical")
    water    = _find_child(soil, "Models.Soils.Water")
    if physical is None:
        raise RuntimeError("В Soil не найден узел Models.Soils.Physical")
    if water is None:
        raise RuntimeError("В Soil не найден узел Models.Soils.Water")

    soil_static = {
        "thickness": [float(x) for x in physical["Thickness"]],
        "SAT":       [float(x) for x in physical["SAT"]],
        "DUL":       [float(x) for x in physical["DUL"]],
        "LL15":      [float(x) for x in physical["LL15"]],
        "AirDry":    [float(x) for x in physical["AirDry"]],
        "KS":        [float(x) for x in physical["KS"]],
    }
    sw_initial = [float(x) for x in water["InitialValues"]]

    n = len(soil_static["thickness"])
    for k, v in soil_static.items():
        if len(v) != n:
            raise ValueError(f"Длина {k} ({len(v)}) != число слоёв ({n})")
    if len(sw_initial) != n:
        raise ValueError(f"Длина InitialValues ({len(sw_initial)}) != число слоёв ({n})")

    return {
        "soil_static": soil_static,
        "sw_initial": sw_initial,
        "start_date": start_date,
        "end_date": end_date,
    }


# =========================================================
# 2. ПАРСЕР .MET
# =========================================================
def parse_met(path: str) -> list[dict]:
    """
    Возвращает список словарей по дням:
    [{year, day, radn, maxt, mint, rain, wind_speed, sp}, ...]
    """
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("!") or line.startswith("["):
                continue
            # пропускаем строки-заголовки (начинаются с буквы или содержат скобки)
            first = line.split()[0]
            if not first.isdigit():
                continue
            parts = line.split()
            if len(parts) < 8:
                continue
            rows.append({
                "year":       int(parts[0]),
                "day":        int(parts[1]),
                "radn":       float(parts[2]),
                "maxt":       float(parts[3]),
                "mint":       float(parts[4]),
                "rain":       float(parts[5]),
                "wind_speed": float(parts[6]),
                "sp":         float(parts[7]),
            })
    return rows


def met_to_date(year: int, day_of_year: int) -> str:
    return (datetime(year, 1, 1) + timedelta(days=day_of_year - 1)).strftime("%Y-%m-%d")


# =========================================================
# 3. ПАРСЕР .DB
# =========================================================
def list_tables(db_path: str) -> list[str]:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]
    con.close()
    return tables


def read_report(db_path: str, table: str = "Report") -> tuple[list[str], list[tuple]]:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute(f"PRAGMA table_info('{table}')")
    cols = [r[1] for r in cur.fetchall()]
    cur.execute(f"SELECT * FROM '{table}'")
    rows = cur.fetchall()
    con.close()
    return cols, rows


def extract_sw_series(db_path: str, table: str = "Report") -> dict:
    cols, rows = read_report(db_path, table)
    date_idx = None
    for i, c in enumerate(cols):
        if c.lower() in ("clock.today", "today", "date"):
            date_idx = i
            break
    if date_idx is None:
        raise RuntimeError(f"Не найдена колонка с датой в {table}: {cols}")

    sw_idx = [i for i, c in enumerate(cols) if c.upper().startswith("SW(")]
    if not sw_idx:
        raise RuntimeError(
            f"Не найдены колонки SW(...). Доступные колонки: {cols}"
        )

    out = {}
    for r in rows:
        date_val = str(r[date_idx])[:10]
        out[date_val] = [float(r[i]) for i in sw_idx]
    return out


# =========================================================
# 4. СБОРКА СЛОВАРЕЙ ДЛЯ BRIDGE
# =========================================================
def build_apsim_soil_data(soil_static: dict, sw_current: list[float]) -> dict:
    return {
        "thickness": soil_static["thickness"],
        "SW":        list(sw_current),
        "SAT":       soil_static["SAT"],
        "DUL":       soil_static["DUL"],
        "LL15":      soil_static["LL15"],
        "AirDry":    soil_static["AirDry"],
        "KS":        soil_static["KS"],
    }


def build_climate_data(met_row: dict,
                       e_s_mm: float | None = None,
                       e_p_mm: float | None = None) -> dict:
    P = float(met_row["rain"])
    if e_s_mm is None:
        e_s_mm = max(0.0, 0.2 * met_row["radn"])
    if e_p_mm is None:
        tmean = 0.5 * (met_row["maxt"] + met_row["mint"])
        e_p_mm = max(0.0, 0.15 * max(0.0, tmean))
    return {"P": P, "E_s": e_s_mm, "E_p": e_p_mm}


# =========================================================
# 5. ДЕМО
# =========================================================
def prepare_all(
    apsimx_path: str,
    met_path: str,
    db_path: str | None = None,
    report_table: str = "Report",
    out_dir: str = "prepared",
) -> dict:
    os.makedirs(out_dir, exist_ok=True)

    print("[1/3] Парсинг .apsimx...")
    apsim = parse_apsimx(apsimx_path)
    print(f"      слоёв: {len(apsim['soil_static']['thickness'])}")
    print(f"      период: {apsim['start_date']} .. {apsim['end_date']}")

    print("[2/3] Парсинг .met...")
    met = parse_met(met_path)
    print(f"      дней: {len(met)}")

    soil_init = build_apsim_soil_data(apsim["soil_static"], apsim["sw_initial"])

    sw_series = None
    if db_path and os.path.exists(db_path):
        print("[3/3] Парсинг .db...")
        try:
            sw_series = extract_sw_series(db_path, report_table)
            print(f"      дней с SW: {len(sw_series)}")
        except Exception as e:
            print(f"      [!] Не удалось прочитать SW из .db: {e}")

    bundle = {
        "start_date": apsim["start_date"],
        "end_date":   apsim["end_date"],
        "soil_static": apsim["soil_static"],
        "sw_initial":  apsim["sw_initial"],
        "soil_init_for_bridge": soil_init,
        "weather": [
            {**row, "date": met_to_date(row["year"], row["day"])}
            for row in met
        ],
    }
    if sw_series:
        bundle["sw_series"] = sw_series

    out_path = os.path.join(out_dir, "pinn_input_bundle.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, ensure_ascii=False)
    print(f"\n[OK] Сохранено: {out_path}")

    return bundle


if __name__ == "__main__":
    base = Path(__file__).parent.parent  # Поднимаемся на уровень выше из src/
    prepare_all(
        apsimx_path=str(base / "config" / "test_field_2021.apsimx"),
        met_path   =str(base / "weather" / "weather_2021_normalized.met"),
        db_path    =str(base / "config" / "test_field_2021.db"),
        report_table="Report",
        out_dir    =str(base / "prepared"),
    )