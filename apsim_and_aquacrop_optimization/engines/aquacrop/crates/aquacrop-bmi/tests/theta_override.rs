//! Verifies the fixed `soil__water_content_in_layers` setter: injected soil moisture now reaches
//! every compartment that legitimately belongs to the target layer, including compartments beyond
//! the declared `.SOL` horizon depth -- not just the ones within it.
//!
//! ## Root cause and fix (see `crates/aquacrop-bmi/src/engine_values.rs`'s `scatter_layer_theta`)
//!
//! `aquacrop-core`'s compartment grid is sized to the *crop's* maximum effective rooting depth
//! (`AdjustSizeCompartments`), not the declared soil profile depth -- a soil with shallower
//! declared horizons than the crop's rooting depth legitimately has compartments extending past
//! the last declared horizon, using that horizon's own properties (`soil.rs`'s
//! `layer_index_at_depth`, which clamps to the last layer index for any depth beyond the last
//! declared boundary). This is official AquaCrop behavior, not a Rust deviation: the reference's
//! own `AdjustThetaInitial` (`global.f90:5931-5940`, "Stretch thickness of bottom soil layer if
//! required") permanently stretches the deepest declared layer's *own* thickness value in memory
//! to cover the compartment grid's actual depth, once at initialization.
//!
//! The old `scatter_layer_theta` computed each layer's write boundary purely from the declared
//! `.SOL` thicknesses, never learning about this extension -- so injecting a value for the last
//! declared layer silently never reached compartments past the original declared depth, no error,
//! no indication. The fix stretches a local copy of the last layer's thickness by exactly the
//! amount the reference does (compartment total depth minus declared depth) before computing
//! overlap boundaries, leaving `soil_layer_thicknesses` itself (used for grid sizing and
//! `soil_water_actual_layer-N`) and the existing last-write-wins overlap semantics for genuinely
//! straddling compartments at declared-horizon boundaries completely unchanged.
//!
//! ## Read-path asymmetry noted, not touched by this fix
//!
//! Before the fix, `get_value("soil__water_content_in_layers", ...)` (which aggregates by
//! `compartment.layer_index`, already extension-aware) reported a *different*, misleading value
//! than what was actually written by `set_value` for the same name -- injecting `0.14` into
//! Ottawa's single declared layer read back as `~0.211` (a blend of the 8 correctly-written
//! compartments and the 4 silently-dropped ones), while the per-compartment getter showed the true
//! picture. After this fix, both getters agree because the underlying write now actually reaches
//! every compartment `layer_index` says belongs to that layer -- this was a symptom of the write
//! bug, not a separate defect, and needed no changes of its own.
//!
//! ## Separate, deliberately out of scope
//!
//! The one-`update()` read lag on `get_value("soil__water_content_in_layers"/
//! "soil__water_content_in_compartments", ...)` relative to `set_value` (documented in a prior
//! revision of this file) is a distinct finding, already characterized, and not touched here.

use std::fs;
use std::path::Path;

use aquacrop_bmi::BmiModel;
use aquacrop_conformance::OfficialTestcase;

const GOSHA_FIXTURES: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures");

fn read_fixture(relative: &str) -> Result<String, Box<dyn std::error::Error>> {
    Ok(fs::read_to_string(
        OfficialTestcase::new().source_root().join(relative),
    )?)
}

fn read_gosha_fixture(relative: &str) -> Result<String, Box<dyn std::error::Error>> {
    Ok(fs::read_to_string(
        Path::new(GOSHA_FIXTURES).join(relative),
    )?)
}

fn ottawa_scenario_json() -> Result<String, Box<dyn std::error::Error>> {
    Ok(serde_json::json!({
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
    })
    .to_string())
}

#[test]
fn theta_set_value_covers_the_full_compartment_depth() -> Result<(), Box<dyn std::error::Error>> {
    // Ottawa.SOL: single horizon, 1.5 m deep, Sat=46%, FC=29%, WP=13% (vol %). AlfOttawaGDD's 3.00 m
    // maximum rooting depth extends the compartment grid to 3.05 m (12 compartments), so
    // compartments 8-11 (1.45 m of the 3.05 m profile) legitimately exist beyond the declared
    // horizon and must be reachable by set_value like every other compartment.
    const INJECTED_THETA: f64 = 0.14;

    let json = ottawa_scenario_json()?;

    let mut control = BmiModel::new();
    control.initialize(&json)?;
    let mut injected = BmiModel::new();
    injected.initialize(&json)?;

    let comp_size = control.grid_size("soil__water_content_in_compartments")?;
    let layer_size = control.grid_size("soil__water_content_in_layers")?;
    assert_eq!(
        layer_size, 1,
        "Ottawa.SOL should declare exactly one horizon"
    );
    assert_eq!(
        comp_size, 12,
        "AlfOttawaGDD/Ottawa.SOL should build the full 12-compartment grid"
    );
    let mut comp_buf = vec![0.0; comp_size];
    let mut layer_buf = vec![0.0; layer_size];

    for _ in 0..10 {
        control.update()?;
        injected.update()?;
    }

    injected.get_value("soil__water_content_in_compartments", &mut comp_buf)?;
    let pre_injection = comp_buf.clone();
    eprintln!("day 10 (pre-injection) per-compartment theta: {pre_injection:?}");

    injected.set_value(
        "soil__water_content_in_layers",
        &vec![INJECTED_THETA; layer_size],
    )?;

    // Read-path round-trip, verification 1: both getters must now agree with what was injected,
    // once the documented one-update() lag has passed (see module doc's "Separate, deliberately
    // out of scope" note).
    control.update()?;
    injected.update()?;

    injected.get_value("soil__water_content_in_compartments", &mut comp_buf)?;
    let injected_day11 = comp_buf.clone();
    injected.get_value("soil__water_content_in_layers", &mut layer_buf)?;
    eprintln!("day 11 (one update() after injection) per-compartment: {injected_day11:?}");
    eprintln!("day 11 (one update() after injection) layer-aggregated: {layer_buf:?}");

    for (compartment, &theta) in injected_day11.iter().enumerate() {
        let moved_from_injection = (theta - INJECTED_THETA).abs();
        assert!(
            moved_from_injection < 0.05,
            "compartment {compartment}: theta moved implausibly far from the injected value in a \
             single day ({INJECTED_THETA} -> {theta}) -- expected every compartment to reflect the \
             injection, including those beyond the declared 1.5 m horizon"
        );
    }
    // The layer-aggregated getter must now closely match the injected value too (previously misled
    // by the 4 silently-dropped compartments, reading back ~0.211 instead of ~0.14).
    assert!(
        (layer_buf[0] - INJECTED_THETA).abs() < 0.05,
        "layer-aggregated get_value ({}) diverged from the injected value ({INJECTED_THETA}) by \
         more than a single day's physical evolution should allow -- the read/write asymmetry may \
         have returned",
        layer_buf[0]
    );

    // Physical effect, verification 2: the previously-dropped compartments (8-11) must now
    // demonstrably influence subsequent simulation behavior, not merely persist on read-back. Step
    // forward several more days and confirm the injected run's water-stress/transpiration diverges
    // measurably from the control run, driven by the whole profile (including the deep
    // compartments) now starting from a much drier state.
    let mut control_stress = [0.0];
    let mut injected_stress = [0.0];
    for _ in 0..15 {
        control.update()?;
        injected.update()?;
    }
    control.get_value("plant_stress_water", &mut control_stress)?;
    injected.get_value("plant_stress_water", &mut injected_stress)?;
    eprintln!(
        "cumulative water stress after injection: control={:.4}, injected={:.4}",
        control_stress[0], injected_stress[0]
    );
    assert!(
        injected_stress[0] > control_stress[0] + 1.0,
        "expected the drier full-profile injection to measurably increase cumulative water stress \
         relative to the control run (control={}, injected={}) -- if the deep compartments weren't \
         actually driving physics, this wouldn't move",
        control_stress[0],
        injected_stress[0]
    );

    Ok(())
}

#[test]
fn theta_set_value_state_overwrite_does_not_appear_as_a_spurious_flux()
-> Result<(), Box<dyn std::error::Error>> {
    // A direct state overwrite (like the infiltration override, but unlike it, not a flux) should
    // not show up as infiltration, drainage, or runoff on the day it's applied -- confirms the
    // injection isn't being double-counted or misattributed in the water-balance ledger.
    let json = ottawa_scenario_json()?;
    let mut model = BmiModel::new();
    model.initialize(&json)?;
    for _ in 0..10 {
        model.update()?;
    }

    let layer_size = model.grid_size("soil__water_content_in_layers")?;
    let comp_size = model.grid_size("soil__water_content_in_compartments")?;
    let mut thickness = vec![0.0; comp_size];
    model.get_value("soil_depth~compartment", &mut thickness)?;
    let mut theta = vec![0.0; comp_size];
    model.get_value("soil__water_content_in_compartments", &mut theta)?;
    let pre_injection_total: f64 = theta
        .iter()
        .zip(&thickness)
        .map(|(t, thickness)| t * thickness * 1000.0)
        .sum();

    model.set_value("soil__water_content_in_layers", &vec![0.14; layer_size])?;
    model.update()?;

    let mut infiltration = [0.0];
    let mut runoff = [0.0];
    let mut drainage = [0.0];
    model.get_value("soil_infiltration~rate", &mut infiltration)?;
    model.get_value("water_runoff~surface", &mut runoff)?;
    model.get_value("soil_water_drainage~deep", &mut drainage)?;
    eprintln!(
        "day of injection: infiltration={:.4}, runoff={:.4}, drainage={:.4}",
        infiltration[0], runoff[0], drainage[0]
    );

    model.get_value("soil__water_content_in_compartments", &mut theta)?;
    let post_injection_total: f64 = theta
        .iter()
        .zip(&thickness)
        .map(|(t, thickness)| t * thickness * 1000.0)
        .sum();
    let jump = pre_injection_total - post_injection_total;
    eprintln!(
        "profile storage: before={pre_injection_total:.2}mm, after={post_injection_total:.2}mm, \
         jump={jump:.2}mm (expected: a large drop, from injecting a dry value, unrelated to \
         infiltration/runoff/drainage that day)"
    );

    // The injection drops storage by tens of mm (0.29 -> 0.14 across most of a 3.05m profile);
    // none of that magnitude should be attributed to infiltration, runoff, or drainage that day --
    // those should stay at whatever small values the day's real weather alone would produce.
    assert!(
        jump > 20.0,
        "expected the injection itself to dominate the day's storage change (jump={jump}mm) -- \
         otherwise this test isn't exercising what it claims to"
    );
    assert!(
        infiltration[0] < 20.0 && runoff[0] < 20.0 && drainage[0] < 20.0,
        "infiltration ({}), runoff ({}), or drainage ({}) is implausibly large for what should be \
         an ordinary weather day -- the state overwrite may be leaking into flux accounting",
        infiltration[0],
        runoff[0],
        drainage[0]
    );

    Ok(())
}

#[test]
fn theta_set_value_covers_a_twenty_layer_extended_profile() -> Result<(), Box<dyn std::error::Error>>
{
    // A single uniform injected value (rather than a distinct value per layer) deliberately avoids
    // creating sharp artificial gradients between adjacent compartments -- with 20 declared layers
    // this close together relative to the compartment grid, distinct per-layer values trigger
    // substantial same-day redistribution/capillary flow (confirmed while developing this test:
    // deep compartments moved by several hundredths after just one update() following a
    // steeply-graded injection), which would make an exact-value comparison unreliable for reasons
    // unrelated to what this test checks. A uniform value sidesteps that while still exercising
    // every declared layer's write path and the extension mechanism.
    const INJECTED_THETA: f64 = 0.10;

    // GOSHA Pixel A's 20-horizon soil (0.10 m each, 2.00 m declared total) under Maize (2.30 m
    // maximum rooting depth) -- Rust-only, the Fortran fork's `max_SoilLayers = 10` cannot even
    // represent a 20-horizon soil, so there is no cross-implementation check for this specific
    // fixture (noted explicitly rather than silently narrowing the comparison). This exercises both
    // "many declared layers" and "compartment grid extends past the declared depth" at once, more
    // stringently than the single-horizon Ottawa case above.
    let json = serde_json::json!({
        "season_year": 1,
        "crop_text": read_gosha_fixture("crops/Maize.CRO")?,
        "soil_text": read_gosha_fixture("gosha/soil/PixelA_20h.SOL")?,
        "management_text": read_gosha_fixture("management/Fert0NoCut.MAN")?,
        "temperature_text": read_gosha_fixture("gosha/climate/GOSHAA.Tnx")?,
        "rain_text": read_gosha_fixture("gosha/climate/GOSHAA.PLU")?,
        "eto_text": read_gosha_fixture("gosha/climate/GOSHAA.ETo")?,
        "co2_text": read_fixture("SIMUL/MaunaLoa.CO2")?,
        "simulation_start": {"year": 2020, "month": 4, "day": 15},
        "simulation_end": {"year": 2020, "month": 7, "day": 15},
        "crop_start": {"year": 2020, "month": 4, "day": 15},
        "crop_end": {"year": 2020, "month": 7, "day": 15},
    })
    .to_string();

    let mut control = BmiModel::new();
    control.initialize(&json)?;
    let mut injected = BmiModel::new();
    injected.initialize(&json)?;

    let layer_size = control.grid_size("soil__water_content_in_layers")?;
    let comp_size = control.grid_size("soil__water_content_in_compartments")?;
    assert_eq!(
        layer_size, 20,
        "PixelA_20h.SOL should declare exactly 20 horizons"
    );
    eprintln!(
        "compartment count for a 2.30 m rooting depth over a 2.00 m declared soil: {comp_size}"
    );

    let mut thickness = vec![0.0; comp_size];
    control.update()?;
    injected.update()?;
    control.get_value("soil_depth~compartment", &mut thickness)?;
    let declared_depth = 2.0;
    let compartment_depth: f64 = thickness.iter().sum();
    eprintln!(
        "declared soil depth={declared_depth}m, compartment grid depth={compartment_depth:.3}m"
    );
    assert!(
        compartment_depth > declared_depth + 0.01,
        "expected Maize's 2.30 m rooting depth to extend the compartment grid past the declared \
         2.00 m soil depth (compartment_depth={compartment_depth}) -- otherwise this fixture \
         doesn't exercise the extension case and the test should use a deeper-rooted crop"
    );
    // Find the compartments that fall entirely beyond the declared 2.00 m depth (the ones the
    // original bug silently dropped for a single-horizon soil).
    let mut cumulative = 0.0;
    let mut beyond_declared_depth = Vec::new();
    for (index, &t) in thickness.iter().enumerate() {
        if cumulative >= declared_depth {
            beyond_declared_depth.push(index);
        }
        cumulative += t;
    }
    assert!(
        !beyond_declared_depth.is_empty(),
        "expected at least one compartment entirely beyond the declared 2.00 m depth"
    );
    eprintln!("compartments entirely beyond the declared depth: {beyond_declared_depth:?}");

    injected.set_value(
        "soil__water_content_in_layers",
        &vec![INJECTED_THETA; layer_size],
    )?;

    control.update()?;
    injected.update()?;

    let mut control_theta = vec![0.0; comp_size];
    let mut injected_theta = vec![0.0; comp_size];
    control.get_value("soil__water_content_in_compartments", &mut control_theta)?;
    injected.get_value("soil__water_content_in_compartments", &mut injected_theta)?;
    eprintln!("post-injection control:  {control_theta:?}");
    eprintln!("post-injection injected: {injected_theta:?}");

    for compartment in 0..comp_size {
        if beyond_declared_depth.contains(&compartment) {
            // The specific case this task fixes: compartments entirely beyond the declared horizon
            // depth must now diverge from the control run, proving the injection reached them.
            let divergence = (injected_theta[compartment] - control_theta[compartment]).abs();
            assert!(
                divergence > 0.02,
                "compartment {compartment} (beyond the declared 2.00 m depth): injected \
                 ({}) did not diverge meaningfully from control ({}) -- the injection may not be \
                 reaching compartments past the declared soil depth",
                injected_theta[compartment],
                control_theta[compartment]
            );
        }
        // All compartments, including the extended ones, should land close to the injected value
        // (not necessarily exact, since one day's evaporation/drainage/redistribution legitimately
        // moves it, especially for the shallow, evaporation-exposed top compartment).
        let moved_from_injection = (injected_theta[compartment] - INJECTED_THETA).abs();
        assert!(
            moved_from_injection < 0.05,
            "compartment {compartment}: theta ({}) diverged from the injected value \
             ({INJECTED_THETA}) by more than one day's physical evolution should allow",
            injected_theta[compartment]
        );
    }

    Ok(())
}
