## 7. Stress-Response Functions

### 7.1 Generic Water-Stress Function

Let relative depletion be $W_{rel}=D_r/TAW_r$, with upper and lower thresholds
$p_U$ and $p_L$. Define:

$$s=\frac{W_{rel}-p_U}{p_L-p_U}.$$

If $s\le0$, $K_s=1$; if $s\ge1$, $K_s=0$; otherwise:

$$
K_s=1-\frac{\exp(f_{shape}s)-1}{\exp(f_{shape})-1}.
$$

When `roundc(10*f_shape)==0`, `KsAny` substitutes `f_shape=0.01`. Thus the
nominally linear case is implemented as a nearly linear exponential, not literally
as `1-s`. Repeat this behavior for strict compatibility.

If `pL-pU < 0.0001`, the source sets `pU = pL - 0.0001`.

### 7.2 ETo Adjustment of a Depletion Threshold

For thresholds using the FAO adjustment method:

$$
p_{adj}=\operatorname{clamp}\left[
p+f_{adj}\,0.04(5-ETo)\log_{10}(10-9p),0,1\right].
$$

Apply the adjustment daily before evaluating the associated `Ks`. AquaCrop compares
relative depletion in the entire root zone and the upper soil zone, then uses the
wetter of the two for stress determination.

### 7.3 Temperature Stress

`KsTemperature(T0,T1,T)` handles both cold stress (`T0<T1`) and heat stress
(`T0>T1`). Between the thresholds it uses a logistic response. With
$r=(T-T_0)/(T_1-T_0)$:

$$M_0=0.02,\qquad M_x=1,$$

$$q=-\ln\frac{M_0M_x-0.98M_0}{0.98(M_x-M_0)},$$

$$
K_s=\frac{M_0M_x}{M_0+(M_x-M_0)e^{-qr}}-M_0(1-r),
$$

followed by clamping to `[0,1]`. If the thresholds are undefined or equal, the
factor is 1.

### 7.4 Combining Stresses

- Water stress separately affects leaf expansion, stomata, senescence, pollination,
  and root growth.
- Fertility stress produces `Ksexp,f`, `KsCCx`, `fCDecline`, and `KsWP`.
- Salinity produces analogous canopy effects and `Kssto,salt`.
- Fertility and salinity effects on canopy development are **not added**. The stronger
  effect, corresponding to the smaller `Ks`, is selected at each time step.
- Increasing `ECsw` intensifies stomatal closure as the soil dries even when `ECe`,
  the seasonal salinity indicator, remains unchanged.

## 8. Growing Degree Days

All methods finish with:

$$GDD=T_{avg}^{*}-T_{base}.$$

| Method | Temperature preparation |
|---:|---|
| 1 | Average raw `Tmax,Tmin`, then clamp the average to `[Tbase,Tupper]` |
| 2 | Clamp `Tmax` and `Tmin` separately to `[Tbase,Tupper]`, then average |
| 3 | Clamp `Tmax` to `[Tbase,Tupper]`; cap `Tmin` only at `Tupper`; average and raise the result to `Tbase` if needed |

```text
method 1: Tavg* = clamp((Tmax+Tmin)/2, Tbase, Tupper)
method 2: Tavg* = (clamp(Tmax,Tbase,Tupper)+clamp(Tmin,Tbase,Tupper))/2
method 3: Tmax*=clamp(Tmax,Tbase,Tupper)
          Tmin*=min(Tmin,Tupper)
          Tavg*=max((Tmax*+Tmin*)/2,Tbase)
```

Crop stages may use calendar days or accumulated GDD. Do not merge `SumGDD`,
`SumGDDfromDay1`, previous-day GDD, and the GDD total adjusted for canopy
development: they serve different purposes in the Fortran code.
