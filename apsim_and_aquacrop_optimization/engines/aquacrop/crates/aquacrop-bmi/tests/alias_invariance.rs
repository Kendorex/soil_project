//! Tests that BMI aliases have the same behavior as their canonical names.
//!
//! The orphaned alias and its target must both be rejected.

use std::fs;

use aquacrop_bmi::BmiModel;
use aquacrop_bmi::registry;
use aquacrop_conformance::OfficialTestcase;

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

fn fresh_model_after_days(days: u32) -> Result<BmiModel, Box<dyn std::error::Error>> {
    let mut model = BmiModel::new();
    model.initialize(&scenario_json()?)?;
    for _ in 0..days {
        model.update()?;
    }
    Ok(model)
}

/// Alias whose target (`management_surface-storage`) is not declared in
/// `input_items` or `output_items`. Both names are rejected as unknown variables.
const ORPHANED_ALIAS: &str = "management__surface_storage";

/// `management_weed-cover`'s setter has no observable live effect (documented limitation --
/// `DailyResult.weed_cover` reflects the parsed configuration, not the wrapper-tracked write). The
/// set-consistency check is skipped for its alias for this reason, not because the alias itself is
/// broken.
const NO_LIVE_EFFECT_ALIAS: &str = "management__weed_cover";

#[test]
fn every_alias_resolves_identically_to_its_canonical_name() -> Result<(), Box<dyn std::error::Error>>
{
    assert_eq!(
        registry::ALIASES.len(),
        41,
        "this test must cover every alias; update it if the ledger's alias count changes"
    );

    let get_model = fresh_model_after_days(10)?;

    let mut checked = 0usize;
    let mut set_checked = 0usize;
    for (alias, canonical) in registry::ALIASES {
        checked += 1;

        if alias == ORPHANED_ALIAS {
            let mut buffer = [0.0];
            let alias_result = get_model.get_value(alias, &mut buffer);
            let canonical_result = get_model.get_value(canonical, &mut buffer);
            assert!(
                alias_result.is_err() && canonical_result.is_err(),
                "orphaned alias {alias} (-> {canonical}) must be rejected on both names, \
                 matching the reference's own dangling mapping"
            );
            continue;
        }

        let meta = registry::metadata(canonical)
            .ok_or_else(|| format!("alias {alias} resolves to undeclared canonical {canonical}"))?;
        let size = get_model.grid_size(canonical)?;
        assert_eq!(
            get_model.grid_size(alias)?,
            size,
            "alias {alias} and canonical {canonical} must report the same grid size"
        );

        // Get-consistency: every variable that supports get_value at all must return byte-for-byte
        // identical values whether addressed by alias or by canonical name, since alias resolution
        // is purely a name lookup ahead of the same underlying state.
        let mut via_alias = vec![0.0; size];
        let mut via_canonical = vec![0.0; size];
        let alias_get = get_model.get_value(alias, &mut via_alias);
        let canonical_get = get_model.get_value(canonical, &mut via_canonical);
        match (alias_get, canonical_get) {
            (Ok(()), Ok(())) => {
                assert_eq!(
                    via_alias, via_canonical,
                    "alias {alias} and canonical {canonical} returned different values"
                );
            }
            (Err(_), Err(_)) => {
                // Both consistently reject get (write-only variable). This matches the reference.
                // own get/set asymmetry; nothing further to compare for get.
            }
            (alias_result, canonical_result) => {
                return Err(format!(
                    "alias {alias} and canonical {canonical} disagree on whether get_value is \
                     supported: alias={alias_result:?}, canonical={canonical_result:?}"
                )
                .into());
            }
        }

        // Set-effect consistency, for every settable variable: driving one model entirely through
        // the alias and an otherwise-identical model entirely through the canonical name must
        // produce the identical resulting state, regardless of each variable's own clamping,
        // rounding, or persistent-vs-immediate timing (which this test does not need to know in
        // advance, since both paths dispatch through the identical underlying code once the name
        // is resolved).
        if meta.role == registry::Role::Output || size != 1 {
            // Vector inputs (soil__water_content_in_layers) have documented scatter semantics
            // exercised elsewhere (protocol.rs); output-only variables have no set to check.
            continue;
        }
        if alias == NO_LIVE_EFFECT_ALIAS {
            continue;
        }
        set_checked += 1;

        let probe_value = 17.25;
        let mut model_via_alias = fresh_model_after_days(10)?;
        let mut model_via_canonical = fresh_model_after_days(10)?;
        let alias_set = model_via_alias.set_value(alias, &[probe_value]);
        let canonical_set = model_via_canonical.set_value(canonical, &[probe_value]);
        assert!(
            alias_set.is_ok() && canonical_set.is_ok(),
            "alias {alias} / canonical {canonical}: set_value must succeed identically \
             (alias={alias_set:?}, canonical={canonical_set:?})"
        );
        model_via_alias.update()?;
        model_via_canonical.update()?;

        let mut result_from_alias_path = [0.0];
        let mut result_from_canonical_path = [0.0];
        model_via_alias.get_value(canonical, &mut result_from_alias_path)?;
        model_via_canonical.get_value(canonical, &mut result_from_canonical_path)?;
        assert_eq!(
            result_from_alias_path[0].to_bits(),
            result_from_canonical_path[0].to_bits(),
            "alias {alias}: setting through the alias produced a different resulting state \
             ({}) than setting through canonical {canonical} ({})",
            result_from_alias_path[0],
            result_from_canonical_path[0]
        );
    }

    assert_eq!(
        checked, 41,
        "every alias must be individually checked, not sampled"
    );
    assert!(
        set_checked >= 9,
        "expected at least the known settable, live-effect aliases to be exercised, got {set_checked}"
    );
    Ok(())
}
