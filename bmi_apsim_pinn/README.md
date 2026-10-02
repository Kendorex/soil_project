# APSIM-PINN Hybrid Model

Гибридная модель для прогнозирования влажности почвы, объединяющая физическую модель **APSIM** (Agricultural Production Systems sIMulator) с нейросетью **PINN** (Physics-Informed Neural Network) через **BMI** (Basic Model Interface).

## Описание

Проект реализует гибридный подход:

- **APSIM** отвечает за симуляцию агроэкосистемы (погода, растения, почва).
- **PINN** (DeepONet) предсказывает динамику влажности почвы на основе уравнения Ричардса.
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
│ ├── pinn_inference.py         # Нейросеть и физика Кэмпбелла
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
  "model":  "pi_deeponet_apsim_campbell.pth",
  "out_dir": "run_output",
  "mode": "replace"
}
```
Все пути относительны корня проекта.

## Как это работает

1. APSIM запускается в WSL и подключается к ZMQ-серверу.
2. На каждом шаге (`paused1`) APSIM передаёт текущее состояние почвы.
3. PINN предсказывает новую влажность `SW` на основе:
   - текущего профиля влажности (20 сенсоров),
   - климата (`P`, `E_s`, `E_p`),
   - параметров почвы Кэмпбелла (`b`, `h_e`, `K_s`, `θ_s`, `θ_r`).
4. Влажность клипуется в диапазон `[LL15, DUL]` и пересчитывается в потенциал `psi`.
5. `psi` записывается обратно в APSIM, который продолжает симуляцию.

## Зависимости

- Python 3.10+
- PyTorch, NumPy, Pandas
- PyZMQ, msgpack
- pytest (для тестов)
- WSL + APSIM (для полного запуска)
