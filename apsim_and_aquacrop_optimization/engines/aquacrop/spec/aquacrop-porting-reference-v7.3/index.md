# AquaCrop 7.3 Porting Reference

This is the step-oriented index for porting the AquaCrop 7.3 computational core. The
content is split by implementation concern while preserving the original section
numbers, so references such as “Section 14.5” remain unambiguous.

## Recommended reading order

Read the files in order. The first group establishes the version baseline and
architecture; the middle files follow the daily simulation pipeline; the final
files define verification and completion criteria.

00. [Scope and Version Baseline and Significant Changes in AquaCrop 7.3](00-overview.md)
01. [Architecture of the Reference Implementation](01-architecture.md)
02. [Numerical Compatibility](02-numerical-compatibility.md)
03. [Units and Basic Conversions and One Daily Step: Required Ordering](03-units-and-daily-step.md)
04. [Stress-Response Functions and Growing Degree Days](04-stress-and-gdd.md)
05. [Green Canopy Cover](05-green-canopy.md)
06. [Rooting Depth](06-rooting-depth.md)
07. [Soil-Water Balance](07-soil-water-balance.md)
08. [Salt Balance](08-salt-balance.md)
09. [Soil Evaporation](09-soil-evaporation.md)
10. [Transpiration and Root Water Extraction](10-transpiration-and-extraction.md)
11. [Biomass and Atmospheric CO₂ and Harvest Index and Yield](11-biomass-and-yield.md)
12. [Input Changes in AquaCrop 7.3 and Suggested Port Structure](12-inputs-and-port-structure.md)
13. [Verification Strategy](13-verification-strategy.md)
14. [Known Porting Traps and Practical Implementation Sequence and Definition of Done for an AquaCrop 7.3 Port](14-traps-sequence-and-done.md)
15. [Quick Index: Formula to Fortran Procedure](15-quick-index.md)

## Porting workflow

1. Read the overview, architecture, numerical compatibility, units, and daily-step
   files before implementing model code.
2. Implement the process files in daily-step order, using the procedure names and
   state fields as the boundary between modules.
3. Use the verification, traps, sequence, and definition-of-done files to drive
   tests and diagnose the first divergent day or process.
4. Use the quick index to locate the corresponding Fortran procedure when a formula
   needs source-level confirmation.

## Scope boundary

The guide covers the scientific core and significant AquaCrop 7.3 changes. Exact
version-gated file formats and OUT formatting remain a separate I/O specification;
see the completeness note in [the quick index](15-quick-index.md).
