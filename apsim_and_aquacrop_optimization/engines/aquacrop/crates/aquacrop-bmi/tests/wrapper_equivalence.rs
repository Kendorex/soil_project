//! Compares the direct project runner with the BMI wrapper for the same scenario.
//!
//! Both paths must produce the same daily results within floating-point tolerance.

use std::fs;

use aquacrop_bmi::BmiModel;
use aquacrop_conformance::OfficialTestcase;
use aquacrop_core::engine::InitialConditions;
use aquacrop_core::io::load_project_run;

fn read_fixture(relative: &str) -> Result<String, Box<dyn std::error::Error>> {
    Ok(fs::read_to_string(
        OfficialTestcase::new().source_root().join(relative),
    )?)
}

fn build_scenario_json() -> Result<String, Box<dyn std::error::Error>> {
    let scenario = serde_json::json!({
        "season_year": 1,
        "crop_text": read_fixture("DATA/AlfOttawaGDD.CRO")?,
        "soil_text": read_fixture("DATA/Ottawa.SOL")?,
        "management_text": read_fixture("DATA/Ottawa.MAN")?,
        "temperature_text": read_fixture("DATA/Ottawa.Tnx")?,
        "rain_text": read_fixture("DATA/Ottawa.PLU")?,
        "eto_text": read_fixture("DATA/Ottawa.ETo")?,
        "co2_text": read_fixture("SIMUL/MaunaLoa.CO2")?,
        "simulation_start": {"year": 2014, "month": 5, "day": 21},
        "simulation_end": {"year": 2014, "month": 10, "day": 31},
        "crop_start": {"year": 2014, "month": 5, "day": 21},
        "crop_end": {"year": 2014, "month": 10, "day": 31},
    });
    Ok(scenario.to_string())
}

#[test]
fn bmi_wrapper_matches_standalone_engine_day_by_day() -> Result<(), Box<dyn std::error::Error>> {
    let mut standalone = load_project_run(
        OfficialTestcase::new().source_root(),
        "LIST/Ottawa.PRM",
        0,
        InitialConditions::at_field_capacity(),
    )?;

    let mut wrapper = BmiModel::new();
    wrapper.initialize(&build_scenario_json()?)?;

    let mut compared_days = 0;
    loop {
        let standalone_day = standalone.step()?;
        wrapper.update()?;

        let Some(standalone_day) = standalone_day else {
            break;
        };
        compared_days += 1;

        macro_rules! assert_scalar_matches {
            ($name:expr, $expected:expr) => {{
                let mut actual = [0.0_f64];
                wrapper.get_value($name, &mut actual)?;
                let expected: f64 = $expected;
                assert!(
                    (actual[0] - expected).abs() <= 1.0e-9,
                    "day {}: {} differs: standalone {expected}, bmi {}",
                    standalone_day.day_number,
                    $name,
                    actual[0],
                );
            }};
        }

        assert_scalar_matches!("air_precipitation", standalone_day.rain);
        assert_scalar_matches!(
            "air_temperature_minimum~day",
            standalone_day.minimum_temperature
        );
        assert_scalar_matches!(
            "air_temperature_maximal~day",
            standalone_day.maximum_temperature
        );
        assert_scalar_matches!(
            "air_evapotranspiration~reference",
            standalone_day.reference_et
        );
        assert_scalar_matches!(
            "atmosphere_co2-concentration",
            standalone_day.carbon_dioxide
        );
        assert_scalar_matches!(
            "plant_cover~projective",
            standalone_day.canopy_cover * 100.0
        );
        assert_scalar_matches!("plant_biomass~above-ground", standalone_day.biomass);
        assert_scalar_matches!("plant_yield~standard", standalone_day.dry_yield);
        assert_scalar_matches!("plant_root_depth", standalone_day.rooting_depth);
        assert_scalar_matches!("soil_water_drainage~deep", standalone_day.drainage);
        assert_scalar_matches!("water_runoff~surface", standalone_day.runoff);
        assert_scalar_matches!("soil_infiltration~rate", standalone_day.infiltration);
        assert_scalar_matches!("air_evaporation", standalone_day.evaporation);
        assert_scalar_matches!("air_transpiration~actual", standalone_day.transpiration);
        assert_scalar_matches!("soil_water_capillary", standalone_day.capillary_rise);
        assert_scalar_matches!(
            "plant_biomass_potential",
            standalone_day.unrestricted_biomass
        );
        assert_scalar_matches!(
            "plant_stress_aeration",
            f64::from(standalone_day.root_zone_anaerobic_days)
        );

        assert_eq!(
            wrapper.grid_size("soil__water_content_in_compartments")?,
            standalone_day.compartments.len(),
            "day {}: compartment count differs",
            standalone_day.day_number
        );
        let mut theta = vec![0.0; standalone_day.compartments.len()];
        wrapper.get_value("soil__water_content_in_compartments", &mut theta)?;
        for (index, (actual, expected)) in theta
            .iter()
            .zip(standalone_day.compartments.iter().map(|c| c.theta))
            .enumerate()
        {
            assert!(
                (actual - expected).abs() <= 1.0e-9,
                "day {} compartment {index} theta differs: standalone {expected}, bmi {actual}",
                standalone_day.day_number,
            );
        }
    }

    assert!(
        compared_days > 100,
        "expected a multi-month comparison, got {compared_days} days"
    );
    Ok(())
}
