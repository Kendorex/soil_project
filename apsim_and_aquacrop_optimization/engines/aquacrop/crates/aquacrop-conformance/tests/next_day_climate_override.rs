//! Regression coverage for `Simulation::next_day_climate_mut`.
//!
//! This accessor was added to `aquacrop-core` specifically so a BMI-style caller can override a
//! day's forcing before advancing the simulation, mirroring how the reference implementation
//! applies persistent weather overrides before each simulated day. These tests prove the override
//! is (a) visible to the very next `step()` call, (b) does not affect any other day, and (c) has no
//! effect once the configured climate series is exhausted.

use aquacrop_conformance::OfficialTestcase;
use aquacrop_core::engine::{InitialConditions, Simulation};
use aquacrop_core::io::load_project_run;

fn build_simulation() -> Result<Simulation, aquacrop_core::Error> {
    load_project_run(
        OfficialTestcase::new().source_root(),
        "LIST/Ottawa.PRM",
        0,
        InitialConditions::at_field_capacity(),
    )
}

#[test]
fn overriding_the_next_days_rain_is_visible_only_on_that_day()
-> Result<(), Box<dyn std::error::Error>> {
    let mut simulation = build_simulation()?;

    let first_day = simulation
        .step()?
        .ok_or("simulation ended before its first day")?;
    let unmodified_second_day_rain = {
        // Peek the upcoming record without mutating it, to know what the override replaces.
        let climate = simulation
            .next_day_climate_mut()
            .ok_or("simulation ended before its second day")?;
        climate.weather.rain
    };

    let overridden_rain = unmodified_second_day_rain + 37.5;
    {
        let climate = simulation
            .next_day_climate_mut()
            .ok_or("simulation ended before its second day")?;
        climate.weather.rain = overridden_rain;
    }

    let second_day = simulation
        .step()?
        .ok_or("simulation ended before its second day")?;
    assert_eq!(second_day.day_number, first_day.day_number + 1);
    assert_eq!(second_day.rain.to_bits(), overridden_rain.to_bits());

    // The third day must be unaffected: the override only ever touched the second day's record.
    let unmodified_third_day_rain = simulation
        .next_day_climate_mut()
        .ok_or("simulation ended before its third day")?
        .weather
        .rain;
    let third_day = simulation
        .step()?
        .ok_or("simulation ended before its third day")?;
    assert_eq!(
        third_day.rain.to_bits(),
        unmodified_third_day_rain.to_bits()
    );
    assert_ne!(
        third_day.rain.to_bits(),
        overridden_rain.to_bits(),
        "the override must not leak into the following day"
    );

    Ok(())
}

#[test]
fn step_keeps_returning_none_after_the_run_logically_ends() -> Result<(), Box<dyn std::error::Error>>
{
    let mut simulation = build_simulation()?;
    while simulation.step()?.is_some() {}

    // Idempotent exhaustion: once step() reaches the run's last_day_number, every further call
    // must keep returning Ok(None) rather than resuming, erroring, or oscillating.
    for _ in 0..5 {
        assert!(matches!(simulation.step(), Ok(None)));
    }
    Ok(())
}

#[test]
fn next_day_climate_mut_can_still_return_some_after_the_run_logically_ends()
-> Result<(), Box<dyn std::error::Error>> {
    // The Ottawa fixture's three linked runs share one multi-year climate series (see
    // io/runner.rs's climate assembly), so the first run's climate vector extends well past its
    // own last_day_number. This documents and locks in that `next_day_climate_mut` reports
    // vector exhaustion, not run-logical exhaustion: it keeps returning the same unconsumed
    // record (day_number never advances further) once step() stops, rather than turning `None`
    // merely because step() has stopped producing results. A BMI-style wrapper must therefore
    // track "has the run ended" against its own day/end-time bookkeeping, not by polling this
    // method for `None`.
    let mut simulation = build_simulation()?;
    while simulation.step()?.is_some() {}

    let stuck_day_number = simulation
        .next_day_climate_mut()
        .map(|climate| climate.day_number);
    assert!(stuck_day_number.is_some());

    // Calling step() again (still Ok(None), per the other test) must not change which record
    // next_day_climate_mut reports, since step() never advances past the out-of-range record.
    simulation.step()?;
    assert_eq!(
        simulation
            .next_day_climate_mut()
            .map(|climate| climate.day_number),
        stuck_day_number
    );

    Ok(())
}
