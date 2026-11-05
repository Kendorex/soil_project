//! Serde contract checks for public simulation inputs and outputs.

#![cfg(feature = "serde")]

use std::assert_matches;

use aquacrop_core::calendar::CalendarDate;
#[cfg(feature = "file-io")]
use aquacrop_core::climate::GrowingDegreeDayMethod;
use aquacrop_core::climate::{CalibrationClimate, DailyWeather, ReferenceTemperatureClimatology};
#[cfg(feature = "file-io")]
use aquacrop_core::crop::CropSeason;
use aquacrop_core::crop::{
    Crop, CropCycle, CropParameters, DevelopmentCalendar, PartialDevelopmentCalendar, SeasonCrop,
    SeasonCropCycle,
};
use aquacrop_core::engine::{
    CompletedSimulation, DailyResult, FinalResult, InitialConditions, SimulationBoundary,
    SimulationCheckpoint, SimulationConfig,
};
use aquacrop_core::irrigation::{DailyIrrigation, IrrigationMode};
use aquacrop_core::output::{
    AggregatedResult, DailyReport, EvaluationPair, EvaluationResult, FieldObservation,
    ObservedValue, OutputAggregation, SeasonalReport, SimulationResult, Statistics,
};
use aquacrop_core::soil::{SALT_CELL_COUNT, SoilCompartmentState};
use assertables::assert_eq_f64;
use serde::Serialize;
use serde::de::DeserializeOwned;

fn assert_serde_contract<T: Serialize + DeserializeOwned>() {}

#[test]
fn public_simulation_contracts_implement_serde() {
    assert_serde_contract::<SimulationBoundary>();
    assert_serde_contract::<SimulationConfig>();
    assert_serde_contract::<Crop>();
    assert_serde_contract::<CropParameters>();
    assert_serde_contract::<CropCycle>();
    assert_serde_contract::<SeasonCrop>();
    assert_serde_contract::<SeasonCropCycle>();
    assert_serde_contract::<InitialConditions>();
    assert_serde_contract::<DailyResult>();
    assert_serde_contract::<FinalResult>();
    assert_serde_contract::<SimulationCheckpoint>();
    assert_serde_contract::<CompletedSimulation>();
    assert_serde_contract::<DailyWeather>();
    assert_serde_contract::<CalibrationClimate>();
    assert_serde_contract::<ReferenceTemperatureClimatology>();
    assert_serde_contract::<DailyIrrigation>();
    assert_serde_contract::<OutputAggregation>();
    assert_serde_contract::<AggregatedResult>();
    assert_serde_contract::<DailyReport>();
    assert_serde_contract::<SeasonalReport>();
    assert_serde_contract::<ObservedValue>();
    assert_serde_contract::<FieldObservation>();
    assert_serde_contract::<EvaluationPair>();
    assert_serde_contract::<Statistics>();
    assert_serde_contract::<EvaluationResult>();
    assert_serde_contract::<SimulationResult>();
}

#[test]
fn simulation_boundary_serializes_day_numbers_as_calendar_dates() {
    let value = serde_json::to_value(SimulationBoundary::Day(1_155))
        .expect("a valid AquaCrop day number should serialize");

    assert_eq!(value, serde_json::json!("1904-02-29"));
}

#[test]
fn simulation_boundary_deserializes_calendar_dates() {
    let boundary: SimulationBoundary = serde_json::from_value(serde_json::json!("1904-02-29"))
        .expect("a valid AquaCrop calendar date should deserialize");

    assert_matches!(boundary, SimulationBoundary::Date(date) if date.day_number() == 1_155);
}

#[test]
fn simulation_boundary_rejects_day_numbers_before_the_calendar_epoch() {
    let result = serde_json::to_value(SimulationBoundary::Day(0));

    assert!(result.is_err());
}

#[cfg(feature = "file-io")]
fn official_crop() -> Result<Crop, aquacrop_core::Error> {
    aquacrop_core::io::parse_crop_file(include_str!(
        "../../../third-party/KUL-RSDA/AquaCrop/testcase/DATA/AlfOttawaGDD.CRO"
    ))
}

#[cfg(feature = "file-io")]
#[test]
fn crop_serialization_flattens_parameters() -> Result<(), Box<dyn std::error::Error>> {
    let value = serde_json::to_value(official_crop()?)?;

    assert!(value.get("parameters").is_none());
    assert!(value.get("kind").is_some());
    let decoded: Crop = serde_json::from_value(value)?;
    assert_eq!(
        decoded.parameters.kind,
        aquacrop_core::crop::CropKind::Forage
    );

    Ok(())
}

#[cfg(feature = "file-io")]
#[test]
fn season_crop_serialization_flattens_parameters() -> Result<(), Box<dyn std::error::Error>> {
    let crop = official_crop()?.for_season(CropSeason::new(
        GrowingDegreeDayMethod::ClampUpperTemperature,
    ))?;
    let value = serde_json::to_value(crop)?;

    assert!(value.get("parameters").is_none());
    assert!(value.get("kind").is_some());
    let decoded: SeasonCrop = serde_json::from_value(value)?;
    assert_eq!(
        decoded.parameters.kind,
        aquacrop_core::crop::CropKind::Forage
    );

    Ok(())
}

#[test]
fn struct_fields_use_camel_case() {
    let value = serde_json::to_value(InitialConditions::default())
        .expect("initial conditions should serialize");

    assert!(value.get("surfaceWater").is_some());
    assert!(value.get("surface_water").is_none());
}

#[test]
fn simple_enum_values_use_snake_case() {
    let value =
        serde_json::to_value(OutputAggregation::TenDaily).expect("aggregation should serialize");

    assert_eq!(value, serde_json::json!("ten_daily"));
}

#[test]
fn data_enum_uses_internal_snake_case_tag_and_camel_case_fields() {
    let value = serde_json::to_value(IrrigationMode::Manual {
        first_day_number: Some(42),
    })
    .expect("irrigation mode should serialize");

    assert_eq!(
        value,
        serde_json::json!({"type": "manual", "firstDayNumber": 42})
    );
}

#[test]
fn calendar_day_crop_cycle_serializes_without_a_gdd_calendar() {
    let cycle = CropCycle::CalendarDays {
        calendar_days: DevelopmentCalendar {
            emergence: 7,
            maximum_rooting: 60,
            senescence: 65,
            maturity: 93,
            flowering: 60,
            flowering_length: 12,
            harvest_index: Some(27),
            canopy_growth_coefficient: 0.1241,
            canopy_decline_coefficient: 0.07697,
        },
    };

    let value = serde_json::to_value(cycle).expect("crop cycle should serialize");

    assert_eq!(
        value,
        serde_json::json!({
            "type": "calendar_days",
            "calendarDays": {
                "emergence": 7,
                "maximumRooting": 60,
                "senescence": 65,
                "maturity": 93,
                "flowering": 60,
                "floweringLength": 12,
                "harvestIndex": 27,
                "canopyGrowthCoefficient": 0.1241,
                "canopyDeclineCoefficient": 0.07697
            }
        })
    );
}

#[test]
fn gdd_crop_cycle_serializes_partial_calendar_days() {
    let cycle = CropCycle::UnresolvedGrowingDegreeDays {
        calendar_days: PartialDevelopmentCalendar {
            emergence: Some(7),
            maximum_rooting: None,
            senescence: None,
            maturity: Some(93),
            flowering: None,
            flowering_length: None,
            harvest_index: None,
            canopy_growth_coefficient: Some(0.1241),
            canopy_decline_coefficient: None,
        },
        gdd: DevelopmentCalendar {
            emergence: 80,
            maximum_rooting: 600,
            senescence: 700,
            maturity: 900,
            flowering: 550,
            flowering_length: 120,
            harvest_index: Some(270),
            canopy_growth_coefficient: 0.01241,
            canopy_decline_coefficient: 0.007_697,
        },
    };

    let value = serde_json::to_value(cycle).expect("crop cycle should serialize");

    assert_eq!(
        value,
        serde_json::json!({
            "type": "unresolved_growing_degree_days",
            "calendarDays": {
                "emergence": 7,
                "maximumRooting": null,
                "senescence": null,
                "maturity": 93,
                "flowering": null,
                "floweringLength": null,
                "harvestIndex": null,
                "canopyGrowthCoefficient": 0.1241,
                "canopyDeclineCoefficient": null
            },
            "gdd": {
                "emergence": 80,
                "maximumRooting": 600,
                "senescence": 700,
                "maturity": 900,
                "flowering": 550,
                "floweringLength": 120,
                "harvestIndex": 270,
                "canopyGrowthCoefficient": 0.01241,
                "canopyDeclineCoefficient": 0.007_697
            }
        })
    );
}

#[test]
fn internally_tagged_data_enum_deserializes() {
    let mode: IrrigationMode = serde_json::from_value(serde_json::json!({"type": "rainfed"}))
        .expect("irrigation mode should deserialize");

    assert_matches!(mode, IrrigationMode::Rainfed);
}

#[test]
fn initial_conditions_round_trip_without_transient_compartment_fields() {
    let initial = InitialConditions {
        compartments: Some(vec![SoilCompartmentState {
            thickness: 0.1,
            theta: 0.29,
            layer_index: 0,
            adjusted_field_capacity: 29.0,
            anaerobic_exposure: 2,
            dissolved_salt: [1.0; SALT_CELL_COUNT],
            deposited_salt: [2.0; SALT_CELL_COUNT],
        }]),
        surface_water: 3.0,
        surface_conductivity: 0.5,
        stored_assimilates: 0.25,
    };

    let json = serde_json::to_string(&initial).expect("initial conditions should serialize");
    let decoded: InitialConditions =
        serde_json::from_str(&json).expect("initial conditions should deserialize");
    let compartment = decoded
        .compartments
        .and_then(|compartments| compartments.into_iter().next())
        .expect("one compartment should round trip");

    assert_eq_f64!(compartment.thickness, 0.1_f64);
    assert_eq_f64!(compartment.theta, 0.29_f64);
    assert_eq!(compartment.anaerobic_exposure, 2);
    assert_eq_f64!(decoded.surface_water, 3.0_f64);
}

#[test]
fn calendar_date_deserialize_valid() {
    let valid: CalendarDate =
        serde_json::from_str(r#""2024-02-29""#).expect("a valid AquaCrop date should deserialize");

    assert_eq!(
        valid,
        CalendarDate::new(2024, 2, 29).expect("the comparison date should be valid")
    );
}

#[test]
fn calendar_date_deserialize_invalid() {
    let invalid = serde_json::from_str::<CalendarDate>(r#""1900-02-29""#);
    assert!(invalid.is_err());
}

#[test]
fn calendar_date_deserialize_invalid_format() {
    let invalid = serde_json::from_str::<CalendarDate>(r#""20240229""#);
    assert!(invalid.is_err());
}
