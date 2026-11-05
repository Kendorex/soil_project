## 5. Units and Basic Conversions

Input `SAT`, `FC`, `WP`, and `FCadj` are in vol %, while `theta` is a fraction in
m³/m³. For a compartment of thickness $\Delta z$ and gravel fraction $v_g$:

$$
W(\theta,\Delta z)=1000\,\theta\,\Delta z
\left(1-\frac{v_g}{100}\right)\quad[\mathrm{mm}].
$$

Water stored in the root zone:

$$
W_r=\sum_i1000\,\theta_i\,\Delta z_{i,r}
\left(1-\frac{v_{g,i}}{100}\right).
$$

Depletion relative to field capacity and total available water:

$$
D_r=\sum_i1000(\theta_{FC,i}-\theta_i)\Delta z_{i,r}
\left(1-\frac{v_{g,i}}{100}\right),
$$

$$
TAW_r=\sum_i1000(\theta_{FC,i}-\theta_{WP,i})\Delta z_{i,r}
\left(1-\frac{v_{g,i}}{100}\right).
$$

$D_r$ may be negative when water content is above FC. For a partially intersected
compartment, use its actual `WFactor`, not the entire compartment.

## 6. One Daily Step: Required Ordering

Ordering is part of the model. Reordering drainage, infiltration, evaporation, or
transpiration changes the water available later on the same day.

### 6.1 Outer Orchestration (`AdvanceOneTimeStep`)

```text
1. Load ETo, rainfall, Tmin/Tmax, and CO2 for the current day.
2. Calculate daily GDD and update calendar counters.
3. Determine the actual root depth using the 7.3 algorithm.
4. Initialize assimilate transfers and cutting events.
5. Run BUDGET_module: water, salt, and canopy processes.
6. Calculate potential biomass.
7. Calculate actual biomass, harvest index, and yield.
8. Process cutting/harvest events, cumulative sums, and output.
9. Store previous-day values and advance the date.
```

### 6.2 Ordering Inside `BUDGET_module`

| No. | Process | Main values changed |
|---:|---|---|
| 1 | Beginning-of-day balance snapshot | Beginning water and salt content |
| 2 | Groundwater effects and adjusted FC | `FCadj`, water-table flag |
| 3 | Redistribution and drainage | `theta`, `fluxout`, `Drain` |
| 4 | Surface runoff | `Runoff` |
| 5 | Effective rain, generated irrigation, surface storage, infiltration | `Irrigation`, storage, `theta`, `Infiltrated` |
| 6 | Capillary rise | `theta`, `CRwater`, `CRsalt` |
| 7 | Salt transport and balance | `Salt`, `Depo`, salt inputs/outputs |
| 8 | Germination check | `Germinate`, `DelayedDays` |
| 9 | Fertility and salinity effects | Current stress effects |
| 10 | Green canopy cover | `CCiActual`, `CCxAdjusted`, early senescence |
| 11 | Potential `Tpot` and `Epot`; ETo adjustment of the stomatal threshold | `Tpot`, `Epot`, `pActStom` |
| 12 | Surface-water and soil evaporation, stages I and II | `Eact`, `theta`, evaporation-layer state |
| 13 | Transpiration and root extraction; feedback to CC | `Tact`, `theta`, `CC` |
| 14 | Horizontal groundwater inflow | Water and salt in the saturated profile |
| 15 | Salt concentration after water removal | `Salt`, `Depo` |
| 16 | End-of-day balance check | Daily water and salt errors |

In 7.3, the `PrematureEnd` check occurs after canopy calculation: on that day,
`CCiActual=0` and `NoMoreCrop=true`. Project initialization also restricts
`Simulation.ToDayNr` and `Crop.LastDayNr` to `DayNrPrematureEnd-1`. Test both paths,
including linked multi-run projects.
