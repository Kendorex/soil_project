//! Full daily-engine comparison with the official AquaCrop output.

use std::fs;

use aquacrop_conformance::{OfficialTestcase, TestResult};
use aquacrop_core::engine::{DailyResult, InitialConditions, Simulation};
use aquacrop_core::io::{load_project_run, run_project, run_project_with_outputs};
use aquacrop_core::output::{OutputAggregation, ResultAggregator};

#[test]
fn daily_engine_accepts_more_than_twelve_compartments() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let compartments = profile.compartments_at_field_capacity_for_depth(1.5);
    let mut simulation = build_run_simulation(
        0,
        InitialConditions {
            compartments: Some(compartments.into_iter().map(Into::into).collect()),
            ..InitialConditions::at_field_capacity()
        },
    )?;

    simulation
        .step()?
        .expect("the extended-compartment simulation should produce a daily result");

    assert_eq!(simulation.finalize().checkpoint.compartments.len(), 15);
    Ok(())
}

#[test]
fn official_ottawa_first_run_matches_daily_core_results() -> TestResult {
    let mut simulation = build_run_simulation(0, InitialConditions::at_field_capacity())?;
    let references = official_run_daily_results(1)?;

    for reference in &references {
        let actual = simulation
            .step()?
            .expect("the first official run should not end before its reference output");
        assert_daily_result(&actual, reference);
    }
    assert!(simulation.step()?.is_none());
    let final_result = simulation.finalize().result;
    assert_close_value(
        "seasonal stored assimilates",
        final_result.stored_assimilates,
        official_run_stored_assimilates(1)?,
        0.000_500_001,
    );
    Ok(())
}

#[test]
fn official_ottawa_second_run_reuses_linked_soil_and_assimilates() -> TestResult {
    let mut first = build_run_simulation(0, InitialConditions::at_field_capacity())?;
    while first.step()?.is_some() {}
    let first = first.finalize().checkpoint;
    let mut second = build_run_simulation(
        1,
        InitialConditions {
            compartments: Some(first.compartments),
            surface_water: first.surface_water,
            surface_conductivity: first.surface_conductivity,
            stored_assimilates: first.stored_assimilates,
        },
    )?;
    let references = official_run_daily_results(2)?;
    for reference in &references {
        let actual = second
            .step()?
            .expect("the second official run should not end before its reference output");
        assert_daily_result(&actual, reference);
    }
    assert!(second.step()?.is_none());
    let final_result = second.finalize().result;
    assert_close_value(
        "second-season stored assimilates",
        final_result.stored_assimilates,
        official_run_stored_assimilates(2)?,
        0.000_500_001,
    );
    Ok(())
}

#[test]
fn official_ottawa_third_run_reuses_second_run_soil_and_assimilates() -> TestResult {
    let mut first = build_run_simulation(0, InitialConditions::at_field_capacity())?;
    while first.step()?.is_some() {}
    let first = first.finalize().checkpoint;

    let mut second = build_run_simulation(
        1,
        InitialConditions {
            compartments: Some(first.compartments),
            surface_water: first.surface_water,
            surface_conductivity: first.surface_conductivity,
            stored_assimilates: first.stored_assimilates,
        },
    )?;
    while second.step()?.is_some() {}
    let second = second.finalize().checkpoint;

    let mut third = build_run_simulation(
        2,
        InitialConditions {
            compartments: Some(second.compartments),
            surface_water: second.surface_water,
            surface_conductivity: second.surface_conductivity,
            stored_assimilates: second.stored_assimilates,
        },
    )?;
    let references = official_run_daily_results(3)?;
    for reference in &references {
        let actual = third
            .step()?
            .expect("the third official run should not end before its reference output");
        assert_daily_result(&actual, reference);
    }
    assert!(third.step()?.is_none());
    let final_result = third.finalize().result;
    assert_close_value(
        "third-season stored assimilates",
        final_result.stored_assimilates,
        official_run_stored_assimilates(3)?,
        0.000_500_001,
    );
    Ok(())
}

#[test]
#[expect(
    clippy::too_many_lines,
    reason = "the conformance check keeps each independently reported period flux explicit"
)]
fn official_ottawa_first_run_aggregates_at_ten_day_boundaries() -> TestResult {
    let mut simulation = build_run_simulation(0, InitialConditions::at_field_capacity())?;
    let references = official_run_daily_results(1)?;
    let mut aggregator = ResultAggregator::new(OutputAggregation::TenDaily);
    let mut expected_start = references
        .first()
        .expect("the first official run should contain daily results")
        .day_number;
    let mut previous_biomass = 0.0;
    let mut expected_rain = 0.0;
    let mut expected_irrigation = 0.0;
    let mut expected_infiltration = 0.0;
    let mut expected_runoff = 0.0;
    let mut expected_drainage = 0.0;
    let mut expected_evaporation = 0.0;
    let mut expected_transpiration = 0.0;
    let mut period_day_count = 0_u32;
    let mut period_count = 0_u32;

    for reference in &references {
        let daily = simulation
            .step()?
            .expect("the first official run should not end before its reference output");
        expected_rain += reference.rain;
        expected_irrigation += reference.irrigation;
        expected_infiltration += reference.infiltration;
        expected_runoff += reference.runoff;
        expected_drainage += reference.drainage;
        expected_evaporation += reference.evaporation;
        expected_transpiration += reference.transpiration;
        period_day_count += 1;

        if let Some(period) = aggregator
            .push(&daily)
            .expect("the official daily result should aggregate")
        {
            let tolerance = f64::from(period_day_count) * 0.050_000_001;
            assert_eq!(period.start.day_number(), expected_start);
            assert_eq!(period.end.day_number(), reference.day_number);
            assert_close_value("aggregated rain", period.rain, expected_rain, tolerance);
            assert_close_value(
                "aggregated irrigation",
                period.irrigation,
                expected_irrigation,
                tolerance,
            );
            assert_close_value(
                "aggregated infiltration",
                period.infiltration,
                expected_infiltration,
                tolerance,
            );
            assert_close_value(
                "aggregated runoff",
                period.runoff,
                expected_runoff,
                tolerance,
            );
            assert_close_value(
                "aggregated drainage",
                period.drainage,
                expected_drainage,
                tolerance,
            );
            assert_close_value(
                "aggregated evaporation",
                period.evaporation,
                expected_evaporation,
                tolerance,
            );
            assert_close_value(
                "aggregated transpiration",
                period.transpiration,
                expected_transpiration,
                tolerance,
            );
            assert_close_value(
                "aggregated biomass",
                period.biomass,
                reference.biomass - previous_biomass,
                0.001_000_001,
            );

            period_count += 1;
            expected_start = reference.day_number + 1;
            previous_biomass = reference.biomass;
            expected_rain = 0.0;
            expected_irrigation = 0.0;
            expected_infiltration = 0.0;
            expected_runoff = 0.0;
            expected_drainage = 0.0;
            expected_evaporation = 0.0;
            expected_transpiration = 0.0;
            period_day_count = 0;
        }
    }

    if let Some(period) = aggregator.finish() {
        let reference = references
            .last()
            .expect("the first official run should have a final daily result");
        let tolerance = f64::from(period_day_count) * 0.050_000_001;
        assert_eq!(period.start.day_number(), expected_start);
        assert_eq!(period.end.day_number(), reference.day_number);
        assert_close_value(
            "final aggregated rain",
            period.rain,
            expected_rain,
            tolerance,
        );
        assert_close_value(
            "final aggregated biomass",
            period.biomass,
            reference.biomass - previous_biomass,
            0.001_000_001,
        );
        period_count += 1;
    }

    assert!(period_count > 1);
    Ok(())
}

#[test]
fn official_project_runner_executes_all_linked_runs() -> TestResult {
    let result = run_project(OfficialTestcase::new().source_root(), "LIST/Ottawa.PRM")?;

    assert_eq!(result.runs.len(), 3);
    for (index, run) in result.runs.iter().enumerate() {
        let run_number = i32::try_from(index)? + 1;
        let references = official_run_daily_results(run_number)?;
        assert_eq!(run.daily.len(), references.len());
        assert_close_value(
            "linked-run final biomass",
            run.final_result.biomass,
            references
                .last()
                .expect("the official linked run should contain daily results")
                .biomass,
            0.000_500_001,
        );
        assert_close_value(
            "linked-run stored assimilates",
            run.final_result.stored_assimilates,
            official_run_stored_assimilates(run_number)?,
            0.000_500_001,
        );
    }
    Ok(())
}

#[test]
fn official_project_runner_preserves_cutting_records() -> TestResult {
    let project = OfficialTestcase::new();
    let result = run_project(project.source_root(), "LIST/Ottawa.PRM")?;

    for (run_index, run) in result.runs.iter().enumerate() {
        let references = official_cutting_results(i32::try_from(run_index)? + 1)?;
        let actual: Vec<_> = run
            .daily
            .iter()
            .filter_map(|daily| daily.cutting.as_ref().map(|cutting| (daily, cutting)))
            .collect();
        assert_eq!(actual.len(), references.len());
        for ((daily, cutting), reference) in actual.iter().zip(&references) {
            assert_eq!(cutting.number, reference.number);
            assert_eq!(daily.day_number, reference.day_number);
            assert_eq!(
                daily.day_after_planting,
                (reference.day_after_planting != -9).then_some(reference.day_after_planting)
            );
            assert_eq!(cutting.interval, reference.interval);
            assert_close_value(
                "cutting biomass",
                cutting.biomass,
                reference.biomass,
                0.000_500_001,
            );
            assert_close_value(
                "cumulative cutting biomass",
                cutting.cumulative_biomass,
                reference.cumulative_biomass,
                0.000_500_001,
            );
            assert_close_value(
                "cutting dry yield",
                cutting.dry_yield,
                reference.dry_yield,
                0.000_500_001,
            );
            assert_close_value(
                "cumulative cutting dry yield",
                cutting.cumulative_dry_yield,
                reference.cumulative_dry_yield,
                0.000_500_001,
            );
        }
    }
    Ok(())
}

#[test]
fn copied_official_project_matches_daily_reference_results() -> TestResult {
    let project = OfficialTestcase::new().copy()?;

    let result = run_project(project.working_root(), "LIST/Ottawa.PRM")?;
    assert_eq!(result.runs.len(), 3);
    for (index, run) in result.runs.iter().enumerate() {
        let references = official_run_daily_results(i32::try_from(index)? + 1)?;
        assert_eq!(run.daily.len(), references.len());
        for (daily, reference) in run.daily.iter().zip(&references) {
            assert_daily_result(daily, reference);
        }
    }
    Ok(())
}

#[test]
fn copied_official_testcase_generates_the_reference_output_set() -> TestResult {
    let project = OfficialTestcase::new().copy()?;

    run_project_with_outputs(project.working_root(), "LIST/Ottawa.PRM")?;
    project.assert_reference_outputs()?;
    Ok(())
}

#[test]
fn copied_project_resolves_partial_gdd_calendar_days() -> TestResult {
    let project = OfficialTestcase::new().copy()?;
    let crop_path = project.working_root().join("DATA/AlfOttawaGDD.CRO");
    let crop = fs::read_to_string(&crop_path)?;
    let partial = crop
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
    fs::write(crop_path, partial)?;

    let result = run_project_with_outputs(project.working_root(), "LIST/Ottawa.PRM")?;

    assert_eq!(result.runs.len(), 3);
    Ok(())
}

fn build_run_simulation(
    run_index: usize,
    initial: InitialConditions,
) -> Result<Simulation, aquacrop_core::Error> {
    let project = OfficialTestcase::new();
    load_project_run(project.source_root(), "LIST/Ottawa.PRM", run_index, initial)
}

struct OfficialDailyResult {
    day_number: i32,
    day_after_planting: i32,
    profile_water: f64,
    surface_water: f64,
    rain: f64,
    irrigation: f64,
    infiltration: f64,
    runoff: f64,
    drainage: f64,
    capillary_rise: f64,
    potential_evaporation: f64,
    evaporation: f64,
    potential_transpiration: f64,
    transpiration: f64,
    stomatal_stress: i32,
    growing_degree_days: f64,
    rooting_depth: f64,
    canopy_cover: f64,
    water_productivity: f64,
    biomass: f64,
    harvest_index: Option<f64>,
    dry_yield: f64,
    mobilized_assimilates: f64,
    stored_assimilates: f64,
    maximum_root_zone_water: f64,
    root_zone_water: f64,
    root_zone_water_at_saturation: f64,
    root_zone_water_at_field_capacity: f64,
    root_zone_leaf_threshold: f64,
    root_zone_stomatal_threshold: f64,
    root_zone_senescence_threshold: f64,
    root_zone_water_at_wilting_point: f64,
    compartment_water: Vec<f64>,
    compartment_extract_conductivity: Vec<f64>,
    reference_et: f64,
    minimum_temperature: f64,
    maximum_temperature: f64,
    carbon_dioxide: f64,
}

struct OfficialCuttingResult {
    number: i32,
    day_number: i32,
    day_after_planting: i32,
    interval: i32,
    biomass: f64,
    cumulative_biomass: f64,
    dry_yield: f64,
    cumulative_dry_yield: f64,
}

fn official_cutting_results(
    requested_run: i32,
) -> Result<Vec<OfficialCuttingResult>, Box<dyn std::error::Error>> {
    let out_path = OfficialTestcase::new()
        .source_root()
        .join("OUTP_REF/OttawaPRMharvests.OUT");
    let output = fs::read_to_string(out_path)?;
    let mut in_requested_run = false;
    let mut results = Vec::new();
    for line in output.lines() {
        if let Some(run) = parse_run_header(line) {
            if in_requested_run && run != requested_run {
                break;
            }
            in_requested_run = run == requested_run;
            continue;
        }
        let values: Vec<_> = line.split_whitespace().collect();
        let Some(number) = values.first().and_then(|value| value.parse::<i32>().ok()) else {
            continue;
        };
        if !in_requested_run || !(1..9999).contains(&number) {
            continue;
        }
        let date = aquacrop_core::calendar::CalendarDate::new(
            parse_integer(&values, 3)?,
            parse_integer(&values, 2)?,
            parse_integer(&values, 1)?,
        )?;
        results.push(OfficialCuttingResult {
            number,
            day_number: date.day_number(),
            day_after_planting: parse_integer(&values, 4)?,
            interval: parse_integer(&values, 5)?,
            biomass: parse_real(&values, 6)?,
            cumulative_biomass: parse_real(&values, 7)?,
            dry_yield: parse_real(&values, 8)?,
            cumulative_dry_yield: parse_real(&values, 9)?,
        });
    }
    Ok(results)
}

fn official_run_daily_results(
    requested_run: i32,
) -> Result<Vec<OfficialDailyResult>, Box<dyn std::error::Error>> {
    let out_path = OfficialTestcase::new()
        .source_root()
        .join("OUTP_REF/OttawaPRMday.OUT");
    let output = fs::read_to_string(out_path)?;
    let mut in_requested_run = false;
    let mut results = Vec::new();
    for line in output.lines() {
        if let Some(run) = parse_run_header(line) {
            if in_requested_run && run != requested_run {
                break;
            }
            in_requested_run = run == requested_run;
            continue;
        }
        if in_requested_run
            && line
                .split_whitespace()
                .next()
                .is_some_and(|token| token.parse::<i32>().is_ok())
        {
            results.push(parse_official_daily_result(line)?);
        }
    }
    Ok(results)
}

fn official_run_stored_assimilates(requested_run: i32) -> Result<f64, Box<dyn std::error::Error>> {
    let out_path = OfficialTestcase::new()
        .source_root()
        .join("OUTP_REF/OttawaPRMseason.OUT");
    let output = fs::read_to_string(out_path)?;
    let prefix = format!("Tot({requested_run})");
    let row = output
        .lines()
        .find(|line| line.split_whitespace().next() == Some(prefix.as_str()))
        .ok_or("official seasonal output does not contain the requested run")?;
    parse_real(&row.split_whitespace().collect::<Vec<_>>(), 36)
}

fn parse_run_header(line: &str) -> Option<i32> {
    let (label, run) = line.trim().split_once(':')?;
    if label != "Run" {
        return None;
    }
    run.trim().parse().ok()
}

fn parse_official_daily_result(
    line: &str,
) -> Result<OfficialDailyResult, Box<dyn std::error::Error>> {
    let values: Vec<_> = line.split_whitespace().collect();
    let date = aquacrop_core::calendar::CalendarDate::new(
        parse_integer(&values, 2)?,
        parse_integer(&values, 1)?,
        parse_integer(&values, 0)?,
    )?;
    let harvest_index = parse_real(&values, 40)?;
    Ok(OfficialDailyResult {
        day_number: date.day_number(),
        day_after_planting: parse_integer(&values, 3)?,
        profile_water: parse_real(&values, 5)?,
        surface_water: parse_real(&values, 8)?,
        rain: parse_real(&values, 6)?,
        irrigation: parse_real(&values, 7)?,
        infiltration: parse_real(&values, 9)?,
        runoff: parse_real(&values, 10)?,
        drainage: parse_real(&values, 11)?,
        capillary_rise: parse_real(&values, 12)?,
        potential_evaporation: parse_real(&values, 14)?,
        evaporation: parse_real(&values, 15)?,
        potential_transpiration: parse_real(&values, 17)?,
        transpiration: parse_real(&values, 18)?,
        stomatal_stress: parse_integer(&values, 26)?,
        growing_degree_days: parse_real(&values, 23)?,
        rooting_depth: parse_real(&values, 24)?,
        canopy_cover: parse_real(&values, 30)? / 100.0,
        water_productivity: parse_real(&values, 38)?,
        biomass: parse_real(&values, 39)?,
        harvest_index: (harvest_index >= 0.0).then_some(harvest_index),
        dry_yield: parse_real(&values, 41)?,
        mobilized_assimilates: parse_real(&values, 45)?,
        stored_assimilates: parse_real(&values, 46)?,
        maximum_root_zone_water: parse_real(&values, 48)?,
        root_zone_water: parse_real(&values, 50)?,
        root_zone_water_at_saturation: parse_real(&values, 51)?,
        root_zone_water_at_field_capacity: parse_real(&values, 52)?,
        root_zone_leaf_threshold: parse_real(&values, 53)?,
        root_zone_stomatal_threshold: parse_real(&values, 54)?,
        root_zone_senescence_threshold: parse_real(&values, 55)?,
        root_zone_water_at_wilting_point: parse_real(&values, 56)?,
        compartment_water: (68..80)
            .map(|index| parse_real(&values, index))
            .collect::<Result<_, _>>()?,
        compartment_extract_conductivity: (80..92)
            .map(|index| parse_real(&values, index))
            .collect::<Result<_, _>>()?,
        reference_et: parse_real(&values, 93)?,
        minimum_temperature: parse_real(&values, 94)?,
        maximum_temperature: parse_real(&values, 96)?,
        carbon_dioxide: parse_real(&values, 97)?,
    })
}

fn parse_real(values: &[&str], index: usize) -> Result<f64, Box<dyn std::error::Error>> {
    Ok(values
        .get(index)
        .ok_or("official daily output row is incomplete")?
        .parse()?)
}

fn parse_integer(values: &[&str], index: usize) -> Result<i32, Box<dyn std::error::Error>> {
    Ok(values
        .get(index)
        .ok_or("official daily output row is incomplete")?
        .parse()?)
}

fn assert_daily_result(actual: &DailyResult, reference: &OfficialDailyResult) {
    assert_eq!(actual.day_number, reference.day_number);
    assert_eq!(
        actual.day_after_planting,
        (reference.day_after_planting != -9).then_some(reference.day_after_planting)
    );
    assert_water_fluxes(actual, reference);
    assert_crop_growth(actual, reference);
    assert_soil_profile(actual, reference);
    assert_climate(actual, reference);
    assert_close(
        reference,
        "water balance",
        actual.water_balance_error,
        0.0,
        1.0e-9,
    );
    assert_close(
        reference,
        "salt balance",
        actual.salt_balance_error,
        0.0,
        1.0e-9,
    );
}

fn assert_water_fluxes(actual: &DailyResult, reference: &OfficialDailyResult) {
    assert_rounded(
        reference,
        "profile water",
        actual.profile_water,
        reference.profile_water,
        10.0,
    );
    assert_rounded(reference, "rain", actual.rain, reference.rain, 10.0);
    assert_rounded(
        reference,
        "irrigation",
        actual.irrigation,
        reference.irrigation,
        10.0,
    );
    assert_rounded(
        reference,
        "surface water",
        actual.surface_water,
        reference.surface_water,
        10.0,
    );
    assert_rounded(
        reference,
        "infiltration",
        actual.infiltration,
        reference.infiltration,
        10.0,
    );
    assert_rounded(reference, "runoff", actual.runoff, reference.runoff, 10.0);
    assert_rounded(
        reference,
        "drainage",
        actual.drainage,
        reference.drainage,
        10.0,
    );
    assert_rounded(
        reference,
        "capillary rise",
        actual.capillary_rise,
        reference.capillary_rise,
        10.0,
    );
    assert_rounded(
        reference,
        "potential evaporation",
        actual.potential_evaporation,
        reference.potential_evaporation,
        10.0,
    );
    assert_rounded(
        reference,
        "evaporation",
        actual.evaporation,
        reference.evaporation,
        10.0,
    );
    assert_rounded(
        reference,
        "potential transpiration",
        actual.potential_transpiration,
        reference.potential_transpiration,
        10.0,
    );
    assert_rounded(
        reference,
        "transpiration",
        actual.transpiration,
        reference.transpiration,
        10.0,
    );
    assert_eq!(
        actual
            .stomatal_stress
            .map_or(-9, |stress| { aquacrop_core::compat::roundc_i32(stress) }),
        reference.stomatal_stress,
        "stomatal stress differs on day {}",
        reference.day_number,
    );
}

#[allow(
    clippy::expect_used,
    reason = "test diagnostics require these reference values to be present together"
)]
fn assert_soil_profile(actual: &DailyResult, reference: &OfficialDailyResult) {
    if reference.maximum_root_zone_water < 0.0 {
        assert!(
            actual.maximum_root_zone_water.is_none(),
            "day {} maximum-root-zone water should be undefined",
            reference.day_number
        );
        assert!(
            actual.root_zone_water.is_none(),
            "day {} root-zone water should be undefined",
            reference.day_number
        );
        assert_compartment_results(actual, reference);
        return;
    }

    let maximum = actual
        .maximum_root_zone_water
        .as_ref()
        .expect("the official run reports maximum-root-zone water");
    assert_rounded(
        reference,
        "maximum-root-zone water",
        maximum.actual,
        reference.maximum_root_zone_water,
        10.0,
    );

    let root_zone = actual
        .root_zone_water
        .as_ref()
        .expect("the official run reports root-zone water");
    for (field, actual, expected) in [
        (
            "root-zone water",
            root_zone.actual,
            reference.root_zone_water,
        ),
        (
            "root-zone saturation water",
            root_zone.saturation,
            reference.root_zone_water_at_saturation,
        ),
        (
            "root-zone field-capacity water",
            root_zone.field_capacity,
            reference.root_zone_water_at_field_capacity,
        ),
        (
            "root-zone leaf threshold",
            root_zone.leaf_threshold,
            reference.root_zone_leaf_threshold,
        ),
        (
            "root-zone stomatal threshold",
            root_zone.stomatal_threshold,
            reference.root_zone_stomatal_threshold,
        ),
        (
            "root-zone senescence threshold",
            root_zone.senescence_threshold,
            reference.root_zone_senescence_threshold,
        ),
        (
            "root-zone wilting-point water",
            root_zone.wilting_point,
            reference.root_zone_water_at_wilting_point,
        ),
    ] {
        assert_rounded(reference, field, actual, expected, 10.0);
    }

    assert_compartment_results(actual, reference);
}

fn assert_compartment_results(actual: &DailyResult, reference: &OfficialDailyResult) {
    assert_eq!(
        actual.compartments.len(),
        reference.compartment_water.len(),
        "day {} compartment count differs",
        reference.day_number
    );
    for (index, (actual, expected)) in actual
        .compartments
        .iter()
        .zip(&reference.compartment_water)
        .enumerate()
    {
        assert_rounded(
            reference,
            &format!("compartment {} water content", index + 1),
            actual.theta * 100.0,
            *expected,
            10.0,
        );
    }
    for (index, (actual, expected)) in actual
        .compartments
        .iter()
        .zip(&reference.compartment_extract_conductivity)
        .enumerate()
    {
        assert_rounded(
            reference,
            &format!("compartment {} extract conductivity", index + 1),
            actual.extract_conductivity,
            *expected,
            10.0,
        );
    }
}

fn assert_climate(actual: &DailyResult, reference: &OfficialDailyResult) {
    assert_rounded(
        reference,
        "reference ET",
        actual.reference_et,
        reference.reference_et,
        10.0,
    );
    assert_rounded(
        reference,
        "minimum temperature",
        actual.minimum_temperature,
        reference.minimum_temperature,
        10.0,
    );
    assert_rounded(
        reference,
        "maximum temperature",
        actual.maximum_temperature,
        reference.maximum_temperature,
        10.0,
    );
    assert_rounded(
        reference,
        "carbon dioxide",
        actual.carbon_dioxide,
        reference.carbon_dioxide,
        100.0,
    );
}

fn assert_crop_growth(actual: &DailyResult, reference: &OfficialDailyResult) {
    assert_rounded(
        reference,
        "GDD",
        actual.growing_degree_days,
        reference.growing_degree_days,
        10.0,
    );
    assert_rounded(
        reference,
        "rooting depth",
        actual.rooting_depth,
        reference.rooting_depth,
        100.0,
    );
    assert_rounded(
        reference,
        "canopy cover",
        actual.canopy_cover,
        reference.canopy_cover,
        1_000.0,
    );
    assert_rounded(
        reference,
        "water productivity",
        actual.water_productivity,
        reference.water_productivity,
        10.0,
    );
    assert_rounded(
        reference,
        "biomass",
        actual.biomass,
        reference.biomass,
        1_000.0,
    );
    assert_eq!(
        actual.harvest_index.is_some(),
        reference.harvest_index.is_some(),
        "day {} harvest-index availability differs",
        reference.day_number
    );
    if let (Some(actual), Some(expected)) = (actual.harvest_index, reference.harvest_index) {
        assert_rounded(reference, "harvest index", actual, expected, 10.0);
    }
    assert_rounded(
        reference,
        "dry yield",
        actual.dry_yield,
        reference.dry_yield,
        1_000.0,
    );
    assert_rounded(
        reference,
        "mobilized assimilates",
        actual.mobilized_assimilates,
        reference.mobilized_assimilates,
        1_000.0,
    );
    assert_rounded(
        reference,
        "stored assimilates",
        actual.stored_assimilates,
        reference.stored_assimilates,
        1_000.0,
    );
}

fn assert_rounded(
    reference: &OfficialDailyResult,
    field: &str,
    actual: f64,
    expected: f64,
    scale: f64,
) {
    let rounding_interval = (0.5 + 1.0e-9) / scale;
    assert!(
        (actual - expected).abs() <= rounding_interval,
        "day {} {field}: actual {actual}, expected {expected}, rounding interval {rounding_interval}",
        reference.day_number,
    );
}

fn assert_close(
    reference: &OfficialDailyResult,
    field: &str,
    actual: f64,
    expected: f64,
    tolerance: f64,
) {
    assert!(
        (actual - expected).abs() <= tolerance,
        "day {} {field}: actual {actual}, expected {expected}, tolerance {tolerance}",
        reference.day_number
    );
}

fn assert_close_value(field: &str, actual: f64, expected: f64, tolerance: f64) {
    assert!(
        (actual - expected).abs() <= tolerance,
        "{field}: actual {actual}, expected {expected}, tolerance {tolerance}"
    );
}
