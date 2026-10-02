import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.bmi_apsim_pinn import BmiApsimPinn


def main():
    # 1. Создаём модель
    model = BmiApsimPinn()

    # 2. Готовим конфиг (setup() создаст его, если нет)
    cfg_file, cfg_dir = model.setup()
    print(f"[main] Конфиг: {cfg_file}")

    # 3. Инициализация — запускает APSIM в WSL и ZMQ-сервер
    model.initialize(cfg_file)

    print(f"[main] Имя:     {model.get_component_name()}")
    print(f"[main] Старт:   {model.get_start_time()}")
    print(f"[main] Конец:   {model.get_end_time()}")
    print(f"[main] Шаг:     {model.get_time_step()} {model.get_time_units()}")
    print(f"[main] Входные: {model.get_input_var_names()}")
    print(f"[main] Выходные:{model.get_output_var_names()}")
    print()

    # 4. Основной цикл по дням
    while model.get_current_time() < model.get_end_time():
        model.update()

        t = model.get_current_time()
        psi = model.get_value("soil_psi")
        sw = model.get_value("soil_water_volumetric")

        print(f"t={t:6.1f}  "
              f"psi[0:3]={psi[:3].round(1).tolist()}  "
              f"sw[0:3]={sw[:3].round(4).tolist()}")

    # 5. Завершение — останавливает APSIM и ZMQ
    model.finalize()
    print("\n[main] Готово.")


if __name__ == "__main__":
    main()