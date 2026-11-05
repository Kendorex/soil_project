//! Conformance checks for the official crop input and its version switches.

use std::assert_matches;

use aquacrop_conformance::{OfficialTestcase, TestResult, read_prepared_crop};
use aquacrop_core::climate::GrowingDegreeDayMethod;
use aquacrop_core::crop::{AirTemperatureCriterion, CropCycle, CropKind, CropSeason};
use aquacrop_core::io::{parse_crop_file, read_crop_file};
use assertables::{assert_eq_f64, assert_in_delta};

#[test]
fn official_crop_file_preserves_primary_parameters() {
    let path = OfficialTestcase::new().gdd_crop_path();
    let crop = read_prepared_crop(path).expect("the official crop file should parse");

    assert_eq!(crop.parameters.kind, CropKind::Forage);
    assert!(crop.cycle.uses_growing_degree_days());
    assert_eq!(crop.cycle.driving_calendar().maturity, 1_920);
    assert_eq!(crop.cycle.calendar_days().maturity, 180);
    assert_eq_f64!(crop.parameters.rooting.maximum_depth, 3.0_f64);
    assert_eq_f64!(crop.parameters.canopy.maximum_cover, 0.95_f64);
    assert_eq!(crop.parameters.productivity.reference_harvest_index, 100);
    assert!(crop.parameters.premature_end_day.is_none());
    assert!(
        crop.parameters
            .development
            .potential_excess_fruits
            .is_none()
    );
    assert!(
        crop.parameters
            .productivity
            .pre_flowering_harvest_index_increase
            .is_none()
    );
    assert!(
        crop.parameters
            .productivity
            .vegetative_growth_impact_coefficient
            .is_none()
    );
    assert!(
        crop.parameters
            .productivity
            .stomatal_closure_impact_coefficient
            .is_none()
    );
    assert!(
        crop.parameters
            .productivity
            .maximum_harvest_index_increase
            .is_none()
    );
}

#[test]
fn calendar_day_crop_file_discards_the_inactive_gdd_calendar() {
    let path = OfficialTestcase::new().default_crop_path();
    let crop = read_crop_file(path).expect("the default crop file should parse");

    assert_matches!(crop.cycle, CropCycle::CalendarDays { .. });
}

#[test]
fn default_crop_season_uses_the_standard_gdd_method_without_temperatures() {
    let season = CropSeason::default();

    assert_eq!(
        season.gdd_method,
        GrowingDegreeDayMethod::ClampUpperTemperature
    );
    assert!(season.temperatures.is_none());
}

#[test]
fn gdd_crop_file_decodes_partial_calendar_day_sentinels() {
    let current =
        include_str!("../../../third-party/KUL-RSDA/AquaCrop/testcase/DATA/AlfOttawaGDD.CRO");
    let partial = current
        .lines()
        .map(|line| {
            if line.contains("Canopy growth coefficient (CGC):") {
                "    -9         : Canopy growth coefficient (CGC): placeholder"
            } else if line.contains("Calendar Days: from sowing to maturity") {
                "    -9         : Calendar Days: from sowing to maturity"
            } else {
                line
            }
        })
        .collect::<Vec<_>>()
        .join("\n");
    let crop = parse_crop_file(&partial).expect("partial calendar days should parse");
    let calendar_days = match crop.cycle {
        CropCycle::UnresolvedGrowingDegreeDays { calendar_days, .. } => Some(calendar_days),
        CropCycle::CalendarDays { .. } | CropCycle::GrowingDegreeDays { .. } => None,
    }
    .expect("the crop should use GDD");

    assert_eq!(calendar_days.emergence, Some(2));
    assert!(calendar_days.maturity.is_none());
    assert!(calendar_days.canopy_growth_coefficient.is_none());
    assert_eq!(calendar_days.canopy_decline_coefficient, Some(0.03636));
}

#[test]
fn official_crop_file_derives_initial_canopy_cover() -> TestResult {
    let path = OfficialTestcase::new().gdd_crop_path();
    let crop = read_crop_file(path)?
        .for_season(CropSeason::new(
            GrowingDegreeDayMethod::ClampUpperTemperature,
        ))?
        .prepare()?;
    let expected_seedling: f64 = (2_000_000.0 / 10_000.0) * (2.5 / 10_000.0);
    let expected_regrowth: f64 = (2_000_000.0 / 10_000.0) * (19.38 / 10_000.0);

    assert_eq_f64!(
        crop.parameters.canopy.initial_seedling_cover,
        expected_seedling
    );
    assert_eq_f64!(
        crop.parameters.canopy.initial_regrowth_cover,
        expected_regrowth
    );

    Ok(())
}

#[test]
fn official_crop_file_derives_root_extraction_endpoints() {
    let path = OfficialTestcase::new().gdd_crop_path();
    let crop = read_prepared_crop(path).expect("the official crop file should parse");

    assert_in_delta!(crop.parameters.rooting.extraction_top, 0.021_67, 1.0e-5);
    assert_in_delta!(crop.parameters.rooting.extraction_bottom, 0.008_33, 1.0e-5);
}

#[test]
fn crop_preparation_rejects_invalid_root_depth_order() {
    let path = OfficialTestcase::new().gdd_crop_path();
    let mut config = read_crop_file(path).expect("the official crop file should parse");
    config.parameters.rooting.minimum_depth = config.parameters.rooting.maximum_depth + 0.1;

    assert!(
        config
            .for_season(CropSeason::new(
                GrowingDegreeDayMethod::ClampUpperTemperature,
            ))
            .and_then(aquacrop_core::crop::SeasonCrop::prepare)
            .is_err()
    );
}

#[test]
fn official_crop_file_derives_senescence_to_maturity_intervals() {
    let path = OfficialTestcase::new().gdd_crop_path();
    let crop = read_prepared_crop(path).expect("the official crop file should parse");

    assert_eq!(crop.cycle.calendar_days().senescence_to_maturity(), 0);
    assert_eq!(crop.cycle.driving_calendar().senescence_to_maturity(), 0);
}

#[test]
fn official_forage_crop_preserves_internal_calendar() {
    let path = OfficialTestcase::new().gdd_crop_path();
    let crop = read_prepared_crop(path).expect("the official crop file should parse");
    let period = crop
        .parameters
        .perennial_period
        .as_ref()
        .expect("the official forage crop should define a perennial period");

    assert_eq!(
        period.onset.criterion,
        Some(AirTemperatureCriterion::GrowingDegreeDays)
    );
    assert_eq!(period.onset.first_day, 1);
    assert_eq!(period.onset.first_month, 4);
    assert_eq!(period.onset.search_length, 120);
    assert_eq!(period.onset.occurrences, 2);
    assert_eq!(
        period.end.criterion,
        Some(AirTemperatureCriterion::GrowingDegreeDays)
    );
    assert_eq!(period.end.last_day, 31);
    assert_eq!(period.end.last_month, 10);
    assert_eq!(period.end.search_length, 60);
}

#[test]
fn pre_7_3_crop_uses_legacy_compatibility_defaults() -> TestResult {
    let current =
        include_str!("../../../third-party/KUL-RSDA/AquaCrop/testcase/DATA/AlfOttawaGDD.CRO");
    let legacy = current
        .lines()
        .enumerate()
        .map(|(index, line)| match index {
            1 => "7.2 : AquaCrop version",
            25 => "200 : ignored premature-end field",
            35 => "0.25 : legacy daily Kc decline",
            _ => line,
        })
        .collect::<Vec<_>>()
        .join("\n");
    let crop = parse_crop_file(&legacy)?
        .for_season(CropSeason::new(
            GrowingDegreeDayMethod::ClampUpperTemperature,
        ))?
        .prepare()?;

    assert!(crop.parameters.premature_end_day.is_none());
    assert_eq_f64!(
        crop.parameters.evapotranspiration.kc_decline_cumulative,
        11.0_f64
    );

    Ok(())
}
