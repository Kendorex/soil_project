//! Root-profile and AquaCrop 7.3 transpiration-distribution checks.

use aquacrop_conformance::{OfficialTestcase, TestResult, profile_water};
use aquacrop_core::soil::{PreparedSoilProfile, SoilLayer};
use aquacrop_core::transpiration::{
    ExtractionGravelMode, RootExtractionParameters, extract_root_water,
};
use assertables::{assert_eq_f64, assert_in_delta};

#[test]
fn official_root_profile_extracts_target_and_conserves_water() -> TestResult {
    let testcase = OfficialTestcase::new();
    let profile = testcase.prepared_soil_profile()?;
    let crop = testcase.prepared_gdd_crop()?;
    let mut compartments = profile.compartments_at_field_capacity();
    let initial_water = profile_water(&profile, &compartments);
    let stress = vec![1.0; compartments.len()];
    let extraction = extract_root_water(
        &profile,
        &mut compartments,
        &RootExtractionParameters {
            rooting_depth: 0.50,
            potential_transpiration: 5.0,
            extraction_top: crop.parameters.rooting.extraction_top,
            extraction_bottom: crop.parameters.rooting.extraction_bottom,
            root_density_correction: 1.0,
            gravel_mode: ExtractionGravelMode::CompartmentLayer,
        },
        &stress,
    )?;
    let final_water = profile_water(&profile, &compartments);

    assert_in_delta!(extraction.actual_transpiration, 5.0, 1.0e-12);
    assert_in_delta!(extraction.unmet_transpiration, 0.0, 1.0e-12);
    assert_in_delta!(
        initial_water,
        final_water + extraction.actual_transpiration,
        1.0e-9
    );
    for compartment in &compartments[..5] {
        assert_eq_f64!(compartment.weighting_factor, 1.0_f64);
    }
    for compartment in &compartments[5..] {
        assert_eq_f64!(compartment.weighting_factor, 0.0_f64);
    }

    Ok(())
}

#[test]
fn gravel_compatibility_mode_exposes_retained_layer_behavior() -> TestResult {
    let profile = two_layer_profile()?;
    let mut corrected = profile.compartments_at_field_capacity();
    let mut strict = profile.compartments_at_field_capacity();
    let initial_water = profile_water(&profile, &corrected);
    let stress = vec![1.0; corrected.len()];
    let corrected_extraction = extract_root_water(
        &profile,
        &mut corrected,
        &extraction_parameters(ExtractionGravelMode::CompartmentLayer),
        &stress,
    )?;
    let strict_extraction = extract_root_water(
        &profile,
        &mut strict,
        &extraction_parameters(ExtractionGravelMode::StrictReference),
        &stress,
    )?;
    let corrected_water = profile_water(&profile, &corrected);
    let strict_water = profile_water(&profile, &strict);

    assert_in_delta!(
        initial_water - corrected_water,
        corrected_extraction.actual_transpiration,
        1.0e-9
    );
    assert_in_delta!(
        corrected_extraction.actual_transpiration,
        strict_extraction.actual_transpiration,
        1.0e-12
    );
    assert!((initial_water - strict_water - strict_extraction.actual_transpiration).abs() > 0.1);

    Ok(())
}

fn extraction_parameters(gravel_mode: ExtractionGravelMode) -> RootExtractionParameters {
    RootExtractionParameters {
        rooting_depth: 0.40,
        potential_transpiration: 4.0,
        extraction_top: 0.02,
        extraction_bottom: 0.01,
        root_density_correction: 1.0,
        gravel_mode,
    }
}

fn two_layer_profile() -> Result<PreparedSoilProfile, aquacrop_core::Error> {
    let layer = |gravel_mass| {
        SoilLayer {
            thickness: 0.20,
            saturation: 46.0,
            field_capacity: 29.0,
            wilting_point: 13.0,
            saturated_hydraulic_conductivity: 120.0,
            penetrability: 100,
            gravel_mass,
            capillary_rise_parameters: Some((-0.40, 1.25)),
        }
        .prepare()
    };
    Ok(PreparedSoilProfile {
        curve_number: 46,
        readily_evaporable_water: 7,
        layers: vec![layer(0)?, layer(50)?],
    })
}
