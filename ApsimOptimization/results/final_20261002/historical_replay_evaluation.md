# Historical N replay evaluation

Acceptance status: `no_validation_accuracy_gain_mechanistic_calibration_required`

The control uses the original static field fertilizer managers. The replay changes only the N amount on sowing events that have an explicit N rate and sowing date in the supplied observation file. APSIM fertilizer type is preserved.

| Scope | n | Control MAE | Replay MAE | Change | Control RMSE | Replay RMSE | Change |
|---|---:|---:|---:|---:|---:|---:|---:|
| Calibration 2019-2022 | 27 | 1.405 | 1.446 | +0.040 | 1.891 | 1.894 | +0.003 |
| Validation 2023 | 10 | 1.213 | 1.302 | +0.089 | 1.500 | 1.578 | +0.079 |
| All observations | 37 | 1.353 | 1.407 | +0.054 | 1.794 | 1.814 | +0.020 |

## Decision-readiness gate

Selected historical case: `control`

Status: `historical_validation_not_ready_for_decision_optimization`

| Check | Result |
|---|---|
| complete_validation_coverage | PASS |
| all_validation_harvest_events_present | PASS |
| validation_rmse_better_than_training_mean | FAIL |
| positive_validation_field_ranking | FAIL |

Validation RMSE: `1.500 t/ha`; training-mean RMSE: `0.837 t/ha`; Spearman field rank: `-0.297`.

No absolute agronomic error threshold was assumed. Readiness requires complete held-out coverage, harvest events, lower RMSE than the calibration-period mean, and positive Spearman field ranking.

## Largest field effects

| Year/field | Crop | Observed N | Control N | Replay N | Yield change, t/ha |
|---|---|---:|---:|---:|---:|
| 2022/Field4 | Овёс | 30 | 10 | 30 | +1.250 |
| 2021/Field6 | Овёс | 60 | 30 | 60 | +1.234 |
| 2022/Field7 | Яровая пшеница | 60 | 30 | 60 | +0.707 |
| 2023/Field9 | Озимая рожь | 60 | 30 | 60 | +0.679 |
| 2023/Field7 | Овёс | 60 | 30 | 60 | +0.662 |
| 2022/Field8 | Озимая рожь | 60 | 30 | 60 | +0.633 |
| 2023/Field8 | Яровая пшеница | 60 | 30 | 60 | +0.589 |
| 2021/Field1 | Овёс | 30 | 10 | 30 | +0.009 |
| 2021/Field4 | Яровая пшеница | 30 | 10 | 30 | +0.008 |
| 2019/Field3 | Овёс | 30 | 30 | 30 | +0.000 |
| 2019/Field7 | Яровая пшеница | 30 | 30 | 30 | +0.000 |
| 2020/Field2 | Озимая рожь | 30 | 30 | 30 | +0.000 |

P/K labels remain diagnostic flags because the APSIM model has no responsive P/K pools. The result cannot establish fertilizer dose bounds, nutrient target intervals, products, prices, or ratios.

Control checkpoint: `outputs/multifield/quality_history/checkpoints/20261002T120258Z_historical_static_control_20261002_150208_5975a634`

Replay checkpoint: `outputs/multifield/quality_history/checkpoints/20261002T120348Z_historical_n_replay_20261002_150208_5975a634`
