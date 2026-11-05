//! Reference-climate and fertility preparation for the official crop.

use aquacrop_conformance::{OfficialTestcase, read_prepared_crop};
use aquacrop_core::biomass::{calibrate_fertility_stress, calibrate_salinity_stress};
use aquacrop_core::climate::{ReferenceTemperature, reference_temperature_climatology};
use aquacrop_core::compat::roundc_i32;
use aquacrop_core::io::{
    read_co2_file, read_management_file, read_program_parameters, read_project_file,
    read_temperature_file,
};

#[test]
fn official_crop_prepares_a_finite_fertility_relationship() -> Result<(), aquacrop_core::Error> {
    let project = OfficialTestcase::new();
    let root = project.source_root();
    let project = read_project_file(root.join("LIST/Ottawa.PRM"))?;
    let crop = read_prepared_crop(root.join("DATA/AlfOttawaGDD.CRO"))?;
    let management = read_management_file(root.join("DATA/Ottawa.MAN"))?;
    let parameters = read_program_parameters(root.join("PARAM/Ottawa.PPn"))?;
    let temperatures = read_temperature_file(root.join("DATA/Ottawa.Tnx"))?;
    let co2 = read_co2_file(root.join("SIMUL/MaunaLoa.CO2"))?;
    let run = project
        .runs
        .first()
        .expect("the official project should contain a run");
    let reference = reference_temperature_climatology(
        temperatures.record.first_date.day_number(),
        &temperatures.observations,
    )?;
    let reference_year = roundc_i32(
        f64::from(temperatures.record.first_date.year() + temperatures.record.last_date.year())
            / 2.0,
    );
    let reference_carbon_dioxide = co2
        .series
        .concentration_for_year(reference_year)
        .expect("the reference year should contain CO2 data");
    let calibration = calibrate_fertility_stress(
        &crop,
        &parameters,
        management.fertility_stress,
        run.crop_period.first,
        reference_carbon_dioxide,
        reference.as_ref(),
    )?;
    assert!(calibration.stress_intercept.is_finite());
    assert!(calibration.stress_linear.is_finite());
    assert!(calibration.stress_quadratic.is_finite());
    assert!((0.0..=1.0).contains(&calibration.attainable_biomass_fraction));
    assert!(calibration.seasonal_potential_transpiration > 0.0);
    Ok(())
}

#[test]
fn default_grain_crop_supports_fertility_calibration() -> Result<(), aquacrop_core::Error> {
    let project = OfficialTestcase::new();
    let root = project.source_root();
    let project = read_project_file(root.join("LIST/Ottawa.PRM"))?;
    let crop = read_prepared_crop(root.join("SIMUL/DEFAULT.CRO"))?;
    let management = read_management_file(root.join("DATA/Ottawa.MAN"))?;
    let parameters = read_program_parameters(root.join("PARAM/Ottawa.PPn"))?;
    let temperatures = read_temperature_file(root.join("DATA/Ottawa.Tnx"))?;
    let co2 = read_co2_file(root.join("SIMUL/MaunaLoa.CO2"))?;
    let run = project
        .runs
        .first()
        .expect("the official project should contain a run");
    let reference = reference_temperature_climatology(
        temperatures.record.first_date.day_number(),
        &temperatures.observations,
    )?;
    let reference_year = roundc_i32(
        f64::from(temperatures.record.first_date.year() + temperatures.record.last_date.year())
            / 2.0,
    );
    let reference_carbon_dioxide = co2
        .series
        .concentration_for_year(reference_year)
        .expect("the reference year should contain CO2 data");

    calibrate_fertility_stress(
        &crop,
        &parameters,
        management.fertility_stress,
        run.crop_period.first,
        reference_carbon_dioxide,
        reference.as_ref(),
    )?;
    Ok(())
}

#[test]
fn official_crop_prepares_a_finite_salinity_relationship() -> Result<(), aquacrop_core::Error> {
    let project = OfficialTestcase::new();
    let root = project.source_root();
    let project = read_project_file(root.join("LIST/Ottawa.PRM"))?;
    let crop = read_prepared_crop(root.join("DATA/AlfOttawaGDD.CRO"))?;
    let parameters = read_program_parameters(root.join("PARAM/Ottawa.PPn"))?;
    let temperatures = read_temperature_file(root.join("DATA/Ottawa.Tnx"))?;
    let co2 = read_co2_file(root.join("SIMUL/MaunaLoa.CO2"))?;
    let run = project
        .runs
        .first()
        .expect("the official project should contain a run");
    let reference = reference_temperature_climatology(
        temperatures.record.first_date.day_number(),
        &temperatures.observations,
    )?;
    let actual: Vec<_> = temperatures
        .observations
        .iter()
        .map(|temperature| ReferenceTemperature {
            minimum: temperature.0,
            maximum: temperature.1,
        })
        .collect();
    let reference_year = roundc_i32(
        f64::from(temperatures.record.first_date.year() + temperatures.record.last_date.year())
            / 2.0,
    );
    let reference_carbon_dioxide = co2
        .series
        .concentration_for_year(reference_year)
        .expect("the reference year should contain CO2 data");

    let calibration = calibrate_salinity_stress(
        &crop,
        &parameters,
        run.crop_period.first,
        temperatures.record.first_date.day_number(),
        &actual,
        reference_carbon_dioxide,
        reference.as_ref(),
    )?;
    assert!(calibration.reduction_intercept.is_finite());
    assert!(calibration.reduction_linear.is_finite());
    assert!(calibration.reduction_quadratic.is_finite());
    Ok(())
}
