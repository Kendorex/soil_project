## 4. Numerical Compatibility

### 4.1 Types, Constants, and Indexing

- Core calculations use `real64` (`real(dp)`). Use IEEE-754 binary64.
- Fortran arrays are 1-based. Document every translation to 0-based indexing.
- The source supports at most 5 soil layers, 12 compartments, and 11 salt cells per
  compartment. The usual grid is 12 compartments of 0.1 m, but this thickness must
  not be hard-coded.
- The simulation time step is one calendar day. Stage-II evaporation subdivides the
  day into 20 internal steps.
- `undef_double = -9.9` and `undef_int = -9` are sentinels, not physical values.
- `equiv = 0.64`: `1 dS/m = 0.64 g/L`.
- `CO2Ref = 369.41 ppm`.
- `EvapZmin = 15 cm`; `ac_zero_threshold = 1e-6`.
- Do not replace every near-zero comparison with one global epsilon. The source uses
  machine `epsilon(0.0_dp)`, the domain threshold `1e-6`, and local thresholds such
  as `1e-5` for different purposes.

### 4.2 Rounding

`roundc` implements round-half-to-even and clips the input to the target integer
range before conversion. Examples:

```text
roundc( 1.5) =  2
roundc( 2.5) =  2
roundc(-1.5) = -2
roundc(-2.5) = -2
```

This affects dates, GDD, the number of salt cells, and conditions such as
`roundc(100*CC)==0`. The standard `round()` function in some languages rounds
halves away from zero and is therefore not compatible.

`trunc` removes the fractional part toward zero. Calendar functions contain small
empirical additions such as `+0.05` and `+0.25+0.06`; do not remove them as noise.

### 4.3 Invariants and Clamping

Check after every process:

- `0 <= CC <= 1`;
- `WP <= theta*100 <= SAT` under normal conditions, although temporary values above
  FC and some paths below WP are allowed until the responsible procedure applies its
  own physical limit;
- `0 <= WFactor <= 1`;
- `0 <= Ks <= 1`;
- `0 <= Zr <= min(crop.RootMax, soil.RootMax)`;
- water and salt are not created without a corresponding input flux;
- `Tact <= TpotMAX` within tolerance;
- compartment thicknesses cover every active depth used by the model.

Do not add extra clamps inside equations without compatibility tests. They can alter
error accumulation and the day on which stage transitions occur.
