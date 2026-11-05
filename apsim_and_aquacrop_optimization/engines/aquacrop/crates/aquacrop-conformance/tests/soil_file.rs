//! Conformance checks for soil-profile parsing and derived layer parameters.

use aquacrop_conformance::{OfficialTestcase, TestResult};
use aquacrop_core::io::parse_soil_profile;
use aquacrop_core::soil::{REFERENCE_SOIL_COMPARTMENT_COUNT, SoilClass, SoilProfile};
use assertables::{assert_eq_f64, assert_in_delta};

#[test]
fn official_soil_file_preserves_profile_and_layer_inputs() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;

    assert_eq!(profile.curve_number, 46);
    assert_eq!(profile.readily_evaporable_water, 7);
    assert_eq!(profile.layers.len(), 1);

    let layer = profile.layers.first().expect("the offical soil has layers");
    assert_eq_f64!(layer.thickness, 1.5_f64);
    assert_eq_f64!(layer.saturation, 46.0_f64);
    assert_eq_f64!(layer.field_capacity, 29.0_f64);
    assert_eq_f64!(layer.wilting_point, 13.0_f64);
    assert_eq_f64!(layer.saturated_hydraulic_conductivity, 1_200.0_f64);

    Ok(())
}

#[test]
fn official_soil_file_derives_hydraulic_parameters() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let layer = profile.layers.first().expect("the offical soil has layers");

    assert_eq!(layer.class, SoilClass::Loamy);
    assert_eq_f64!(layer.drainage_coefficient, 1.0_f64);
    assert_eq_f64!(layer.gravel_volume, 0.0_f64);

    Ok(())
}

#[test]
fn soil_profile_preparation_rejects_an_empty_profile() {
    let config = SoilProfile {
        curve_number: 46,
        readily_evaporable_water: 7,
        layers: Vec::new(),
    };

    assert!(config.prepare().is_err());
}

#[test]
fn official_soil_file_derives_salinity_cells_and_mobility() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mix: f64 = 20.0 / 100.0;
    let a = mix * 2.0;
    let b = (10.0 * (0.5 - mix) * 10.0_f64.ln()).exp();
    let y = (a.ln() + b.ln()).exp();
    let expected_first_mobility = (y - a) / (a * b - a);

    let layer = profile.layers.first().expect("the offical soil has layers");
    assert_eq!(layer.salinity_cell_count, 1);
    assert_eq!(layer.salinity_cell_count_with_saturation, 2);
    assert_eq_f64!(layer.salt_mobility[0], expected_first_mobility);
    assert_eq_f64!(layer.salt_mobility[1], 1.0_f64);

    Ok(())
}

#[test]
fn official_soil_profile_builds_reference_compartment_limit() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let compartments = profile.reference_compartments_at_field_capacity_for_depth(3.0);

    assert_eq!(compartments.len(), REFERENCE_SOIL_COMPARTMENT_COUNT);
    for compartment in &compartments {
        assert_eq_f64!(compartment.theta, 0.29_f64);
    }

    let expected_thicknesses = [
        0.10, 0.15, 0.15, 0.20, 0.20, 0.25, 0.25, 0.30, 0.30, 0.35, 0.35, 0.45,
    ];
    for (compartment, expected) in compartments.iter().zip(expected_thicknesses) {
        assert_in_delta!(compartment.thickness, expected, f64::EPSILON);
    }

    Ok(())
}

#[test]
fn extended_soil_profile_builds_more_than_twelve_compartments() -> TestResult {
    let compartments = OfficialTestcase::new()
        .prepared_soil_profile()?
        .compartments_at_field_capacity_for_depth(3.0);

    assert_eq!(compartments.len(), 30);

    for compartment in compartments {
        assert_eq_f64!(compartment.thickness, 0.1_f64);
    }

    Ok(())
}

#[test]
fn soil_file_accepts_more_than_five_layers() {
    let layer = "0.25 46 29 13 1200 100 0 -0.4 -1.2 sandy";
    let input = format!(
        "Extended soil\n7.3\n46\n7\n6\n-9\nheader\nseparator\n{}\n",
        [layer; 6].join("\n")
    );
    let profile = parse_soil_profile(&input).expect("the extended soil fixture should parse");

    assert_eq!(profile.layers.len(), 6);
}

#[test]
fn version_3_1_soil_derives_missing_capillary_parameters() -> TestResult {
    let input = "Legacy soil\n3.1\n46\n7\n1\n-9\nheader\nseparator\n1.5 46 29 13 1200 --- sandy\n";
    let profile = parse_soil_profile(input)?.prepare()?;
    let layer = profile.layers.first().expect("the legacy soil has layers");

    assert_eq!(layer.penetrability, 100);
    assert_eq!(layer.gravel_mass, 0);

    let (a, b) = layer
        .capillary_rise_parameters
        .expect("the legacy soil layer derives capillary rise parameters");
    assert_eq_f64!(a, (-0.498_6 + 9.0 * 1_200.0 / 100_000.0_f64));
    assert_eq_f64!(b, (-2.132_0 + 0.477_8 * 1_200.0_f64.ln()));

    Ok(())
}
