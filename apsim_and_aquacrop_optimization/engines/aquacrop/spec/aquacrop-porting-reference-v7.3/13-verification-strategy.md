## 19. Verification Strategy

### 19.1 Three Levels of Equivalence

1. **Formula-level unit tests.** Test thresholds, midpoints, negative and undefined
   inputs, and both sides of every branch.
2. **Conservation tests.** Isolate water and salt processes: rainfall only,
   evaporation only, drainage only, and capillary rise only.
3. **Golden tests.** Run the official 7.3 testcase and compare daily `theta`, CC, E,
   Tr, B, HI, Y, water, and salt values.

### 19.2 Values to Compare Every Day

| Group | Minimum comparison set |
|---|---|
| Calendar | Date, DAP, daily GDD, `SumGDD`, `DelayedDays` |
| Canopy and roots | `CCiActual`, `CCxAdjusted`, `RootingDepth` |
| Surface fluxes | Rain, irrigation, runoff, infiltration, storage |
| Atmosphere | ETo, Epot/Eact, Tpot/Tact |
| Soil profile | `theta`, `fluxout`, and `FCadj` for every compartment |
| Salt | `Salt`, `Depo`, ECe/ECsw, salt input/output |
| Production | Daily and cumulative biomass, HI, dry yield |
| Checks | Daily water- and salt-balance errors |

Compare internal binary64 values, or at least 15–17 significant digits, before
comparing formatted `.OUT` files. Formatted output can conceal the first day of drift.

### 19.3 Required AquaCrop 7.3 Scenario Matrix

- Unstressed calendar-day crop.
- The same crop in GDD mode.
- Prolonged water stress followed by root-growth recovery.
- A dry subsoil and a restrictive soil layer.
- Different gravel fractions by layer, exposing the `layeri` behavior.
- Simultaneous water, salinity, and anaerobic stress.
- Surface-wet and depth-wet profiles for extraction-distribution tests.
- Premature termination before maturity, including `PrematureEnd > 365`.
- Generated irrigation with `IrriInfoLastDay`, plus a legacy `.IRR` without it.
- A 7.2 `.CRO` file receiving `KcDeclineCumul=11` automatically.
- Mulch, partial wetting, and evaporation transitions I↔II.
- Shallow groundwater with saline capillary rise.
- Weeds, cutting, and perennial assimilate transfer.

### 19.4 Tolerances

For isolated pure functions, target an absolute or relative error near `1e-12`.
For daily state, begin around `1e-10` to `1e-8`, while always locating the first day
of divergence. A seasonal tolerance must not hide compensating daily errors. For
values controlling branches, reproduce both the floating-point value and `roundc`
behavior exactly where possible.

Build the reference Fortran with array and range checks (`make DEBUG=1`) and run the
official testcase. If a Fortran compiler is unavailable, pin the 7.3 reference outputs
as golden fixtures and record that execution validation is still outstanding.
