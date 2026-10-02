# Команды APSIMX-оптимизатора

Все реальные APSIMX-прогоны выполняются в Ubuntu WSL из каталога:

```bash
cd /home/user/ApsimOptimization
source venv/bin/activate
```

Оптимизатор использует один неизменяемый шаблон
`simulation/standart.apsimx`. Для каждого погодного сценария создаётся
отдельная временная копия в `outputs/.optimizer_work/`.

## Проверки

```bash
python3 -m pytest tests
python3 run_optimizer.py --check-env
python3 run_optimizer.py --dry-run
python3 run_optimizer.py --diagnose-soil-outputs
```

Текущий dry-run показывает:

- 1 фиксированную дату/правило посева;
- 2 варианта сорта: `Zlata` и `Irgina`;
- 6 значений population;
- 5 значений глубины;
- 3 значения междурядья;
- 29 схем удобрения;
- 5220 технологий;
- 3 активных сценария и 15660 возможных APSIMX-прогонов.

Сценарий `s2_holod` присутствует в config, но временно отключён.

## Контрольные APSIMX-прогоны

Один сценарий:

```bash
python3 run_optimizer.py --single-run \
  --scenario s3_norm \
  --scheme F0_no_fertilizer \
  --cultivar Irgina \
  --population 250 \
  --depth 30 \
  --row-spacing 250 \
  --ignore-cache
```

Одна технология по всем активным сценариям:

```bash
python3 run_optimizer.py --evaluate-technology \
  --scheme F0_no_fertilizer \
  --population 250 \
  --depth 30 \
  --row-spacing 250 \
  --ignore-cache
```

`--ignore-cache` нужен для контрольного запуска через Models.dll. Без него
валидный сохранённый результат может быть прочитан из cache.

## NSGA-II

Быстрая проверка, 10 кандидатов × 3 поколения:

```bash
python3 run_optimizer.py \
  --config configs/optimizer_config_quick_two_cultivars_no_cold.yaml \
  --optimize-nsga2
```

Основная конфигурация, 20 кандидатов × 10 поколений:

```bash
python3 run_optimizer.py --optimize-nsga2
```

Каждый новый кандидат требует до трёх APSIMX-прогонов. Повторные технологии
берутся из cache.

## Full-grid

Полный перебор содержит 15660 прогонов и может занимать много часов:

```bash
python3 run_optimizer.py --full-grid --allow-large-grid
```

Не запускайте его для обычной проверки передачи проекта.

## Диагностика почвенных выходов

```bash
python3 run_optimizer.py --test-soil-output-candidates --scenario s3_norm
```

Каждое выражение Reporter проверяется отдельно. Технически работающее выражение
не включается в почвенные ограничения автоматически.

## Проверка результатов

```bash
python3 run_optimizer.py --validate-outputs
```

Основные выходы находятся в `outputs/`: `results_runs.csv`,
`results_technologies.csv`, `nsga2_evaluations.csv`, `pareto_nsga2.csv`,
`best_solution.json` и `optimizer_log.txt`.

## ZeroMQ oneshot (на будущее)

ZeroMQ не участвует в текущем оптимизационном цикле через `Models.dll`.
Отдельный минимальный launcher не содержит FSM, суточных барьеров или обмена
состояниями.

```bash
python3 tools/zmq_oneshot.py --dry-run
python3 tools/zmq_oneshot.py
```

Фактическая команда берёт пути и порт из config и использует профиль
`-P oneshot`. Узел `Synchroniser` в `simulation/standart.apsimx` отключён.
