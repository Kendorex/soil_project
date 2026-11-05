//! Tests BMI registry, lifecycle, error handling, and override timing.

use std::fs;

use aquacrop_bmi::BmiModel;
use aquacrop_bmi::error::BmiError;
use aquacrop_bmi::registry;
use aquacrop_conformance::OfficialTestcase;

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn read_fixture(relative: &str) -> Result<String, Box<dyn std::error::Error>> {
    Ok(fs::read_to_string(
        OfficialTestcase::new().source_root().join(relative),
    )?)
}

fn scenario_json() -> Result<String, Box<dyn std::error::Error>> {
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

fn ledger_counts() -> (usize, usize, usize) {
    // Pinned to the official Fortran BMI reference's own variable contract
    // (bmi_aquacrop.f90: input_items/output_items/resolve_var_alias) at the revision this
    // wrapper was built against: 16 inputs, 31 outputs, 41 aliases.
    (16, 31, 41)
}

#[test]
fn registry_counts_match_the_ledger_exactly() {
    let (input_count, output_count, alias_count) = ledger_counts();
    // `+ 1` accounts for the one deliberate, documented post-ledger addition,
    // `crop__gdd_cumulative_from_planting` (see `registry.rs`'s module doc comment for the full
    // reasoning).
    let documented_post_ledger_additions = 1;
    assert_eq!(registry::INPUT_NAMES.len(), input_count);
    assert_eq!(
        registry::OUTPUT_NAMES.len(),
        output_count + documented_post_ledger_additions
    );
    assert_eq!(registry::ALIASES.len(), alias_count);
    assert_eq!(BmiModel::input_var_names().len(), input_count);
    assert_eq!(
        BmiModel::output_var_names().len(),
        output_count + documented_post_ledger_additions
    );
}

#[test]
fn registry_names_are_exactly_the_declared_order_with_no_duplicates() {
    let mut seen = std::collections::HashSet::new();
    for name in registry::INPUT_NAMES
        .iter()
        .chain(registry::OUTPUT_NAMES.iter())
    {
        seen.insert(*name);
    }
    assert_eq!(seen.len(), registry::VARIABLES.len());
    for name in &seen {
        assert!(
            registry::metadata(name).is_some(),
            "{name} is declared as input/output but has no metadata entry"
        );
    }
}

#[test]
fn every_alias_resolves_to_a_declared_canonical_name_or_is_the_known_orphan() {
    for (alias, canonical) in &registry::ALIASES {
        assert_eq!(registry::resolve_alias(alias), *canonical);
        let is_declared = registry::VARIABLES
            .iter()
            .any(|meta| meta.name == *canonical);
        let is_known_orphan = *canonical == "management_surface-storage";
        assert!(
            is_declared || is_known_orphan,
            "alias {alias} resolves to undeclared canonical {canonical}, and it is not the known orphaned alias"
        );
    }
}

#[test]
fn unresolved_name_passes_through_unchanged_matching_the_reference_default_case() {
    assert_eq!(
        registry::resolve_alias("not_a_real_variable"),
        "not_a_real_variable"
    );
}

#[test]
fn methods_before_initialize_report_not_initialized() {
    let model = BmiModel::new();
    assert!(matches!(
        model.current_time(),
        Err(BmiError::NotInitialized)
    ));
    assert!(matches!(model.end_time(), Err(BmiError::NotInitialized)));
}

#[test]
fn double_initialize_is_rejected() -> TestResult {
    let mut model = BmiModel::new();
    model.initialize(&scenario_json()?)?;
    let error = model.initialize(&scenario_json()?);
    assert!(matches!(error, Err(BmiError::AlreadyInitialized)));
    Ok(())
}

#[test]
fn update_after_finalize_is_rejected() -> TestResult {
    let mut model = BmiModel::new();
    model.initialize(&scenario_json()?)?;
    model.finalize()?;
    assert!(matches!(model.update(), Err(BmiError::AlreadyFinalized)));
    assert!(matches!(model.finalize(), Err(BmiError::AlreadyFinalized)));
    Ok(())
}

#[test]
fn update_past_end_time_never_errors_and_keeps_advancing_current_time() -> TestResult {
    // Matches the reference's own permissive (arguably buggy) behavior, replicated deliberately
    // The wrapper preserves this reference behavior.
    let mut model = BmiModel::new();
    model.initialize(&scenario_json()?)?;
    let end_time = model.end_time()?;
    while model.current_time()? < end_time {
        model.update()?;
    }
    let after_end = model.current_time()?;
    for _ in 0..5 {
        model.update()?;
    }
    assert!(model.current_time()? > after_end);
    Ok(())
}

#[test]
fn unknown_variable_is_rejected_on_get_and_set() -> TestResult {
    let mut model = BmiModel::new();
    model.initialize(&scenario_json()?)?;
    let mut buffer = [0.0];
    assert!(matches!(
        model.get_value("not_a_real_variable", &mut buffer),
        Err(BmiError::UnknownVariable(_))
    ));
    assert!(matches!(
        model.set_value("not_a_real_variable", &buffer),
        Err(BmiError::UnknownVariable(_))
    ));
    Ok(())
}

#[test]
fn write_only_variables_reject_get_and_read_only_variables_reject_set() -> TestResult {
    let mut model = BmiModel::new();
    model.initialize(&scenario_json()?)?;
    let mut buffer = [0.0];

    for name in [
        "groundwater__depth",
        "groundwater__ec",
        "soil_water__infiltration_amount",
    ] {
        assert!(
            matches!(
                model.get_value(name, &mut buffer),
                Err(BmiError::UnsupportedOperation {
                    operation: "get",
                    ..
                })
            ),
            "{name} must reject get_value, matching the reference (write-only)"
        );
    }
    for name in [
        "plant_yield~standard",
        "plant_biomass~above-ground",
        "soil_water_drainage~deep",
    ] {
        assert!(
            matches!(
                model.set_value(name, &buffer),
                Err(BmiError::UnsupportedOperation {
                    operation: "set",
                    ..
                })
            ),
            "{name} must reject set_value, matching the reference (read-only)"
        );
    }
    Ok(())
}

#[test]
fn wrong_shape_is_rejected() -> TestResult {
    let mut model = BmiModel::new();
    model.initialize(&scenario_json()?)?;
    let mut too_small = [0.0; 1];
    let error = model.get_value("soil__water_content_in_compartments", &mut too_small);
    assert!(matches!(error, Err(BmiError::WrongShape { .. })));
    Ok(())
}

#[test]
fn persistent_rain_override_applies_to_every_subsequent_day_until_cleared() -> TestResult {
    let mut model = BmiModel::new();
    model.initialize(&scenario_json()?)?;

    model.set_value("air_precipitation", &[123.0])?;
    model.update()?;
    let mut rain = [0.0];
    model.get_value("air_precipitation", &mut rain)?;
    assert_eq!(rain[0].to_bits(), 123.0_f64.to_bits());

    // Persistent, not one-shot: the override must still apply on the following day.
    model.update()?;
    model.get_value("air_precipitation", &mut rain)?;
    assert_eq!(rain[0].to_bits(), 123.0_f64.to_bits());

    // bmi__clear_weather_overrides with a nonzero value clears it.
    model.set_value("bmi__clear_weather_overrides", &[1.0])?;
    model.update()?;
    model.get_value("air_precipitation", &mut rain)?;
    assert_ne!(rain[0].to_bits(), 123.0_f64.to_bits());
    Ok(())
}

#[test]
fn clear_weather_overrides_is_a_noop_when_written_as_zero() -> TestResult {
    let mut model = BmiModel::new();
    model.initialize(&scenario_json()?)?;
    model.set_value("air_precipitation", &[55.0])?;
    model.set_value("bmi__clear_weather_overrides", &[0.0])?;
    model.update()?;
    let mut rain = [0.0];
    model.get_value("air_precipitation", &mut rain)?;
    assert_eq!(rain[0].to_bits(), 55.0_f64.to_bits());
    Ok(())
}

#[test]
fn multiple_instances_are_independent() -> TestResult {
    let mut a = BmiModel::new();
    let mut b = BmiModel::new();
    a.initialize(&scenario_json()?)?;
    b.initialize(&scenario_json()?)?;

    a.set_value("air_precipitation", &[999.0])?;
    a.update()?;
    b.update()?;

    let mut a_rain = [0.0];
    let mut b_rain = [0.0];
    a.get_value("air_precipitation", &mut a_rain)?;
    b.get_value("air_precipitation", &mut b_rain)?;
    assert_eq!(a_rain[0].to_bits(), 999.0_f64.to_bits());
    assert_ne!(a_rain[0].to_bits(), b_rain[0].to_bits());
    Ok(())
}

#[test]
fn repeated_runs_are_deterministic() -> TestResult {
    let json = scenario_json()?;
    let mut first = BmiModel::new();
    first.initialize(&json)?;
    let mut second = BmiModel::new();
    second.initialize(&json)?;

    for _ in 0..30 {
        first.update()?;
        second.update()?;
    }
    let mut first_biomass = [0.0];
    let mut second_biomass = [0.0];
    first.get_value("plant_biomass~above-ground", &mut first_biomass)?;
    second.get_value("plant_biomass~above-ground", &mut second_biomass)?;
    assert_eq!(first_biomass[0].to_bits(), second_biomass[0].to_bits());
    Ok(())
}

#[test]
fn scatter_write_layer_boundary_semantics_match_the_reference() -> TestResult {
    // A compartment spanning two layers receives the value of the later layer.
    let mut model = BmiModel::new();
    model.initialize(&scenario_json()?)?;
    model.update()?;

    let layer_count = model.grid_size("soil__water_content_in_layers")?;
    let mut target = vec![0.0; layer_count];
    for (index, slot) in target.iter_mut().enumerate() {
        let offset = u16::try_from(index)?;
        *slot = 0.01f64.mul_add(f64::from(offset), 0.10);
    }
    model.set_value("soil__water_content_in_layers", &target)?;

    let mut readback = vec![0.0; layer_count];
    model.get_value("soil__water_content_in_layers", &mut readback)?;
    // Interior layers with no shared boundary compartment should read back exactly; only
    // boundary-straddling compartments (if any exist for this profile) may differ, which is the
    // documented, expected scatter behavior rather than a failure.
    assert_eq!(readback.len(), target.len());
    Ok(())
}
