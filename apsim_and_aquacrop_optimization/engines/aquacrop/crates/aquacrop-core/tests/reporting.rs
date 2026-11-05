//! Filesystem-independent reporting API checks.

#![cfg(not(feature = "file-io"))]

use aquacrop_core::engine::FinalResult;
use aquacrop_core::output::{SeasonalReport, SimulationResult};

#[test]
fn completed_run_exposes_reports_without_file_io() {
    let result = SimulationResult {
        daily: Vec::new(),
        final_result: FinalResult {
            last_day_number: None,
            biomass: 0.0,
            unrestricted_biomass: 0.0,
            dry_yield: 0.0,
            stored_assimilates: 0.0,
        },
    };

    assert!(result.daily_reports(None).is_empty());
    let evaluation = result
        .evaluate(1.0, &[])
        .expect("an empty observation set should be valid");
    assert!(evaluation.canopy.is_empty());
    assert!(evaluation.biomass.is_empty());
    assert!(evaluation.soil_water.is_empty());

    let seasonal: Option<SeasonalReport> = None;
    assert!(seasonal.is_none());
}
