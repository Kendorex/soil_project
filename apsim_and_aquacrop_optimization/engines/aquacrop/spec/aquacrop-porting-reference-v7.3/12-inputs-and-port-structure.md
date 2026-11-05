## 17. Input Changes in AquaCrop 7.3

### 17.1 Crop Files

For crop files earlier than 7.3, the parser:

- sets `PrematureEnd = -9`;
- skips the legacy `KcDecline (%/day)` line;
- sets `KcDeclineCumul = 11`.

For 7.3 files it reads:

- `PrematureEnd`: ordinal day counted from 1 January of the planting year. A value
  greater than 365 belongs to the following calendar year; `-9` disables the feature;
- `KcDeclineCumul`: cumulative reduction of `KcTr,x` at maturity, in percent.

When `PrematureEnd` is active, its absolute day number is resolved relative to the
planting year. `Simulation.ToDayNr` and `Crop.LastDayNr` are restricted to
`DayNrPrematureEnd-1`. Backward-compatible parsing must branch on the version number
stored inside the file, not merely on the filename extension.

### 17.2 Irrigation Files

For `Generate` mode, a 7.3 irrigation file adds `IrriInfoLastDay` after the timing
and depth criteria. It is the number of days before the end of the growing period
during which no irrigation should be generated. Older files receive `-9`.

The source condition is:

```text
if day >= Crop.DayN - IrriInfoLastDay + 1:
    Irrigation = 0
else:
    generate_irrigation_if_triggered()
```

This check occurs before the current day's infiltration. The extra line is not read
for manual irrigation mode.

### 17.3 CO₂ Data and Output

- The bundled `MaunaLoa.CO2` series is extended through 2023. Pin a copy in test
  data so future runs do not depend on a changing external source.
- `OutputAggregate` supports daily, ten-day, and monthly output. Aggregation is
  applied to completed daily results; it does not change the model time step.
- Frequency analysis should consume stored or aggregated results and must not feed
  back into `SimulationState`.

## 18. Suggested Port Structure

```text
aquacrop/
  domain/          SoilLayer, Compartment, Crop, Management, SimulationState
  io/              project, climate, crop, soil, irrigation, versioned readers
  climate/         dates, GDD, expansion of aggregated input data
  stress/          water, temperature, fertility, salinity
  processes/
    rooting
    drainage
    runoff
    infiltration
    capillary_rise
    salinity
    canopy
    evaporation
    transpiration
    biomass
    harvest_index
  engine/          initialize, step, finalize
  output/          daily results, aggregation, writers
  compat/          roundc, sentinels, v72/v73 switches
  tests/
    unit/
    conservation/
    golden/
```

Good process-level test boundaries correspond to `calculate_drainage`,
`calculate_runoff`, `calculate_CapillaryRise`, `DetermineCCi[GDD]`,
`CalculateETpot`, `calculate_transpiration`, `DeterminePotentialBiomass`, and
`DetermineBiomassAndYield`.
