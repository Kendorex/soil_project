# Observations and frozen chronological predictions

Copied unchanged from the user's `new_calibr/result` calibration package for
portable local assessment. Units are dry-matter tonnes per hectare, as declared
in `yield_comparison.json`. Conversion assumptions (grain moisture, hay/green
dry matter) remain those of the supplied package; no new measurements are invented.

`records` includes observations and final-fit predictions. `pre_update_records`
contains predictions frozen before incorporating the corresponding years into
the chronological update. Each record supplies `trained_through` and
`predicted_before_update`. This conditional evaluation knows weather and
management. Initial crop priors and species proxies are not independently
validated by this export replay. Do not describe final-fit scores as held-out.

The source pipeline's chronology is documented in
`new_calibr/apsim_calibration/calibrate_history.py` and `new_calibr/API.md`.
Checks and SHA-256 are saved by `scripts/check_apsim_readiness.py` in
`for_me/apsim_readiness_0p1ha`.
