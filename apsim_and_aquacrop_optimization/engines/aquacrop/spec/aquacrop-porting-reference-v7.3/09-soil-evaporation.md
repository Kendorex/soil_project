## 13. Soil Evaporation

### 13.1 Potential Evaporation Coefficient

Micro-advection adjustment of canopy cover:

$$CC^*=\operatorname{clamp}(1.72CC-CC^2+0.30CC^3,0,1).$$

For a wet soil surface:

$$K_e=(1-CC^*)K_{ex},$$

where the typical `Kex=1.10`. Potential soil evaporation is:

$$E_p=K_eETo.$$

Withered canopy adds sheltering. The source combines green and withered cover using
`CCEffectEvapLate`.

For mulch:

$$f_m^{eff}=1-f_{mulch}\frac{Mulch}{100}.$$

Partially wetted irrigation uses the wetted fraction `fw`. When mulch and partial
wetting both apply, the stronger restriction is selected by taking the smaller
potential evaporation.

### 13.2 Two Evaporation Stages

Readily evaporable water:

$$REW=1000(\theta_{FC}-\theta_{airdry})Z_{surf},$$

with typical $\theta_{airdry}\approx0.5\theta_{WP}$ and $Z_{surf}\approx0.04m$.

- **Stage I:** energy-limited, with $E=(1-CC^*)K_{ex}ETo$ until REW is depleted.
- **Stage II:** controlled by dryness of the evaporation layer. The reduction
  coefficient is:

$$
K_r=\frac{\exp(f_KW_{rel})-1}{\exp(f_K)-1},
$$

with default $f_K=4$.

During stage II, the Fortran implementation performs 20 substeps. After each one it
extracts water from the current evaporation depth, may deepen the evaporation layer
up to `EvapZmax`, and transports salt. A one-step analytical replacement generally
will not reproduce the same water and salt profiles.

Rain or ordinary irrigation may reset the process to stage I. Before the first day
without previous state, `PrepareStage2` initializes the evaporation state. Incorrect
initialization of `EvapStartStg2`, `EvapWCsurf`, or `EvapZ` causes immediate drift.
