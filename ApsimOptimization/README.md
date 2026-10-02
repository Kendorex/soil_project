# APSIMX: многополевая и многокультурная оптимизация удобрений

### Component-aware oat validation

Saved oat diagnostic runs can be compared without rerunning APSIM:

```bash
python3 scripts/evaluate_oats_component_validation.py
```

With no arguments the script compares the control and candidate from the most
recent `OATS_GRAIN_N_CEILING` experiment. It writes separate development
(2020-2022) and validation (2023) errors for yield, phenology, dry biomass,
thousand-grain mass, and yield-derived grain number. The generated
`oats_component_validation_*.tar.gz` is the compact review artifact.

Актуальная постановка использует воспроизводимо собранную модель
`full_field2019_2023_calibrated_v2.apsimx`, исправленную по единицам погоду
`weather_2019_2023_units_corrected.met`, 10 полей и NSGA-II из `pymoo`.
Хеши исходников, перенесённые параметры калибровки и все изменения модели записаны
в соседних `*.provenance.json` и проверяются перед запуском.
Цели: урожай выбранного года, чистая прибыль и соблюдение диапазонов почвенных
показателей. Тип/смесь и доза удобрения задаются отдельно по полям.
Один кандидат может рассчитываться по нескольким подтверждённым вариантам модели
или погоды; результаты сохраняют средние/худшие цели и диапазоны по сценариям.
Сейчас активен только откалиброванный базовый сценарий, поскольку альтернативный
водный режим ещё не подтверждён.

Начните с `python run_optimizer.py inspect`. Рабочая конфигурация:
`configs/multifield.yaml`. Диапазоны доз и почвы, цены и прочие доменные параметры
оставлены незаполненными до подтверждения. Базовый расчёт доступен командой
`python run_optimizer.py baseline --year 2023`; производственная оптимизация —
`python run_optimizer.py optimize` после успешного `validate`.

Для воспроизводимой проверки без утверждения агрономических норм подготовлен
`configs/multifield_2023_n_diagnostic.yaml`. Он использует исторический диапазон
N30–N60, старые экономические прокси и отклонение от модельного baseline по
NO3+NH4 как третью цель. Эти значения служат только для диагностики кода и
отклика APSIM.

Историческая готовность проверяется командой:

```bash
APSIM_MODELS=/absolute/path/to/Models \
  python3 scripts/evaluate_historical_replay.py --run
```

Архив содержит `decision_readiness.json`. Рекомендационный режим допускается
только при полном покрытии 2023 года, наличии уборок, RMSE ниже прогноза средним
за 2019–2022 годы и положительном ранжировании полей по Spearman. Абсолютный порог
ошибки не придумывается. Калибровочный опыт компонентов урожая овса запускается
`python3 scripts/calibrate_oats_yield_components.py --run`; кандидаты выбираются
на 2019–2022 годах и отдельно проверяются на 2023 годе.
Следующий опыт листового аппарата и кущения запускается командой
`python3 scripts/calibrate_oats_canopy_structure.py --run`. Он использует точные
дискретные значения Drummond и Milton из локального ресурса APSIM, сохраняя
откалиброванную фенологию Yakov.
Факторная проверка влияния унаследованного состояния Field2/Field7 и известных
меток N30/N60 выполняется командой
`python3 scripts/diagnose_oats_field_state.py --run`. Она сохраняет четыре
сопоставимых прогона, таблицы для графиков и архив с хешами и журналами.
Происхождение исторических доз проверяется командой
`python3 scripts/audit_historical_management.py`, а чувствительность овса к
пропущенным событиям — `python3 scripts/diagnose_oats_missing_history.py --run`.
Структуру почвенных входов по полям можно проверить командой
`python3 scripts/audit_field_soils.py`.

Полная проверка на Ubuntu одной командой:

```bash
APSIM_MODELS=/absolute/path/to/Models \
  bash scripts/run_multifield_ubuntu_check.sh
```

Скрипт запускает тесты, baseline, записывает checkpoint качества, выполняет
короткий реальный NSGA-II и изолированные диагностики N, конкуренции овса,
срока N для ржи, перехода травы → озимая культура и укоренения яровых культур
2019 года. Затем он строит PNG-графики и создаёт
`multifield_check_<timestamp>.tar.gz`. Архив содержит CSV-историю качества,
графики и может быть передан для анализа без ручного поиска каталогов.

Расширенная проверка механизма водного ограничения запускается отдельно:

```bash
python3 scripts/diagnose_soil_root_access.py --run
```

Она использует только уже заданные в APSIMX точки `AirDry`, текущий crop `LL` и
`DUL`. Варианты являются инженерной диагностикой и не становятся почвенными
параметрами или границами оптимизации.

После этой проверки расширенный диагностический Pareto-фронт строится командой:

```bash
APSIM_MODELS=/absolute/path/to/Models \
  bash scripts/run_multifield_pareto_check.sh
```

По умолчанию сценарий использует 12 решений и 3 поколения, затем создаёт архив
`multifield_pareto_<timestamp>.tar.gz` с JSON, компактными CSV и графиками целей
и доз Pareto.

**[Полная инструкция: запуск в WSL, конфигурация, единицы, результаты](docs/MULTIFIELD.md).**

## Прежний пшеничный режим и калибровочные эксперименты

Ниже описан сохранённый прежний режим. Он вызывается старыми флагами, например
`--optimize-nsga2`, и не используется новой командой `optimize`.

Проект подбирает экономически эффективную технологию яровой пшеницы. Python
связывает имитационную модель APSIMX с алгоритмом NSGA-II из библиотеки
`pymoo`: оптимизатор предлагает технологию, APSIMX рассчитывает урожайность,
после чего Python определяет прибыль и возвращает целевые значения в NSGA-II.

## Как работает программа

```text
optimizer_config.yaml
→ кандидат NSGA-II
→ временные копии standart.apsimx
→ APSIMX по активным погодным сценариям
→ SQLite Report
→ урожайность и экономика
→ цели NSGA-II
→ следующее поколение
```

Исходный файл `simulation/standart.apsimx` не изменяется. Для каждой
технологии и каждого погодного сценария создаётся отдельная рабочая копия.
В актуальном шаблоне площадь APSIMX Zone задана как `1.0` га.

## Текущее пространство поиска

Основная конфигурация `configs/optimizer_config.yaml` содержит:

| Параметр | Значения |
|---|---|
| Правило посева | `1-may..30-may`, фиксировано |
| Сорт | `Zlata`, `Irgina` |
| Population | 250, 300, 350, 400, 450, 500 растений/м² |
| Глубина | 20, 30, 40, 50, 60 мм |
| Междурядье | 125, 150, 250 мм |
| Удобрение | 29 готовых схем |

Схемы удобрения включают контроль без удобрения и четыре формы азотного
удобрения по семи дозам. Фосфорные и калийные схемы сейчас не моделируются.

Активны три сценария: `s1_dojdey`, `s3_norm`, `s4_zasuha`.
`s2_holod` сохранён в конфигурации, но временно отключён. Полное пространство
содержит 5220 технологий и 15660 сценарных APSIMX-прогонов.

## Цели NSGA-II

```text
f1 = -profit_avg
f2 = controllable_cost
f3 = -profit_min
```

- `profit_avg` — средняя прибыль по активным погодным сценариям;
- `profit_min` — прибыль в худшем сценарии;
- `controllable_cost` — стоимость удобрения, внесения, финансирования и
  изменяемая часть стоимости семян.

Постоянная статья 23 000 руб./га входит в прибыль, но не входит в
`controllable_cost`, поскольку одинакова для всех технологий.

## Установка в Ubuntu или WSL

Путь проекта может быть любым. Машинный путь к APSIMX передаётся переменной
`APSIM_MODELS`, поэтому YAML редактировать не требуется.

```bash
cd /path/to/ApsimOptimization_Work_in_prog
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -r requirements.txt
APSIM_MODELS=/path/to/ApsimX/bin/Release/net8.0/Models \
  bash scripts/run_multifield_ubuntu_check.sh
```

## Быстрая демонстрация

Подробные подписанные команды находятся в `DEMO_COMMANDS.txt`.

```bash
python3 -m pytest tests
python3 run_optimizer.py --config configs/optimizer_config_demo.yaml --check-env
python3 run_optimizer.py --config configs/optimizer_config_demo.yaml --dry-run
python3 run_optimizer.py --config configs/optimizer_config_demo.yaml --optimize-nsga2 --ignore-cache
```

Демонстрационная конфигурация содержит 64 возможные технологии. NSGA-II
использует 6 кандидатов и 2 поколения: не более 12 оценок технологий и 36
новых APSIMX-прогонов по трём сценариям.

Готовая последовательность запуска:

```bash
bash scripts/run_demo_wsl.sh
```

## Основные режимы

```bash
python3 run_optimizer.py --check-env
python3 run_optimizer.py --dry-run
python3 run_optimizer.py --single-run --scenario s3_norm --scheme F0_no_fertilizer
python3 run_optimizer.py --evaluate-technology --scheme F0_no_fertilizer
python3 run_optimizer.py --optimize-nsga2
python3 run_optimizer.py --full-grid --allow-large-grid
python3 run_optimizer.py --diagnose-soil-outputs
python3 run_optimizer.py --test-soil-output-candidates --scenario s3_norm
python3 run_optimizer.py --validate-outputs
```

### Проверка запуска из APSIMX GUI

`simulation/standart.apsimx` является шаблоном, а не готовым универсальным
сезоном. Для GUI следует открывать подготовленную временную копию с уже
зафиксированными Clock, погодой и технологией. Один переносимый файл и его
`run_manifest.json` создаются командой:

```bash
python3 run_optimizer.py --prepare-gui-run --scenario s3_norm --scheme F0_no_fertilizer
```

Путь к файлу, который нужно открыть в APSIMX GUI, будет напечатан в консоли.
После нажатия Run результат проверяется той же строгой политикой, что и
консольный запуск:

```bash
python3 scripts/validate_apsim_run.py path/to/prepared_run.apsimx
```

Для строгой проверки версии APSIM передайте путь к `Models.dll`, использованному
GUI: `--models-dll path/to/Models.dll`. Его SHA-256 должен совпасть со значением
в `run_manifest.json`.

Проверка возвращает ненулевой код и не допускает результат в оптимизацию, если
культура не была посеяна, не достигла `HarvestRipe` или урожайность равна нулю.
Она также проверяет переносимые относительные пути, полное покрытие `Clock`
погодными данными и физический порядок `AirDry <= LL15 < DUL < SAT`.

Основной full-grid содержит 15660 прогонов и может работать много часов.

## Входные данные

- `simulation/standart.apsimx` — базовая APSIMX-модель;
- `data/weather/` — погодные сценарии;
- `data/cultivars/cultivars.txt` — доступные сорта;
- `data/fertilizers/fertilizer_schemes.json` — схемы удобрения;
- `data/economics/` — цены и экономические константы;
- `docs/20260804_ВНИИМЗ_трансекта/` — исходные полевые материалы для
  калибровочных режимов.

## Выходные файлы

Основная конфигурация пишет результаты в `outputs/`, демонстрационная — в
`outputs/demo/`.

- `results_runs.csv` — отдельные APSIMX-прогоны;
- `results_technologies.csv` — агрегация одной технологии по сценариям;
- `nsga2_evaluations.csv` — кандидаты, оценённые NSGA-II;
- `pareto_nsga2.csv` — Парето-фронт;
- `best_solution.json` — лучшее по прибыли, самое дешёвое и компромиссное
  решения;
- `optimizer_log.txt` — поколения, кандидаты, APSIMX runs, cache и цели;
- `validation_report.json`, `validation_report.txt` — проверка полного
  комплекта результатов.

## Структура проекта

```text
configs/       конфигурации оптимизации, диагностики и калибровки
data/          погода, сорта, удобрения и экономика
docs/          постановка задачи, исходные материалы и техническая логика
examples/      компактные проверенные примеры результатов
outputs/       генерируемые результаты
scripts/       команды проверки и демонстрации в WSL
simulation/    актуальный базовый шаблон standart.apsimx
src/           модули оптимизатора
tests/         unit-тесты без реального APSIMX
tools/         вспомогательный ZeroMQ oneshot launcher
run_optimizer.py
```

## Ограничения текущей версии

- Реальный APSIMX проверяется только в Ubuntu WSL.
- Холодный сценарий временно отключён, поэтому текущая оптимизация использует
  три погодных сценария.
- `N_NO3`, `P_2O5`, `SOC` и `SOC_0` не подтверждены как точные Reporter
  outputs. Soil constraints безопасно остаются `soil_constraints_unchecked`.
- `MineralN` и `OrganicC` сохраняются как дополнительные показатели, но не
  подменяют почвенные ограничения.
- Параметры стоимости семян имеют статус `provisional`.
- `Irgina` предварительно откалибрована по фенологии; полноценная калибровка
  урожайности требует подтверждённых почвенных и начальных условий.

## ZeroMQ

Рабочий evaluator NSGA-II запускает APSIMX через `Models.dll`. Отдельный
launcher `tools/zmq_oneshot.py` подготовлен для будущего запуска:

```bash
python3 tools/zmq_oneshot.py --dry-run
python3 tools/zmq_oneshot.py
```

Он использует `ApsimZMQServer.dll -P oneshot`. FSM, суточные барьеры и ручная
синхронизация в актуальной архитектуре отсутствуют. `Synchroniser` в базовом
шаблоне отключён.

Дополнительное техническое описание: `docs/APSIMX_OPTIMIZATION_LOGIC.md`.
