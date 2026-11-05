# AquaCrop 7.3: Reference Guide for Porting the Computational Core

> A working specification for developers porting AquaCrop to another programming
> language. Based on *Reference Manual, Chapter 3 — Calculation Procedures,
> AquaCrop 7.2, August 2024*. Version 7.3 changes are incorporated from the
> *AquaCrop Version 7.3 Release Note, January 2026* and verified against the
> Fortran source.

## 1. Scope and Version Baseline

This document covers the AquaCrop computational core: the daily water and salt
balances, canopy and root development, evaporation, transpiration, biomass, and
yield. It is intended as an implementation guide for a compatible simulator. It is
not a crop-calibration guide and does not specify the GUI.

Reference points used in this guide:

| Artifact | Pinned version |
|---|---|
| Calculation-procedure manual | AquaCrop 7.2, August 2024 |
| AquaCrop 7.2 source | tag `v7.2`, commit `ce00f98b106558e9c319807c6fb9d366b494e5cc` |
| AquaCrop 7.3 release notes | January 2026 |
| AquaCrop 7.3 source | tag `v7.3_typofix`, commit `5a688fdb2aae99d96dd7e0f10046530a8fdf88e5` |

Stable links:

- [AquaCrop repository](https://github.com/KUL-RSDA/AquaCrop);
- [source at the 7.3 tag](https://github.com/KUL-RSDA/AquaCrop/tree/v7.3_typofix);
- [AquaCrop 7.3 release](https://github.com/KUL-RSDA/AquaCrop/releases/tag/v7.3_typofix);
- [7.2-to-7.3 tag comparison](https://github.com/KUL-RSDA/AquaCrop/compare/v7.2...v7.3_typofix).

When the sources disagree:

1. Use the manual for scientific meaning and units.
2. Use the source of the pinned tag for numerical compatibility.
3. For new 7.3 behavior, use the release notes together with the source: the notes
   state the intent, while the source defines the exact ordering and boundary cases.

## 2. Significant Changes in AquaCrop 7.3

Of the eight items in the release notes, four change the computational core, two
change input data or simulation termination rules, and two mainly concern result
presentation.

| AquaCrop 7.3 change | Affected area | Porting requirement |
|---|---|---|
| Exponential aging of `KcTr,x` and the new `KcDeclineCumul` parameter | Potential transpiration | Required. The old daily `KcDecline` parameter is no longer a 7.3 crop parameter |
| Updated root-zone expansion | Root depth following restricted growth | Required. Recovery now depends on the root depth achieved on the previous day |
| Updated root-zone water extraction | Actual transpiration and the `theta` profile | Required. The target extraction profile is separated from local water availability |
| Premature termination of annual crops | Calendar, canopy, and project termination | Required for compatibility with 7.3 `.CRO` files |
| End of generated irrigation before maturity | Generated irrigation | Required when supporting generated schedules |
| Mauna Loa CO₂ observations extended through 2023 | CO₂ input series | Update the bundled data. The CO₂ equations did not change |
| Ten-day/monthly aggregation of daily output | Reports | Does not change the physical core; required for output compatibility |
| Frequency analysis | GUI/post-processing | May be implemented as a separate statistical module |

The source diff contains an additional important transpiration correction. After
water and salinity stress have already been applied, the anaerobic factor is applied
to the restricted `TpotMAX`, rather than to the original `Tpot` again:

```text
7.2: TpotMAX = Ksaer * Tpot
7.3: TpotMAX = Ksaer * TpotMAX
```

Thus, 7.3 preserves the previously applied stress factors instead of overwriting
them.

Two further changes appear only in the source diff. In 7.3, `roundc` clips its input
to the target `int8` or `int32` range before conversion. The GDD-to-calendar-day
procedures also distinguish more carefully between the actual and reference
temperature series. The first change is described in Section 4.2; the second mainly
affects preparation of fertility/salinity response curves and conversion of GDD crop
parameters.
