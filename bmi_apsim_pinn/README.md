# APSIM-PINN Hybrid Model

Гибридная модель для прогнозирования влажности почвы, объединяющая физическую модель **APSIM** (Agricultural Production Systems sIMulator) с нейросетью **PINN** (Physics-Informed Neural Network) через **BMI** (Basic Model Interface).

## Описание

Проект реализует гибридный подход:

- **APSIM** отвечает за симуляцию агроэкосистемы (погода, растения, почва).
- **PINN** (SWIM3 MVP DeepONet) предсказывает влажность по слоям и поверхностные/нижние потоки.
- **ZMQ-сервер** обеспечивает обмен данными между APSIM и нейросетью в реальном времени.
- **BMI-обёртка** предоставляет стандартный интерфейс для интеграции с внешними системами (например, фреймворком **PyMT**).

## Структура проекта

```BMI_APSIM_PINN/
├── config/                     # Конфигурация и входные данные APSIM
│ ├── bmi_config.json           # Пути к модели и данным
│ ├── test_field_2021.apsimx
│ └── test_field_2021.db
├── examples/                   # Примеры запуска
│ ├── run_example_no_pymt.py    # Прямой запуск BMI-модели (без PyMT)
│ └── run_pymt.py               # Запуск через PyMT
├── src/                        # Исходный код
│ ├── init.py
│ ├── apsim_pinn_server.py      # ZMQ-сервер и логика гибрида
│ ├── bmi_apsim_pinn.py         # BMI-обёртка
│ ├── pinn_inference.py         # Адаптер checkpoint к APSIM
│ ├── pi_deeponet_apsim_swim3_mvp.py  # Архитектура для загрузки объекта модели
│ ├── prepare_pinn_input.py     # Парсинг .apsimx, .met, .db
│ └── meta/                     # Метаданные CSDMS для PyMT
│ └── ApsimPinn/
│ ├── api.yaml
│ └── info.yaml
├── tests/                      # Тесты (pytest)
│ ├── init.py
│ ├── conftest.py
│ └── test_bmi_apsim_pinn.py
├── weather/                    # Метеоданные
│ ├── weather_2021_normalized.met
│ └── weather_2021_normalized.txt
├── pyproject.toml              # Метаданные пакета, entry points
├── pytest.ini
└── README.md
```
## Требования

- **Python 3.10–3.11**

- **APSIM** — установленный и работающий одним из двух способов:
  - **Windows:** APSIM Next Gen 2024+ (инсталлятор или сборка из исходников).  
    Проверьте, что `Models.exe` есть по какому-то пути.
  - **WSL (Ubuntu):** собранный APSIM (например, `/home/user/ApsimX/bin/Release/net8.0/Models`).

  > Оба варианта могут стоять параллельно — переключение делается **без правки кода**, через `config/bmi_config.json` путем выставления в `"apsim_mode": "windows",` или `"apsim_mode": "wsl"`.

- **Windows 10/11 или Linux**

- **Conda** (рекомендуется) — для установки PyMT (опционально)

## Установка

### 1. Клонирование репозитория

```bash
git clone https://github.com/your-username/BMI_APSIM_PINN.git
cd BMI_APSIM_PINN
```

## 2. Создание conda-среды
Рекомендуется использовать conda, потому что PyMT распространяется только через conda-forge и не собирается из PyPI.
```
conda create -n pymt_env python=3.11 -c conda-forge
conda activate pymt_env
```

## 3. Зависимости
```
pip install -e .
```

## 4. Установка PyMT(опционально)
PyMT нужен только для связывания (coupling) с другими BMI-моделями. Если вы планируете запускать APSIM-PINN отдельно — этот шаг можно пропустить.

```bash
conda install -c conda-forge pymt
conda install -c conda-forge "setuptools<81"
```

## 5. APSIM в WSL
Убедитесь, что в WSL (Ubuntu) установлен APSIM:

```bash
# В WSL:
/home/user/ApsimX/bin/Release/net8.0/Models --version
```

При необходимости отредактируйте WSL_DISTRO, WSL_USER и APSIM_BIN в src/apsim_pinn_server.py




# Запуск
Прямой запуск (без PyMT)
```bash
python examples/run_example_no_pymt.py
```

Запуск с PyMT
```bash
python examples/run_pymt.py
```

# Тесты

```bash
# Быстрые тесты (без запуска APSIM)
pytest tests/ -v -m "not integration"
```

# Все тесты (требуют WSL и APSIM)
```
pytest tests/ -v -s
```

# Конфигурация
Файл config/bmi_config.json:

```json
{
  "apsimx": "config/test_field_2021.apsimx",
  "model":  "src/pi_deeponet_swim3_mvp.pth",
  "out_dir": "run_output",
  "mode": "replace"
}
```
Все относительные пути задаются от корня `bmi_apsim_pinn`. Обученный
`pi_deeponet_swim3_mvp.pth` нужно положить рядом с `src/pinn_inference.py`.
Файл должен содержать словарь `model`, `config`, `normalization`, созданный
`pi_deeponet_apsim_swim3_mvp.py`. Без него запуск завершается до старта APSIM.

## Как это работает

1. APSIM запускается в WSL и подключается к ZMQ-серверу.
2. Пауза `paused1` наступает после расчёта движения воды за текущий день.
   Мост берёт состояние `SW` и глубину пруда с конца предыдущего дня,
   параметры семи слоёв (`DUL`, `SAT`, `AirDry`, `LL15`, `KS`, толщины),
   текущие `Rain` и `Eos` и историю этих двух воздействий за 7 дней.
   В первые шесть дней недостающая история заполняется нулями.
3. DeepONet предсказывает `SW` на конец текущего дня и шесть величин:
   `q_top`, `q_bottom`, `runoff`, `evap_actual`, `pond`, `sink_total`.
   Влажность доступна как `pinn_soil_water_volumetric`, потоки и `pond`
   — через BMI с префиксом `pinn_` и в CSV истории.
   Потоки имеют единицы м/сутки, глубина пруда — м.
4. Предсказанная `SW` ограничивается диапазоном `(0, SAT)` и переводится
   в `PSI` через кривую APSIM. `PSI` записывается в SWIM3.
5. На `EndOfDay` считывается фактическое состояние APSIM для следующего шага.

## Зависимости

- Python 3.10+
- PyTorch, NumPy, Pandas
- PyZMQ, msgpack
- pytest (для тестов)
- WSL + APSIM (для полного запуска)
