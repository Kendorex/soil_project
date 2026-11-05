## 15. Biomass and Atmospheric CO₂

### 15.1 Base Relationship

Daily dry above-ground biomass increment is:

$$
\Delta B_i=WP^*_{adj,i}\frac{Tr_i}{ETo_i},
\qquad B=\sum_i\Delta B_i.
$$

`WP*` is calibrated in g/m² and the source accumulates the resulting biomass in
t/ha according to AquaCrop's established numerical convention. In weed-infested
fields, use crop transpiration `TrW` rather than total canopy transpiration.

### 15.2 CO₂ Adjustment of WP

The crop-type factor is:

$$
f_{type}=\begin{cases}
1,&WP\le20,\\
(40-WP)/20,&20<WP<40,\\
0,&WP\ge40.
\end{cases}
$$

Sink strength is $f_{sink}=\operatorname{clamp}(AdaptedToCO2/100,0,1)$.

For the legacy curve at `CO2 <= 550`:

$$
w=\operatorname{clamp}\left(
1-\frac{550-CO2}{550-CO2_{ref}},0,1\right),
$$

$$
f_{old}=\frac{CO2/CO2_{ref}}
{1+(CO2-CO2_{ref})[(1-w)0.000138+
w(0.000138f_{sink}+0.001165(1-f_{sink}))]}.
$$

For the adjusted curve above the reference concentration:

$$
f_{shape}=-4.61824-3.43831f_{sink}-5.32587f_{sink}^2,
$$

$$
f_{adj}=1+0.58\frac{e^{f_{shape}CO2_{rel}}-1}
{e^{f_{shape}}-1},\quad
CO2_{rel}=\frac{CO2-CO2_{ref}}{2000-CO2_{ref}}.
$$

At `CO2 >= 2000`, $f_{adj}=1.58$. At or below `CO2Ref`, select the legacy curve;
between `CO2Ref` and 550, select the smaller of the two curves; above 550, select
the adjusted curve. The final multiplier is:

$$f_{CO2}=1+f_{type}(f_{selected}-1).$$

Complete adjusted productivity is:

$$WP^*_{adj}=f_{CO2}f_{yield}K_{s,WP,i}WP^*.$$

The fertility effect strengthens as the season proceeds. Its source implementation
depends on the ratio of accumulated normalized actual and potential transpiration,
approximately:

$$
K_{s,WP,i}=1-f_{WP}
\left(\frac{\sum Tr/ETo}{\sum Tr_x/ETo}\right)^2.
$$

The Mauna Loa update through 2023 changes the bundled input series, not these
equations.

## 16. Harvest Index and Yield

### 16.1 HI Development

The base logistic function is:

$$
HI_i=\frac{HI_{ini}HI_o}
{HI_{ini}+(HI_o-HI_{ini})e^{-HIGC\,t}},
\qquad HI_{ini}=0.01.
$$

For leafy vegetables, time starts after germination; for root and tuber crops, at
the start of yield-organ formation; and for grain and fruit crops, the logistic lag
phase is followed by linear growth. `HarvestIndexDay`, `HImultiplier`, and the crop
type branches in `DetermineBiomassAndYield` are authoritative for boundary cases.

When early senescence reduces green canopy below the configured threshold, HI stops
increasing because the photosynthetic surface is insufficient.

### 16.2 Stress Before Yield Formation

Relative biomass at flowering or tuber formation is:

$$B_{rel}=B/B_0.$$

The range in which HI may increase is:

$$Range(B_{rel})=\frac{\ln(\Delta HI_{ante})}{5.62}\le1.$$

Within that range, `fante` uses two sine branches around the point of maximum effect.
The response is asymmetric: the maximum divides the range in proportions 2/3 and
1/3. Use `HImultiplier` and its callers for exact zero-biomass handling and limits.

### 16.3 Pollination

Relative flowering distribution at percentage time $k$ is:

$$f_k=0.00558k^{0.63}-0.000969k-0.00383.$$

Each day contributes successfully pollinated flowers to attainable HI:

$$
HI_{adj}=\sum_jK_{s,j}\left(1+\frac{f_{excess}}{100}\right)F_jHI_o,
\qquad HI_{adj}\le HI_o.
$$

$K_{s,j}$ is limited by water, cold, and heat stress. Complete pollination failure
occurs 5 °C beyond the corresponding onset threshold. Pollination is evaluated in
calendar days even for a GDD crop.

### 16.4 Stress After the Start of Yield Formation

Positive daily adjustment from restricted leaf growth:

$$
\left(\frac{dHI}{dt}\right)_i=
\left(1+\frac{1-K_{s,exp,i}}{a}\right)
\left(\frac{dHI}{dt}\right)_0.
$$

The positive multiplier is:

$$
f_{post\uparrow}=1+\frac{1}{n_{exp}}
\sum_i\frac{1-K_{s,exp,i}}{a}.
$$

The negative stomatal effect is:

$$
\left(\frac{dHI}{dt}\right)_i=
K_{s,sto,i}^{1/10}\left(1-\frac{1-K_{s,sto,i}}{b}\right)
\left(\frac{dHI}{dt}\right)_0,
$$

$$
f_{post\downarrow}=\frac{1}{n_{yield}}
\sum_iK_{s,sto,i}^{1/10}
\left(1-\frac{1-K_{s,sto,i}}{b}\right).
$$

When the periods have different lengths:

$$
f_{post}=\left(
\frac{w_1f_{post\uparrow}+w_2-w_1}{w_2}
\right)f_{post\downarrow}.
$$

Final harvest index is:

$$HI=f_{ante}f_{post}HI_{adj},$$

$$HI\le\left(1+\frac{\Delta HI_{tot}}{100}\right)HI_o.$$

### 16.5 Yield and Water Productivity

$$Y_{dry}=HI\,B,$$

$$Y_{dry}=f_mY_{fresh},$$

$$WP_{ET}=\frac{Y}{E_{TOT}+Tr_{TOT}}.$$

Handle units explicitly in the output layer. Yield and water are stored internally in
t/ha and mm, while the reported water productivity is commonly expressed in kg/m³.
