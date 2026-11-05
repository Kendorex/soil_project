//! Tests that `BmiModel::end_time()` reflects the current simulation end day.
//!
//! `end_time()` used to cache `last_day_number - first_day_number` at `initialize()`, before
//! `aquacrop-core`'s own `adjust_premature_end` (`crates/aquacrop-core/src/engine.rs`) had a chance
//! to shorten `last_day_number` for a non-forage crop with a declared `.CRO` `premature_end_day` --
//! a real field, not a hypothetical one. The cached value silently disagreed with what the running
//! simulation actually honored: the crop cycle genuinely stopped producing new results at the
//! adjusted day, while `end_time()` kept reporting the original, longer declared period. A coupling
//! orchestrator looping `while t < model.end_time()` would run the model past its actual season
//! end with no error and plausible-looking (frozen) numbers.
//!
//! Fixed by adding `Simulation::last_day_number()` (a live accessor,
//! `crates/aquacrop-core/src/engine.rs`) and having `end_time()` read it fresh on every call instead
//! of caching a pre-adjustment snapshot. `first_day_number` is still safely cached -- confirmed
//! nothing in `aquacrop-core` ever mutates it after construction, unlike `last_day_number`.

use std::fs;

use aquacrop_bmi::BmiModel;
use aquacrop_conformance::OfficialTestcase;

fn read_fixture(relative: &str) -> Result<String, Box<dyn std::error::Error>> {
    Ok(fs::read_to_string(
        OfficialTestcase::new().source_root().join(relative),
    )?)
}

/// `SIMUL/DEFAULT.CRO` (a non-forage, calendar-cycle, AquaCrop-7.3-format crop, 125 calendar days
/// to maturity) with its declared `DayNr Premature end` field (line 26, `-9` = unset) set to
/// `premature_end_day`, an absolute day-of-year counted from 1 January of the planting year.
fn crop_with_premature_end(premature_end_day: i32) -> Result<String, Box<dyn std::error::Error>> {
    let text = read_fixture("SIMUL/DEFAULT.CRO")?;
    let mut lines: Vec<String> = text.lines().map(str::to_owned).collect();
    assert!(
        lines[25].contains("Premature end"),
        "expected DEFAULT.CRO line 26 to be the premature-end field, got: {}",
        lines[25]
    );
    lines[25] = format!(
        "   {premature_end_day:<8}  : DayNr Premature end (counting from 1 January of planting year) - only applicable for annual crops"
    );
    Ok(lines.join("\n"))
}

/// A flat climate series covering `n_days`, starting 1 January of `year`, in the standard
/// 8-header-line AquaCrop text format.
fn flat_climate(n_days: usize) -> (String, String, String) {
    let mut tnx = String::from("Flat\n     1\n     1\n     1\n  2020\n\n  Tmin\tTmax\n===\n");
    let mut plu = String::from("Flat\n     1\n     1\n     1\n  2020\n\n  Rain\n===\n");
    let mut eto = String::from("Flat\n     1\n     1\n     1\n  2020\n\n  ETo\n===\n");
    for _ in 0..n_days {
        tnx.push_str("15.0\t25.0\n");
        plu.push_str("0.0\n");
        eto.push_str("5.0\n");
    }
    (tnx, plu, eto)
}

fn scenario_json(crop_text: &str) -> Result<String, Box<dyn std::error::Error>> {
    // 2020 is a leap year: 1 Jan-30 May inclusive is 151 days, not 150 -- pad generously.
    let (tnx, plu, eto) = flat_climate(160);
    Ok(serde_json::json!({
        "season_year": 1,
        "crop_text": crop_text,
        "soil_text": read_fixture("DATA/Ottawa.SOL")?,
        "temperature_text": tnx,
        "rain_text": plu,
        "eto_text": eto,
        "co2_text": read_fixture("SIMUL/MaunaLoa.CO2")?,
        "simulation_start": {"year": 2020, "month": 1, "day": 1},
        "simulation_end": {"year": 2020, "month": 5, "day": 30},
        "crop_start": {"year": 2020, "month": 1, "day": 1},
        "crop_end": {"year": 2020, "month": 5, "day": 30},
    })
    .to_string())
}

#[test]
fn end_time_reflects_premature_end_adjustment() -> Result<(), Box<dyn std::error::Error>> {
    // simulation_start (1 Jan 2020) to simulation_end (30 May 2020) declares a 150-day period.
    // premature_end_day=100 shortens the crop (and simulation) end to day 99 (100 - 1), well
    // short of the crop's own natural 125-day maturity and far short of the declared 150.
    const PREMATURE_END_DAY: i32 = 100;
    const EXPECTED_END_TIME: f64 = (PREMATURE_END_DAY - 1) as f64;

    let crop_text = crop_with_premature_end(PREMATURE_END_DAY)?;
    let json = scenario_json(&crop_text)?;

    let mut model = BmiModel::new();
    model.initialize(&json)?;

    let end_time = model.end_time()?;
    #[allow(
        clippy::float_cmp,
        reason = "end_time is a whole-day count carried in BMI's f64 time API; both sides are \
                  exactly-representable integers, so exact equality is the intended assertion"
    )]
    {
        assert_eq!(
            end_time, EXPECTED_END_TIME,
            "end_time() should reflect the premature-end-adjusted period ({EXPECTED_END_TIME}), \
             not the originally-declared 150-day period -- got {end_time}"
        );
    }

    // Confirm end_time() actually matches where the simulation stops producing new results,
    // not just that it changed to *some* different number.
    let mut buf = [0.0];
    let mut last_biomass = -1.0;
    let mut last_changed_day = -1;
    for day in 0..(PREMATURE_END_DAY - 1 + 20) {
        model.update()?;
        model.get_value("plant_biomass~above-ground", &mut buf)?;
        if (buf[0] - last_biomass).abs() > 1e-12 {
            last_changed_day = day;
        }
        last_biomass = buf[0];
    }
    #[allow(
        clippy::float_cmp,
        reason = "both sides are whole-day counts (one converted from i32), exactly representable \
                  in f64, so exact equality is the intended assertion"
    )]
    {
        assert_eq!(
            f64::from(last_changed_day),
            end_time,
            "end_time() ({end_time}) should match the last day the simulation actually produced a \
             new result ({last_changed_day})"
        );
    }

    Ok(())
}

#[test]
fn end_time_unaffected_when_no_premature_end_is_declared() -> Result<(), Box<dyn std::error::Error>>
{
    // Same crop file, sentinel (-9, "unset") premature-end value -- end_time() should report the
    // full, unadjusted 150-day declared period.
    let crop_text = crop_with_premature_end(-9)?;
    let json = scenario_json(&crop_text)?;

    let mut model = BmiModel::new();
    model.initialize(&json)?;

    #[allow(
        clippy::float_cmp,
        reason = "150 is a whole-day count carried in BMI's f64 time API, exactly representable, \
                  so exact equality is the intended assertion"
    )]
    {
        assert_eq!(
            model.end_time()?,
            150.0,
            "end_time() should report the full declared period when no premature end is set"
        );
    }

    Ok(())
}
