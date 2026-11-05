import os
from aquacrop_bmi_rs import AquaCropBmi
import numpy as np

# Путь к тестовым данным
TESTCASE_ROOT = os.path.join("third-party", "KUL-RSDA", "AquaCrop", "testcase")

def read_file(relative_path):
    with open(os.path.join(TESTCASE_ROOT, relative_path), encoding="utf-8") as f:
        return f.read()

# Собираем конфигурацию в словарь (как в тестах)
scenario = {
    "season_year": 1,
    "crop_text": read_file("DATA/AlfOttawaGDD.CRO"),
    "soil_text": read_file("DATA/Ottawa.SOL"),
    "management_text": read_file("DATA/Ottawa.MAN"),
    "temperature_text": read_file("DATA/Ottawa.Tnx"),
    "rain_text": read_file("DATA/Ottawa.PLU"),
    "eto_text": read_file("DATA/Ottawa.ETo"),
    "co2_text": read_file("SIMUL/MaunaLoa.CO2"),
    "simulation_start": {"year": 2014, "month": 5, "day": 21},
    "simulation_end": {"year": 2014, "month": 10, "day": 31},
    "crop_start": {"year": 2014, "month": 5, "day": 21},
    "crop_end": {"year": 2014, "month": 10, "day": 31},
}

# Инициализируем модель
model = AquaCropBmi()
model.initialize_from_dict(scenario)

print(f"Модель запущена. Всего дней симуляции: {int(model.get_end_time())}")

# Запускаем симуляцию по дням
days = 0
end_time = model.get_end_time()
while model.get_current_time() < end_time:
    model.update()
    days += 1

print(f"Симуляция завершена за {days} шагов.")

# Получаем итоговый урожай (plant_yield)
dest = np.zeros(1)
model.get_value("plant_yield~standard", dest)

print(f"Итоговый урожай (plant_yield): {dest[0]:.3f} т/га")

# Завершаем работу
model.finalize()
print("Успешно завершено!")