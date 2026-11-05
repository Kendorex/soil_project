# Joint crop-map and management search

New future APSIM and coupled configurations use
`optimization.search_scope: "joint"`. The management preset is an initial anchor,
not a fixed policy. Existing frozen night batches and historical runs retain
their original configuration. The standalone AquaCrop workflow is unchanged;
it has no nitrogen-dose response model.

```powershell
python run.py apsim --engine apsim --search-scope joint --algorithm nsga2 --objectives yield profit humic --run
python run.py apsim --engine apsim --search-scope joint --algorithm nsga3 --objectives yield profit mineral_n humic --run
```

Omit `--run` or use `--prepare-only` to prepare inputs without simulations.
Use `--search-scope crop_map` to reproduce the fixed-management map workflow.
Start a new night batch to use changed source defaults; resuming an existing
batch intentionally preserves its frozen scope.

The default domain has 20 integer variables:

- 10 whole legal crop-sequence choices, one per field over 2025-2029.
- Spring wheat: cultivar, population, sowing depth, row spacing, fertilizer scheme.
- Oats with underseed: population, sowing depth, fertilizer scheme.
- Winter rye proxy: population and fertilizer scheme.

Fertilizer scheme combines form and dose: one zero-N control plus four forms at
each positive dose of 30, 60, 90, 120, 150, 180 or 210 kg N/ha. Management options
come from the existing native-management configuration. These are crop-level
policies shared across fields/years, not independent field/year operations.
Perennial components retain their profile settings. Historical arrays are
preserved; future management is applied by harvest year, including prior-autumn
winter sowing. Dates remain cloned calendar assumptions, not search variables.

The all-zero vector represents the initial legal map and configured management
anchor. Each candidate is decoded into BOTH sequences and policies before model
construction. The cache key includes both: identical maps with different doses
or other management values never reuse the same evaluation. Every unique
combination is simulated and scored afresh. The fixed reference used for the
mineral-N comparison is calculated once and does not change between candidates.

Humic remains an objective without a hard loss prohibition in the new defaults.
Crop-mix constraints remain hard. Enlarging the domain does not fix the low
fraction of farm-wide crop-count-compliant maps, and 10 individuals/4 generations
is still a small trial budget for a 20-variable search.

For the coupled model, native AquaCrop still does not acquire an N-dose yield
response from this change. Dose affects APSIM soil indicators and diagnostic
costs. Exact depth/row-spacing effects and cultivar differences absent from
AquaCrop's supplied effective profiles are not invented.

`decision_space.json` records variable counts and exact management options.
`selected_management.json` contains the selected crop policies; each candidate
result records its own `policies`. The selected full APSIM model matches both
the chosen map and management. Old completed scores are not rewritten.
