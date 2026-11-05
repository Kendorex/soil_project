//! Potential-canopy checks using the official crop parameters.

use aquacrop_conformance::OfficialTestcase;
use aquacrop_core::canopy::{
    derive_canopy_timing, perennial_weed_adjustment, potential_canopy_cover,
};
use assertables::{assert_eq_f64, assert_in_delta};

#[test]
fn official_gdd_crop_respects_emergence_and_maturity_boundaries() {
    let crop = OfficialTestcase::new()
        .prepared_gdd_crop()
        .expect("the official crop file should parse");

    let at_emergence = potential_canopy_cover(&crop, 1, 0, 5.0, 0, 0);
    let during_growth = potential_canopy_cover(&crop, 30, 0, 300.0, 0, 0);
    let after_maturity = potential_canopy_cover(&crop, 181, 0, 1_921.0, 0, 0);

    assert_eq_f64!(at_emergence, 0.0_f64);
    assert!(during_growth > crop.parameters.canopy.initial_seedling_cover);
    assert!(during_growth <= crop.parameters.canopy.maximum_cover);
    assert_eq_f64!(after_maturity, 0.0_f64);
}

#[test]
fn official_gdd_crop_derives_reference_full_canopy_times() {
    let crop = OfficialTestcase::new()
        .prepared_gdd_crop()
        .expect("the official crop file should parse");

    let timing = derive_canopy_timing(&crop);

    assert_eq!(timing.days_to_initial_cover, Some(0));
    assert_eq!(timing.days_to_full_canopy, 33);
    assert_eq!(timing.gdd_to_initial_cover, Some(0));
    assert_eq!(timing.gdd_to_full_canopy, 461);
}

#[test]
fn perennial_weed_replacement_matches_pinned_source_routine() {
    let adjustment = perennial_weed_adjustment(20, 0.8, -0.01, 0.8, 3, 100);

    assert_in_delta!(adjustment.canopy_multiplier, 1.312_750_249_666, 1.0e-12);
    assert_eq!(adjustment.relative_cover, 36);
}
