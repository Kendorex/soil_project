//! Regression coverage for the effective crop-cycle length.

use std::fs;
use std::path::Path;

use aquacrop_core::canopy::{derive_canopy_timing, effective_cycle_length};
use aquacrop_core::climate::GrowingDegreeDayMethod;
use aquacrop_core::crop::CropSeason;
use aquacrop_core::io::parse_crop_file;

const FIXTURES: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/crops");

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn read_fixture(name: &str) -> Result<String, std::io::Error> {
    fs::read_to_string(Path::new(FIXTURES).join(name))
}

/// A stage sum below maturity remains unchanged.
#[test]
fn wheatliza_effective_cycle_length_is_one_day_short_of_declared_maturity() -> TestResult {
    let crop = parse_crop_file(&read_fixture("WheatLiza.CRO")?)?
        .for_season(CropSeason::new(GrowingDegreeDayMethod::default()))?
        .prepare()?;
    assert_eq!(crop.cycle.calendar_days().maturity, 118);

    let timing = derive_canopy_timing(&crop);
    let length = effective_cycle_length(&crop, timing.days_to_full_canopy);

    assert_eq!(length, 117, "WheatLiza effective cycle length");
    Ok(())
}

/// A stage sum above maturity is clamped.
#[test]
fn wheatlubava_effective_cycle_length_matches_declared_maturity_exactly() -> TestResult {
    let crop = parse_crop_file(&read_fixture("WheatLubava.CRO")?)?
        .for_season(CropSeason::new(GrowingDegreeDayMethod::default()))?
        .prepare()?;
    assert_eq!(crop.cycle.calendar_days().maturity, 118);

    let timing = derive_canopy_timing(&crop);
    let length = effective_cycle_length(&crop, timing.days_to_full_canopy);

    assert_eq!(length, 118, "WheatLubava effective cycle length");
    Ok(())
}
