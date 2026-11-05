//! Root-zone and local water-stress checks.

use aquacrop_conformance::{OfficialTestcase, TestResult};
use aquacrop_core::stress::{
    DepletionThresholds, determine_root_zone_water, local_root_water_stress,
};
use assertables::assert_in_delta;

#[test]
fn wetter_upper_zone_is_selected_for_stomatal_response() -> TestResult {
    let testcase = OfficialTestcase::new();
    let profile = testcase.prepared_soil_profile()?;
    let crop = testcase.prepared_gdd_crop()?;
    let mut compartments = profile.compartments_at_field_capacity();
    for compartment in compartments.iter_mut().take(5).skip(2) {
        let layer = &profile.layers[compartment.layer_index];
        compartment.theta = layer.wilting_point / 100.0;
    }
    let water = determine_root_zone_water(
        &profile,
        &compartments,
        0.50,
        0.20,
        &DepletionThresholds {
            leaf_expansion: crop.parameters.water_stress.canopy_expansion_upper,
            stomatal_closure: crop.parameters.water_stress.stomatal_closure_upper,
            senescence: crop.parameters.water_stress.senescence_upper,
        },
    )?;
    let layer = &profile.layers[compartments[0].layer_index];
    let wet_factor = local_root_water_stress(
        &compartments[0],
        layer,
        crop.parameters.water_stress.stomatal_closure_upper,
        crop.parameters.water_stress.stomatal_closure_shape,
        0.0,
    );
    let dry_factor = local_root_water_stress(
        &compartments[2],
        layer,
        crop.parameters.water_stress.stomatal_closure_upper,
        crop.parameters.water_stress.stomatal_closure_shape,
        0.0,
    );
    assert!(water.upper_zone_is_wetter);
    assert_in_delta!(water.upper_actual, water.upper_field_capacity, 1.0e-12);
    assert!(water.actual > water.wilting_point);
    assert!(water.actual < water.field_capacity);
    assert_in_delta!(wet_factor, 1.0, f64::EPSILON);
    assert_in_delta!(dry_factor, 0.0, f64::EPSILON);

    Ok(())
}
