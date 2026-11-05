## 20. Known Porting Traps

1. **Percent versus fraction.** `FC=30` and `theta=0.30` are a typical matching pair;
   mixing them creates a factor-of-100 error.
2. **Millimeters versus meters.** Profile depths are meters and fluxes are mm/day.
   Keep the factor 1000 explicit and localized.
3. **Gravel.** It participates in `theta`↔mm conversion but not necessarily inside
   every empirical response function.
4. **Daily order.** Drainage precedes today's infiltration. Today's rain does not
   pass through the drainage call, but it does affect later evaporation and
   transpiration.
5. **Upper zone versus entire root zone.** Stress uses the wetter zone; averaging the
   two zones is incorrect.
6. **Inclusive crop day.** DAP is 1 on `Crop.Day1`, a common source of off-by-one
   errors.
7. **Two time scales.** Even a GDD crop uses calendar days for aging, pollination,
   and some output logic.
8. **Sentinels are not NaN.** Parsers branch on `-9`. An internal option type is
   appropriate, but I/O must restore the sentinel convention.
9. **Evaporation stage is stateful.** `Kr` cannot be derived from current water content
   alone without `EvapStartStg2`, `EvapWCsurf`, and `EvapZ`.
10. **Salt is mass, not only EC.** EC changes with water content even when salt mass
    does not.
11. **Fertility stress is dynamic.** It is not a constant percentage for the season.
12. **7.3 `SinkMajor/SinkMinor`.** These are daily work values; do not blindly carry
    them into the next day.
13. **`TheSum==0`.** Add a diagnostic guard without altering the normal path.
14. **Observed `layeri` behavior.** Make strict-compatible versus corrected behavior
    an explicit choice; see Section 14.5.
15. **Versioned file lines.** New fields cannot simply be appended to one universal
    structure. The Fortran readers skip or substitute lines depending on file version
    and mode.

## 21. Practical Implementation Sequence

1. Implement domain types, units, calendar functions, `roundc`, GDD, and versioned
   parsers.
2. Implement compartments, water storage, and `DetermineRootZoneWC`.
3. Port the soil-water balance without a crop: drainage → runoff → infiltration →
   capillary rise → balance check.
4. Add salinity and dedicated conservation tests.
5. Add unstressed canopy and roots, followed by stress adjustments.
6. Add two-stage soil evaporation.
7. Add potential transpiration and the 7.3 root-extraction algorithm.
8. Add biomass, CO₂ response, harvest index, and yield.
9. Add weeds, perennials, cutting, and assimilate transfer.
10. Add output aggregation and frequency analysis as post-processing.
11. Run golden tests and diagnose differences from the first divergent day and first
    divergent process.

## 22. Definition of Done for an AquaCrop 7.3 Port

- The parser accepts standard 7.2 and 7.3 project, crop, and irrigation inputs.
- All 7.3 computational and termination changes are implemented, and the bundled
  CO₂ series reaches 2023.
- `BUDGET_module` process order matches the Fortran source.
- Formula-level and conservation tests pass.
- The official testcase matches daily internal values within the chosen tolerance.
- Every intentional correction of a Fortran behavior is controlled by an explicit
  compatibility flag and has both strict and corrected golden tests.
- The source tag and reference-data hash are recorded in build metadata.
