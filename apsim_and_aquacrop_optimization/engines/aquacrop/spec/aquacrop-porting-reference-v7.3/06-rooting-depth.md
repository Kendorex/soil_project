## 10. Rooting Depth

### 10.1 Unrestricted Function

Let $Z_0$ be the initial point of the root function, $Z_x$ the maximum depth,
$t_0$ the germination time, $t_x$ the time at maximum rooting, and $n$ the shape:

$$
Z(t)=Z_0+(Z_x-Z_0)
\left(\frac{t-t_0/2}{t_x-t_0/2}\right)^{1/n}.
$$

The crop file stores `RootShape` as `10*n`. Actual root depth is constrained by
`Zn <= Z <= Zx`, and the mean expansion rate cannot exceed 5 cm/day. $Z_0$ is
specified as a percentage of `Zn` through `RootPercentZmin`.

### 10.2 Restrictions

Restrictions are applied to the daily increment, not to total depth:

$$dZ_{adj}=f_{red}dZ.$$

For root water stress with shape $f_r$:

$$
f_{red}=\frac{\exp(K_{s,sto}f_r)-1}{\exp(f_r)-1}.
$$

The default is $f_r=-6$. A nonnegative `KsShapeFactorRoot` selects the linear
factor. In a restrictive soil layer, $f_{red}=Penetrability/100$. Dry soil at the
expansion front is tested at:

$$p_{Zr,exp}=p_{sto}+\frac{1-p_{sto}}{2}.$$

The exact `AdjustedRootingDepth` implementation uses the passed previous-state
ratio `Tact/Tpot` rather than calculating a new `Ks,sto`. With a nonnegative root
shape it uses `Tact/Tpot` directly; with a negative shape it uses the exponential
response above. Root expansion therefore has a one-day feedback from previously
achieved transpiration.

### 10.3 AquaCrop 7.3 Patch: Recovery of Expansion Rate

In 7.2, the slope of the unrestricted root curve was selected using actual time
since planting. After a long restriction, time could be close to $t_x$, making the
slope almost zero and preventing roots from ever reaching $Z_x$.

In 7.3, an effective time is first recovered from the root depth reached on the
previous day, $Z_{prev}$:

$$q=\left(\frac{Z_{prev}-Z_0}{Z_x-Z_0}\right)^n,$$

$$
t_{eff}=q\left(t_x-\frac{t_0}{2}\right)+\frac{t_0}{2}.
$$

In calendar mode, the source rounds `t_eff` with `roundc` and adds one day. In GDD
mode it adds `GDDayi`. The resulting effective time may not exceed actual crop time.
The existing `AdjustedRootingDepth` procedure is then called, and the result is
bounded below by `Zprev`, so root depth cannot decrease.

```text
Z0 = RootMin * RootPercentZmin / 100
t_eff = actual_time
if RootMin < RootMax and Zprev > Z0:
    q = ((Zprev-Z0)/(RootMax-Z0)) ** n
    if expansion_already_beyond_Zmin:
        t_eff_prev = q*(tx-t0/2) + t0/2
        t_eff = min(actual_time, t_eff_prev + one_step)
Znew = adjusted_root_depth(t_eff, stresses, dry_front, penetrability)
Znew = max(Znew, Zprev)
```

Create separate tests for water-stress interruption, a dry subsoil, a restrictive
layer, and both calendar and GDD crop modes.
