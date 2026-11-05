## 11. Soil-Water Balance

### 11.1 Grid

Time is discretized in daily steps. The soil profile is divided into horizontal
compartments, each linked to one soil layer. Vertical processes operate top-down,
except for capillary rise. `WFactor` represents the fraction of a compartment
intersecting a partial active depth.

Gravel reduces water-storage capacity, while `theta`, `FC`, `WP`, and `SAT` refer
to the fine-earth fraction. Every conversion between a change in water content and
millimeters therefore includes `(1-GravelVol/100)`.

### 11.2 Drainage and Redistribution

For water content above adjusted FC, the daily change is:

$$
\Delta\theta=\tau(\theta_{SAT}-\theta_{FC})
\frac{\exp(\theta_i-\theta_{FC})-1}
{\exp(\theta_{SAT}-\theta_{FC})-1}.
$$

All water contents in this equation are fractions, not percentages. Drainage from a
compartment is:

$$
D_i=1000\,\Delta\theta_i\,\Delta z_i
\left(1-\frac{v_{g,i}}{100}\right).
$$

The drainage characteristic derived from saturated hydraulic conductivity is:

$$
\tau=\operatorname{clamp}(0.0866K_{sat}^{0.35},0,1).
$$

`TauFromKsat` actually evaluates `roundc(100*tau)`, clamps the integer to
`[0,100]`, and divides by 100. Thus, the source quantizes $\tau$ in increments of
0.01; reproduce this for strict compatibility.

Compartments are not drained independently. Incoming drainage from above is added,
the current layer's `InfRate` is enforced, and excess water may be redistributed
upward until saturation limits are satisfied. `fluxout_i` records the flux through
the lower boundary and later blocks capillary rise through a draining compartment.

The inverse relation used when admissible drainage is known is:

$$
\theta_x=\theta_{FC}+\ln\left[
1+\Delta\theta\frac{e^{\theta_{SAT}-\theta_{FC}}-1}
{\tau(\theta_{SAT}-\theta_{FC})}\right].
$$

### 11.3 Surface Runoff: Curve Number Method

For rainfall event $P$:

$$
S=254\left(\frac{100}{CN}-1\right),\qquad I_a=f_{Ia}S,
$$

$$
RO=\begin{cases}
0,&P\le I_a,\\
\dfrac{(P-I_a)^2}{P+(1-f_{Ia})S},&P>I_a.
\end{cases}
$$

The manual and defaults use $f_{Ia}=0.05$, but the port should read
`IniAbstract/100` from the simulation parameters.

For antecedent-moisture correction, the 7.2 manual gives:

$$
CN_I=-16.91+1.348CN_{II}-0.01379CN_{II}^2
+0.0001172CN_{II}^3,
$$

$$
CN_{III}=2.5838+1.9449CN_{II}-0.014216CN_{II}^2
+0.000045829CN_{II}^3.
$$

However, `global.f90:DetermineCNIandIII` in both source tags actually uses:

$$
CN_I=1.4\cdot10^{-14}+0.507CN_{II}-0.00374CN_{II}^2
+0.0000867CN_{II}^3,
$$

$$
CN_{III}=5.6\cdot10^{-14}+2.33CN_{II}-0.0209CN_{II}^2
+0.000076CN_{II}^3.
$$

Use the latter expressions, `roundc`, and the subsequent `[1,100]` clamps in
strict-compatibility mode. This is a confirmed manual-versus-source discrepancy,
not a 7.3 change. AquaCrop then interpolates between CNI and CNIII using relative
wetness of the upper soil profile.

For ten-day and monthly rainfall, AquaCrop converts the total into a representative
storm, uses the configured number of storms, and applies a special `10.14` factor.
Do not replace this branch with uniform daily rainfall.

### 11.4 Infiltration and Surface Storage

Inputs are the infiltrating portions of rain, irrigation, and surface storage. With
bunds (`BundHeight >= 0.01 m`), water may remain on the surface. Without bunds,
excess becomes additional runoff. Infiltration is limited by `InfRate`, fills
compartments from the surface downward, and transports dissolved salts.

Keep `InfiltratedRain`, `InfiltratedIrrigation`, and `InfiltratedStorage` as distinct
fluxes because their salinities and reporting totals differ.

### 11.5 Capillary Rise

The maximum rise across distance $z$ above the groundwater table is:

$$CR_{max}=\exp\left(\frac{\ln z-b}{a}\right).$$

The source returns zero if `Ksat<=0`, groundwater depth is not positive, or the
distance is at least 4 m. A point inside the aquifer and all larger results are capped
at `99 mm/day`.

Parameters by soil class and `Ksat`:

| Class | `Ksat`, mm/day | `a` | `b` |
|---:|---:|---|---|
| 1, sandy | 200–2000 | `-0.3112 - 0.00001 Ksat` | `-1.4936 + 0.2416 ln(Ksat)` |
| 2, loamy | 100–750 | `-0.4986 + 0.00009 Ksat` | `-2.1320 + 0.4778 ln(Ksat)` |
| 3, sandy-clayey | 5–150 | `-0.5677 - 0.00004 Ksat` | `-3.7189 + 0.5922 ln(Ksat)` |
| 4, silty-clayey | 1–150 | `-0.6366 + 0.0008 Ksat` | `-1.9165 + 0.7063 ln(Ksat)` |

A shallow water table raises FC toward SAT. For a compartment within the affected
distance:

$$
\Delta\theta_{FC}=(\theta_{SAT}-\theta_{FC})
\frac{(x-z)^2}{x^2},\quad z\le x.
$$

The driving force between WP and adjusted FC is:

$$
f_D=1-\left(
\frac{\theta-\theta_{WP}}{\theta_{FC,adj}-\theta_{WP}}
\right)^{n_D},
$$

where the default $n_D=16$. Below the midpoint between WP and FC, an additional
linear relative-conductivity factor declines to zero at WP.

The procedure works bottom-up:

1. Find the minimum `CRmax` imposed by every layer between the profile and the
   groundwater table.
2. Stop if the current compartment is draining (`fluxout != 0`).
3. Calculate the driving force, relative conductivity, and available storage up to
   `FCadj`.
4. Add water and groundwater salt to the active salt cell.
5. Reduce the remaining flux and continue upward.
