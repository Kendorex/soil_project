## 14. Transpiration and Root Water Extraction

### 14.1 Potential Transpiration

The base equation is:

$$
Tr_p=K_{s,Tr}\,K_{cTr}\,ETo,
\qquad K_{cTr}=CC^*K_{cTr,x,adj},
$$

where `Ks,Tr` is the cold-stress coefficient. Actual demand is subsequently limited
by water, salinity, and anaerobic conditions.

After senescence begins:

$$
K_{cTr,x,adj}\leftarrow K_{cTr,x,adj}
\left(\frac{CC}{CC_x}\right)^a,
$$

where the default $a=1$ (`ExpFsen`; approximately 0.1–4 is supported).

For elevated atmospheric CO₂ when `CO2 > 369.41`:

$$
K_{cTr,x,adj}\leftarrow K_{cTr,x,adj}
\left[1-0.05\frac{CO2-369.41}{550-369.41}\right].
$$

### 14.2 AquaCrop 7.3 Aging Function

In 7.2, a linear decrease began after a five-day lag:

$$
K_{cTr,x,adj}=K_{cTr,x}-(t-5)f_{age}CC_x.
$$

In 7.3, the lag is removed and `fage` is replaced by the cumulative reduction at
maturity, `fcumul = KcDeclineCumul/100`, with default 0.11:

$$
t_{rel}=\frac{D-D_{cut}-L_{12}}{L_{harvest}-L_{12}},
$$

$$
K_{cTr,x,adj}=K_{cTr,x}-
\frac{e^{t_{rel}}-1}{e^1-1}\,f_{cumul}\,CC_{x,withered}.
$$

The branch is active when `VirtualDay-DayLastCut > L12`. In GDD mode, `DAP` is
first converted to equivalent calendar days, so aging remains a function of relative
calendar duration. Cutting restarts the count through `DayLastCut`.

### 14.3 Water, Salinity, and Oxygen Limits

The stomatal thresholds are:

$$D_{upper}=p_{sto}TAW,\qquad D_{lower}=TAW.$$

The model first derives a water factor from either the entire root zone or the wetter
upper zone. `AdjustedKsStoToECsw` then adds the salinity effect. In 7.3:

$$T_{potMAX}=K_{s,ECsw}K_{s,water}T_p,$$

$$T_{potMAX}\leftarrow K_{s,aer}T_{potMAX}.$$

The anaerobic effect ramps up during `DelayLowOxygen` days. A corresponding local
factor reduces extraction capacity in each affected compartment.

### 14.4 Maximum Extraction Profile

`SmaxTop` and `SmaxBot` are derived from the specified average rates in the upper
and lower quarters of the root zone. `Smax` varies linearly with depth and is capped
at `0.06 m³/m³/day`:

$$
S_x(z)=S_{x,bottom}+(S_{x,top}-S_{x,bottom})
\frac{Z_r-z}{Z_r}.
$$

`WFactor` is 1 for a fully rooted compartment, the intersected fraction for the
boundary compartment, and 0 below the root zone.

### 14.5 AquaCrop 7.3 Patch: Extraction Distribution

In 7.2, compartments were processed from top to bottom, with each supplying as much
of the remaining demand as possible. This biased extraction toward the surface and
was particularly unsuitable for soil-moisture data assimilation.

In 7.3, two profiles are constructed. Local capacity after stress is:

$$
M_i=1000\,\alpha_i\,w_iS_{max,i}\Delta z_i.
$$

Unrestricted total capacity is:

$$U=\sum_i1000\,w_iS_{max,i}\Delta z_i.$$

The target extraction profile is:

$$r=\frac{T_{potMAX}}{U},\qquad
m_i=r\,1000\,w_iS_{max,i}\Delta z_i.$$

In the source, $M_i$ is `SinkMajor` and $m_i$ is `SinkMinor`. A top-down pass then
redistributes unmet target extraction through `residue`:

```text
residue = 0
Tact = 0
for i top -> bottom:
    target   = SinkMinor[i]
    capacity = SinkMajor[i]

    if target < capacity:
        sink = min(target + residue, capacity)
        residue = max(0, residue - (capacity-target))
    else:
        sink = capacity
        residue += target-capacity

    sink = min(sink, TpotMAX-Tact)
    theta[i] -= sink / (1000*dz[i]*(1-gravel[i]/100))
    Tact += sink
```

Guard the division `TpotMAX/U`. The Fortran source assumes that positive
transpiration implies nonzero rooted capacity, but an explicit assertion makes
invalid input diagnosable.

**Observed 7.3 source behavior.** In the second loop, where `theta` is updated,
`layeri` is not explicitly reassigned and retains its value from the preceding loop.
The gravel factor may therefore come from the last processed layer rather than the
current compartment's layer. A bit-compatible port should reproduce this behavior.
A scientifically corrected mode should use `compartment[i].Layer` and expose the
difference through an explicit compatibility flag. Do not fix it silently.
