//! Conformance checks for field-management and irrigation inputs.

use std::assert_matches;

use aquacrop_conformance::OfficialTestcase;
use aquacrop_core::io::{parse_irrigation_file, parse_management_file, read_management_file};
use aquacrop_core::irrigation::{
    IrrigationDepthCriterion, IrrigationMode, IrrigationRecord, IrrigationTimeCriterion,
};
use aquacrop_core::management::CuttingCriterion;
use assertables::assert_eq_f64;

#[test]
fn official_management_file_preserves_field_practices() {
    let path = OfficialTestcase::new()
        .source_root()
        .join("DATA/Ottawa.MAN");
    let management = read_management_file(path).expect("the official management file should parse");

    assert_eq!(management.mulch_cover, 0);
    assert_eq!(management.mulch_evaporation_reduction, 50);
    assert_eq!(management.fertility_stress, 50);
    assert_eq_f64!(management.bund_height, 0.0_f64);
    assert!(management.runoff_enabled);
    assert_eq!(management.curve_number_correction, 0);
    assert_eq!(management.weed_cover, 0);
    assert_eq!(management.weed_self_thinning_replacement, 100);
}

#[test]
fn official_management_file_preserves_fixed_cutting_days() {
    let path = OfficialTestcase::new()
        .source_root()
        .join("DATA/Ottawa.MAN");
    let management = read_management_file(path).expect("the official management file should parse");

    assert!(management.cuttings.considered);
    assert!(!management.cuttings.generated);
    assert_eq!(
        management.cuttings.criterion,
        CuttingCriterion::NotApplicable
    );
    assert_eq!(management.cuttings.first_list_day_number, Some(41_274));
    assert_eq!(management.cuttings.records.len(), 8);

    let first = management
        .cuttings
        .records
        .first()
        .expect("the official cutting schedule should contain a first record");
    assert_eq!(first.from_day, 194);
    assert!(first.target.is_none());

    let last = management
        .cuttings
        .records
        .last()
        .expect("the official cutting schedule should contain a last record");
    assert_eq!(last.from_day, 972);
    assert!(last.target.is_none());
}

#[test]
fn pre_5_management_uses_reference_compatibility_defaults() {
    let input = "Legacy management\n4.0\n10\n40\n20\n0.0\n0\n";
    let management =
        parse_management_file(input).expect("the legacy management fixture should parse");

    assert_eq!(management.curve_number_correction, 0);
    assert_eq!(management.weed_cover, 0);
    assert_eq!(management.weed_cover_delta, 0);
    assert_eq_f64!(management.weed_canopy_shape, -0.01_f64);
    assert_eq!(management.weed_self_thinning_replacement, 100);
    assert!(!management.cuttings.considered);
    assert_eq!(management.cuttings.canopy_cover_after_cutting, 30);
}

#[test]
fn version_7_3_generated_irrigation_reads_end_exclusion() {
    let input = "Generated schedule\n7.3\n1\n100\n2\n3\n1\n10\n\nRules\n1 50 -9 0.5\n";
    let schedule =
        parse_irrigation_file(input).expect("the generated irrigation fixture should parse");

    match &schedule.mode {
        IrrigationMode::Generated {
            time_criterion,
            depth_criterion,
            end_exclusion,
        } => {
            assert_matches!(
                time_criterion,
                IrrigationTimeCriterion::ReadilyAvailableWaterDepletion
            );
            assert_matches!(depth_criterion, IrrigationDepthCriterion::ToFieldCapacity);
            assert_eq!(*end_exclusion, Some(10));
        }
        mode => assert!(
            matches!(mode, IrrigationMode::Generated { .. }),
            "expected generated irrigation mode, found {mode:?}"
        ),
    }

    let record = schedule
        .records
        .first()
        .expect("the generated irrigation schedule should contain a record");
    match record {
        IrrigationRecord::Generated {
            from_day,
            time_parameter,
            depth_parameter,
            electrical_conductivity,
        } => {
            assert_eq!(*from_day, 1);
            assert_eq!(*time_parameter, 50);
            assert_eq!(*depth_parameter, None);
            assert_eq_f64!(
                electrical_conductivity
                    .expect("the generated irrigation record should contain conductivity"),
                0.5_f64
            );
        }
        record @ IrrigationRecord::Manual { .. } => assert!(
            matches!(record, IrrigationRecord::Generated { .. }),
            "expected generated irrigation record, found {record:?}"
        ),
    }
}

#[test]
fn generated_irrigation_stops_for_configured_last_days() {
    let input = "Generated schedule\n7.3\n1\n100\n2\n3\n1\n10\n";
    let schedule =
        parse_irrigation_file(input).expect("the generated irrigation fixture should parse");
    let result = [
        schedule.mode.generated_irrigation_allowed(90, 100),
        schedule.mode.generated_irrigation_allowed(91, 100),
    ];

    assert_eq!(result, [true, false]);
}

#[test]
fn pre_7_3_generated_irrigation_has_no_end_exclusion() {
    let input = "Generated schedule\n7.2\n1\n100\n2\n3\n1\n1 50 -9 0.5\n";
    let schedule =
        parse_irrigation_file(input).expect("the legacy irrigation fixture should parse");

    assert_matches!(
        schedule.mode,
        IrrigationMode::Generated {
            end_exclusion: None,
            ..
        }
    );
}

#[test]
fn manual_irrigation_rounds_schedule_values_like_fortran() {
    let input = "Manual schedule\n7.3\n1\n100\n1\n41414\n\nDay depth ECw\n2.5 30.5 0.8\n";
    let schedule =
        parse_irrigation_file(input).expect("the manual irrigation fixture should parse");

    let record = schedule
        .records
        .first()
        .expect("the manual irrigation schedule should contain a record");
    match record {
        IrrigationRecord::Manual {
            day,
            depth,
            electrical_conductivity,
        } => {
            assert_eq!(*day, 2);
            assert_eq!(*depth, 30);
            assert_eq_f64!(
                electrical_conductivity
                    .expect("the manual irrigation record should contain conductivity"),
                0.8_f64
            );
        }
        record @ IrrigationRecord::Generated { .. } => assert!(
            matches!(record, IrrigationRecord::Manual { .. }),
            "expected manual irrigation record, found {record:?}"
        ),
    }
}
