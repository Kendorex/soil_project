# Cross-year oat mechanism diagnostic

Run this stage in Ubuntu/WSL after the environment used for the previous APSIM checks is active:

```bash
python3 scripts/diagnose_oats_history_mechanisms.py --run
```

The script runs the 2019-2023 history once and creates
`oats_history_mechanisms_<timestamp>.tar.gz` in the project root. Send that archive
for analysis. `oats_history.csv` is the stable per-case table for future plots;
`daily_oats_2019_2023.csv` contains the daily mechanism series; and
`history_summary.json` keeps separate metrics for 2019-2022 and the 2023 validation
year. No agronomic bounds or calibration values are introduced by this diagnostic.

The control run on 2026-09-24 completed for all eight oat observations. Oat
yield MAE was 1.557 t/ha over 2019-2023 and 2.232 t/ha for the two 2023 cases.
Five cases were underpredicted and three were overpredicted. The direction of
error followed canopy and biomass formation more clearly than the count of
N-stress days: grain-fill N stress was also present in overpredicted years.
The 2019 Field3 crop remained at 0 t/ha with almost no canopy because of the
already identified early water limitation; its observed cultivar was Argamak,
while the model used Yakov. The next calibration experiment should therefore
evaluate oat phenology and source formation across years, while retaining 2023
as validation and keeping the 2019 case separately identified.
