## 9. Green Canopy Cover

### 9.1 Optimal Growth

Before half the maximum canopy cover:

$$CC(t)=CC_o\exp(CGC\,t).$$

After half the maximum:

$$CC(t)=CC_x-0.25\frac{CC_x^2}{CC_o}\exp(-CGC\,t).$$

The GDD mode uses the equivalent function with `GDDCGC` and accumulated GDD.
For sowing, `CCo` is derived from canopy area per seedling and planting density;
the source divides both corresponding quantities by `10000` during conversion.

### 9.2 Canopy Decline

After the start of senescence, the unstressed curve is:

$$
CC(t)=CC_x\left[1-0.05\left(
\exp\frac{3.33\,CDC\,t}{CC_x+2.29}-1\right)\right].
$$

The GDD mode uses `GDDCDC`. The time origin is the start of senescence; an
off-by-one error is readily visible in daily output.

### 9.3 Water Stress and Feedback

Under leaf-expansion stress:

$$CGC_{adj}=K_{s,exp,w}\,CGC.$$

Severe stress may trigger early canopy senescence. Growth can resume after rewatering
while the crop is still inside its potential vegetative-growth period. When actual
transpiration is zero, `FeedbackCC` halts canopy development; therefore CC cannot be
computed independently from the calendar alone.

### 9.4 Fertility and Salinity

$$CGC_{adj}=K_{s,exp,f}\,CGC,\qquad
CC_{x,adj}=K_{s,CCx}\,CC_x.$$

After the reduced maximum cover has been reached, stress may create an additional
quadratic decline:

$$
CC_{adj}=CC_{x,adj}-f_{CDecline}
\frac{(t-t_{full})^2}{t_{sen}-t_{full}}.
$$

The response shapes are prepared before the run, but the effective fertility level is
dynamic. When water or salinity stress suppresses biomass, effective fertility stress
temporarily decreases, representing lower nutrient removal.

### 9.5 Perennials and Self-Thinning

The elapsed time represented by an initial regrowth canopy is:

$$t=\frac{\ln(CC_{ini}/CC_o)}{CGC},\quad CC_{ini}\le CC_x/2,$$

$$
t=-\frac{1}{CGC}\ln\frac{(CC_x-CC_{ini})CC_o}{0.25CC_x^2},
\quad CC_{ini}>CC_x/2.
$$

For current year $y$, the year $y_x$ at which CCx reaches 90%, and shape $s$:

$$y_0=1+(y_x-1)10^s,$$

$$
f_{CCx}=0.9+0.1\left[1-
\left(\frac{y-1}{y_x-1}\right)^{1/s}\right],
$$

within the valid year range. For the initial canopy:

$$f_{CCo}=1-\frac{y-1}{y_0-1}.$$

Cutting resets canopy age and initiates a new regrowth phase. In 7.3, aging of
`KcTr,x` is also counted from `DayLastCut`.

### 9.6 Weeds

Crop and weed cover are treated as a combined canopy. For relative weed cover $RC$:

$$RC=\frac{CC_W}{CC_W+CC_C},\qquad CC_C=CC_{TOT}(1-RC).$$

The total-canopy multiplier is:

$$
f_{weed}=1-\left(1-\frac{1}{CC_{x,WF}}\right)
\frac{e^{f_{shape}RC}-1}{e^{f_{shape}}-1},
$$

limited to $1/CC_{x,WF}$. Then `CCoTOT=fweed*CCoWF` and
`CCxTOT=fweed*CCxWF`. Crop biomass and yield must use crop transpiration `TrW`,
not the transpiration of the combined canopy.
