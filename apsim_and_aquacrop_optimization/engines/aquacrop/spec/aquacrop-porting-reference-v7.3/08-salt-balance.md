## 12. Salt Balance

### 12.1 Representation

Each compartment is divided into 2–11 salt cells in water-content space:

$$
N_{cell}=\operatorname{clamp}\left(
\operatorname{roundc}\left(1.6+\frac{1000}{K_{sat}}\right),2,11\right).
$$

The implementation explicitly sets 11 cells when `Ksat <= 112`; otherwise it uses
the rounded expression and enforces the lower bound. The first `SC` cells have equal
water-content width `Dx`; the last cell covers the upper range to saturation and has
a different size. Salt is represented separately as dissolved mass (`Salt`) and
deposit (`Depo`). Solubility limits the dissolved fraction.

For a cell containing $W_{cell}$ mm of water:

$$Salt_{cell}=0.64\,W_{cell}\,EC_{cell}\quad[g/m^2].$$

For a complete compartment:

$$
EC_{sw}=\frac{\sum(Salt+Depo)}
{0.64\cdot1000\theta\Delta z(1-v_g/100)},
$$

$$
EC_e=\frac{\sum(Salt+Depo)}
{0.64\cdot1000\theta_{SAT}\Delta z(1-v_g/100)}.
$$

Do not derive `ECe` solely by scaling the current `ECsw`. For compatibility, use
the same active-cell and deposition procedure as the source.

### 12.2 Transport Order

1. Infiltration transports salt downward by convection.
2. Drainage exports dissolved salt according to cell-specific mobility.
3. Diffusion redistributes salt between adjacent cells.
4. Evaporation moves salt upward with:

$$
f_{salt}=\frac{SWC_{rel}}{10}\,10^{SWC_{rel}},\qquad
SWC_{rel}=\frac{\theta-\theta_{airdry}}
{\theta_{SAT}-\theta_{airdry}}.
$$

5. Capillary rise adds `0.64 * CRcomp * ECgroundwater` to the active salt cell.
6. After evaporation and transpiration, `ConcentrateSalts` reconciles dissolved and
   deposited mass.

Water and salt balances are checked together at the beginning and end of each day.
Store salt mass internally in g/m² and convert seasonal report totals to Mg/ha only
at the output boundary.
