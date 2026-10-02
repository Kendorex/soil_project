# Демонстрация APSIMX-оптимизатора

## Что показывает проект

Python связывает два самостоятельных компонента:

```text
pymoo NSGA-II предлагает технологию
→ Python создаёт временную копию standart.apsimx
→ подставляет сорт, параметры посева, удобрение и погоду
→ запускает APSIMX через dotnet Models.dll
→ читает Yield и доступные почвенные показатели из SQLite Report
→ рассчитывает прибыль и управляемые затраты
→ возвращает три целевых значения в NSGA-II
→ NSGA-II формирует следующее поколение
```

Это итерационный цикл. NSGA-II не выбирает решение из заранее рассчитанного
full-grid: оценка APSIMX происходит внутри evaluator для каждого нового
кандидата.

## Входные данные

| Данные | Файл | Использование |
|---|---|---|
| Базовая модель | `simulation/standart.apsimx` | Почва, культура, management и Reporter |
| Погода | `data/weather/*.met` | Отдельный климатический сценарий |
| Сорта | `data/cultivars/cultivars.txt` | Дискретный выбор сорта |
| Схемы удобрения | `data/fertilizers/fertilizer_schemes.json` | Тип, доза и число проходов |
| Цены удобрений | `data/economics/fertilizer_prices.csv` | Стоимость удобрения |
| Экономические константы | `data/economics/economic_constants.csv` | Доход и остальные статьи затрат |

Исходный `standart.apsimx` не изменяется. Для каждой технологии и каждого
погодного сценария создаётся отдельная временная копия.

## Маленькое демонстрационное пространство

Файл `configs/optimizer_config_demo.yaml` специально ограничивает объём
расчёта:

| Параметр | Значения |
|---|---|
| Правило посева | `1-may..30-may`, фиксировано |
| Сорт | `Zlata`, `Irgina` |
| Population | `250`, `450` растений/м² |
| Глубина | `30`, `60` мм |
| Междурядье | `150`, `250` мм |
| Удобрение | F0 без удобрения; NH4NO3N 30, 150 или 210 кг/га |
| Погода | `s1_dojdey`, `s3_norm`, `s4_zasuha` |

Полное сочетание этих значений содержит 64 технологии и 192 сценарных
прогона. Демонстрационный NSGA-II не перебирает их все: он использует 6
кандидатов и 2 поколения. Поэтому требуется не более 12 оценок технологий,
то есть не более 36 новых запусков APSIMX. Повторные кандидаты не запускаются
заново.

## Цели оптимизации

```text
f1 = -profit_avg
f2 = controllable_cost
f3 = -profit_min
```

`profit_avg` — средняя прибыль по активным погодным сценариям,
`profit_min` — прибыль в худшем сценарии, `controllable_cost` — стоимость
удобрения, его внесения, финансирования и изменяемая часть стоимости семян.
Знаки минус нужны потому, что pymoo минимизирует целевые функции.

## Порядок показа в WSL

```bash
cd /home/user/ApsimOptimization
source venv/bin/activate
pip install -r requirements.txt
```

Сначала проверить код и среду:

```bash
python3 -m pytest tests
python3 run_optimizer.py \
  --config configs/optimizer_config_demo.yaml \
  --check-env
```

Показать пространство входных параметров без запуска APSIMX:

```bash
python3 run_optimizer.py \
  --config configs/optimizer_config_demo.yaml \
  --dry-run
```

Показать один реальный прогон APSIMX:

```bash
python3 run_optimizer.py \
  --config configs/optimizer_config_demo.yaml \
  --single-run \
  --scenario s3_norm \
  --scheme F12_NH4NO3N_150_sowing \
  --cultivar Irgina \
  --population 450 \
  --depth 60 \
  --row-spacing 150 \
  --ignore-cache
```

Запустить короткий настоящий цикл NSGA-II ↔ APSIMX:

```bash
python3 run_optimizer.py \
  --config configs/optimizer_config_demo.yaml \
  --optimize-nsga2 \
  --ignore-cache
```

Во втором терминале можно наблюдать цикл в реальном времени:

```bash
tail -f outputs/demo/optimizer_log.txt
```

В логе видны поколение, декодированная технология, запрос APSIMX или cache
hit, средняя и минимальная прибыль, затраты и значения `f1/f2/f3`.

## Что открыть после расчёта

- `outputs/demo/nsga2_evaluations.csv` — все кандидаты, предложенные pymoo;
- `outputs/demo/pareto_nsga2.csv` — решения Парето;
- `outputs/demo/best_solution.json` — лучшее по прибыли, самое дешёвое и
  компромиссное решения;
- `outputs/demo/optimizer_log.txt` — доказательство последовательных вызовов
  APSIMX из NSGA-II;
- `outputs/demo/.optimizer_work/nsga2/` — временные `.apsimx` и `.db`.

## Что важно сказать коллегам

- Активный оптимизатор запускает APSIMX через `Models.dll`.
- `ApsimZMQServer.dll -P oneshot` подготовлен как отдельный будущий способ
  запуска и пока не участвует в evaluator NSGA-II.
- Сценарий `s2_holod` временно отключён, поэтому текущие результаты являются
  исследовательскими для трёх сценариев.
- Ограничения `N_NO3`, `P_2O5`, `SOC/SOC_0` остаются unchecked, пока не
  подтверждены точные показатели, единицы и методика их сопоставления.
- Параметры экономики семян пока предварительные.
