# Migration validation — 2026-10-26

Validation used Windows, Python 3.14.7, the installed Rust stable toolchain,
and APSIM `C:/Program Files/APSIM2026.9.8115.0/bin/Models.exe`.
Missing Python test dependencies were installed in the local `.deps` directory;
no global Python packages were changed.

| Check | Result |
| --- | --- |
| Root pytest suite | 127 passed; 9 subtests passed |
| Existing AquaCrop model unittest suite | 11 passed |
| Rust workspace tests, all features | Passed, including CLI conformance and BMI/core equivalence |
| Rust formatting | Passed |
| Rust Clippy, all targets/features, warnings denied | Passed |
| Rust documentation, all features | Built; four existing private-link warnings in BMI documentation |
| Relocated field runner | Built offline with its lockfile |
| AquaCrop common baseline | Passed |
| AquaCrop all fields | 50 field/year results; all 10 daily traces identical to retained pre-migration traces |
| Common AquaCrop NSGA-II example | 3 distinct real candidate calculations; 2 generations; Pareto output written |
| Common APSIM baseline | Passed; all configured fields read through the strict SQLite reader |
| Common APSIM NSGA-II example | 3 distinct real candidate calculations; 2 generations; Pareto output written |
| Legacy AquaCrop launcher | Listed all 10 fields from its relocated path |

The numerical regression compared complete `daily.jsonl` files, not rounded
plots or aggregated scores. The files for every registered field were byte-for-byte
identical after fresh preparation and execution. The comparison covers the full
2019–2023 calendar and linked state continuation. Evidence is stored in
`runs/aquacrop_all_fields/regression.json` and native outputs beside it.

Small real searches and baselines are retained in:

- `runs/aquacrop_baseline_v3/`
- `runs/aquacrop_all_fields/`
- `runs/aquacrop_search/`
- `runs/apsim_baseline_v2/`
- `runs/apsim_search/`

Initial failed attempts were retained with their logs. An old APSIM diagnostic
report referring to ambiguous `[NFlow].Natm` failed with the installed version.
The successful common example explicitly sets `source_reports: optimizer_only`,
which changes only prepared copies and preserves the reports needed by the
strict optimizer reader. This option is not silently applied to existing
multi-year scenarios.

The preserved full multi-year APSIM production search was not run. Its policy,
economic and fertility behavior is covered by existing regression tests; it
retains its own scenario and runtime requirements. The new common examples use
one harvested-dry-matter objective and do not assert equivalent crop predictions
or nitrogen/carbon processes across engines.

Test-only Rust changes replace now-unfulfilled `expect(float_cmp)` annotations
with `allow(float_cmp)` and improve one empty-stderr assertion for the current
Clippy version. No numerical formulas, process ordering or expected values were
changed. The CLI integration suite was rerun after that assertion update.
