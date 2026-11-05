## 23. Quick Index: Formula to Fortran Procedure

| Area | Reference procedure |
|---|---|
| Round-half-to-even | `utils.f90: roundc_int32`, `roundc_int8` |
| GDD | `global.f90: DegreesDay` |
| Generic `Ks` | `global.f90: KsAny` |
| Temperature response | `global.f90: KsTemperature` |
| Unstressed canopy | `global.f90: CCatTime`, `CCatGDD` |
| Self-thinning | `MultiplierCCxSelfThinning`, `MultiplierCCoSelfThinning` |
| CO₂ adjustment of WP | `global.f90: fAdjustedForCO2` |
| Root depth | `rootunit.f90: AdjustedRootingDepth`; `run.f90: CalculateRootingDepth` |
| Potential E/Tr and 7.3 Kc aging | `global.f90: CalculateETpot` |
| Drainage | `simul.f90: calculate_delta_theta`, `calculate_drainage` |
| Runoff | `simul.f90: calculate_runoff`; `global.f90: DetermineCNIandIII` |
| Infiltration | `simul.f90: calculate_infiltration` |
| Capillary rise | `simul.f90: calculate_CapillaryRise`; `global.f90: MaxCRatDepth` |
| Salt | `simul.f90: calculate_saltcontent`, `ConcentrateSalts` |
| Evaporation | `PrepareStage1/2`, `CalculateSoilEvaporationStage1/2` |
| 7.3 transpiration | `simul.f90: calculate_transpiration` |
| Stressed canopy | `DetermineCCi`, `DetermineCCiGDD`, `FeedbackCC` |
| Biomass and yield | `DeterminePotentialBiomass`, `DetermineBiomassAndYield` |
| Daily process order | `simul.f90: BUDGET_module`; `run.f90: AdvanceOneTimeStep` |

---

### Completeness Note

This guide covers the scientific core and the significant 7.3 changes. Exact line
formats for `.PRO/.PRM/.CLI/.CRO/.SOL/.IRR/.MAN` files and exact `.OUT` formatting
should be ported directly from the version-gated readers and writers at the 7.3 tag.
That is a separate I/O specification and should not be mixed into the physical
process implementation.
