//! Root-growth checks against the version 7.3 process rules.

use aquacrop_conformance::OfficialTestcase;
use aquacrop_core::crop::PreparedCropCycle;
use aquacrop_core::rooting::{
    RootingStress, RootingTime, advance_rooting_depth, depth_through_restrictive_layers,
    unrestricted_rooting_depth,
};
use assertables::assert_in_delta;

const INITIAL_ROOT_DEPTH: i32 = 70;

#[test]
fn water_stress_interruption_recovers_the_expansion_rate() {
    let (crop, profile) =
        official_crop_and_soil().expect("the official crop and soil files should parse");
    let compartments = profile.compartments_at_field_capacity();
    let previous = unrestricted_rooting_depth(&crop, 20, 0, 200.0, INITIAL_ROOT_DEPTH);
    let interrupted = advance_rooting_depth(
        &crop,
        &profile,
        &compartments,
        Some(previous),
        &rooting_time(120, 1_200.0, 10.0),
        &rooting_stress(0.0, 5.0),
        INITIAL_ROOT_DEPTH,
    );
    let recovered = advance_rooting_depth(
        &crop,
        &profile,
        &compartments,
        Some(interrupted.depth),
        &rooting_time(121, 1_210.0, 10.0),
        &rooting_stress(5.0, 5.0),
        INITIAL_ROOT_DEPTH,
    );

    assert_in_delta!(interrupted.depth, previous, f64::EPSILON);
    assert!(recovered.depth > interrupted.depth);
    assert!(recovered.depth < crop.parameters.rooting.maximum_depth);
}

#[test]
fn dry_subsoil_stops_the_daily_increment_at_the_expansion_front() {
    let (crop, profile) =
        official_crop_and_soil().expect("the official crop and soil files should parse");
    let mut compartments = profile.compartments_at_field_capacity();
    for compartment in &mut compartments {
        compartment.theta = profile.layers[compartment.layer_index].wilting_point / 100.0;
    }
    let previous = unrestricted_rooting_depth(&crop, 40, 0, 400.0, INITIAL_ROOT_DEPTH);
    let result = advance_rooting_depth(
        &crop,
        &profile,
        &compartments,
        Some(previous),
        &rooting_time(21, 210.0, 10.0),
        &rooting_stress(5.0, 5.0),
        INITIAL_ROOT_DEPTH,
    );

    assert_in_delta!(result.depth, previous, f64::EPSILON);
}

#[test]
fn restrictive_layer_scales_only_the_depth_inside_that_layer() {
    let (_, mut profile) =
        official_crop_and_soil().expect("the official crop and soil files should parse");
    profile.layers[0].penetrability = 50;

    assert_in_delta!(
        depth_through_restrictive_layers(1.0, &profile.layers),
        0.5,
        f64::EPSILON
    );
}

#[test]
fn calendar_and_gdd_modes_use_their_own_development_clocks() {
    let (mut crop, _) =
        official_crop_and_soil().expect("the official crop and soil files should parse");
    let gdd_depth = unrestricted_rooting_depth(&crop, 90, 0, 960.0, INITIAL_ROOT_DEPTH);
    crop.cycle = PreparedCropCycle::CalendarDays {
        calendar_days: crop.cycle.calendar_days().clone(),
    };
    let calendar_depth = unrestricted_rooting_depth(&crop, 89, 0, 0.0, INITIAL_ROOT_DEPTH);

    assert!(gdd_depth > crop.parameters.rooting.minimum_depth);
    assert!(gdd_depth < crop.parameters.rooting.maximum_depth);
    assert!(calendar_depth > crop.parameters.rooting.minimum_depth);
    assert!(calendar_depth < crop.parameters.rooting.maximum_depth);
    assert!((gdd_depth - calendar_depth).abs() > 1.0e-6);
}

fn official_crop_and_soil() -> Result<
    (
        aquacrop_core::crop::PreparedCrop,
        aquacrop_core::soil::PreparedSoilProfile,
    ),
    aquacrop_core::Error,
> {
    let testcase = OfficialTestcase::new();
    let crop = testcase.prepared_gdd_crop()?;
    let soil = testcase.prepared_soil_profile()?;
    Ok((crop, soil))
}

fn rooting_time(day_after_planting: i32, sum_gdd: f64, daily_gdd: f64) -> RootingTime {
    RootingTime {
        day_after_planting,
        delayed_days: 0,
        previous_sum_gdd: sum_gdd - daily_gdd,
        sum_gdd,
        daily_gdd,
    }
}

fn rooting_stress(actual: f64, potential: f64) -> RootingStress {
    RootingStress {
        potential_canopy: 80.0,
        actual_canopy: 80.0,
        potential_transpiration: potential,
        actual_transpiration: actual,
        cumulative_potential_transpiration: 100.0,
        cumulative_actual_transpiration: 100.0,
        root_shape_factor: Some(-6),
        germinated: true,
    }
}
