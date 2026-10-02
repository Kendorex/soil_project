# Команды для работы с проектом

Все расчёты APSIM рекомендуется выполнять в Ubuntu или WSL из корня проекта.

## 1. Подготовка окружения

```bash
cd /путь/к/ApsimOptimization_2026.10.02
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

Указать исполняемый файл APSIM Next Generation:

```bash
export APSIM_MODELS=/home/user/ApsimX/bin/Release/net8.0/Models
```

Проверка пути:

```bash
test -x "$APSIM_MODELS" && echo "APSIM найден" || echo "Проверьте APSIM_MODELS"
```

## 2. Быстрая проверка проекта

Тесты поддерживаемого многополевого кода и проверка активной конфигурации без
длительного APSIM-прогона:

```bash
bash scripts/run_project_checks.sh
```

Только проверка конфигурации:

```bash
python3 run_multifield.py validate \
  --config configs/multifield_2023_n_diagnostic.yaml
```

## 3. Проверка исторической точности

Команда запускает APSIM для вариантов `control` и `replay`, рассчитывает метрики
2019–2023 и формирует компактный архив доказательств:

```bash
APSIM_MODELS=/home/user/ApsimX/bin/Release/net8.0/Models \
  python3 scripts/evaluate_historical_replay.py --run
```

Это длительный расчёт. Результаты появляются в
`outputs/multifield/HISTORICAL_REPLAY/`.

## 4. Короткая проверка оптимизатора до 20 минут

```bash
APSIM_MODELS=/home/user/ApsimX/bin/Release/net8.0/Models \
  bash scripts/run_optimizer_20min_check.sh
```

Проверка использует population 8 и 2 поколения. Она подтверждает корректность
связки APSIM → обработка результатов → NSGA-II, но не означает сходимость поиска.

## 5. Диагностический запуск NSGA-II

Пример с явно заданным размером поиска:

```bash
python3 run_multifield.py optimize \
  --config configs/multifield_2023_n_diagnostic.yaml \
  --population 20 \
  --generations 5
```

Выходные файлы создаются в новом каталоге
`outputs/multifield/DIAGNOSTIC_2023_N/optimize_<id>/`:

- `pareto.json` и `pareto_summary.csv` — фронт Парето;
- `evaluations.json` и `evaluations_summary.csv` — все кандидаты;
- `config_snapshot.json` — точная конфигурация запуска;
- `history.json` — порядок вычислений и использование кэша.

До улучшения исторической точности эти результаты нельзя использовать как
практические рекомендации.

## 6. Диагностический baseline

```bash
python3 run_multifield.py baseline \
  --config configs/multifield_2023_n_diagnostic.yaml
```

## 7. Построение графика Парето

```bash
python3 scripts/plot_multifield_pareto.py \
  --optimize outputs/multifield/DIAGNOSTIC_2023_N/optimize_<id>
```