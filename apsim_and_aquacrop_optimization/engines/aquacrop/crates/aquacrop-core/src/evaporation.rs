//! Stateful two-stage soil evaporation.

use crate::Error;
use crate::compat::{MINIMUM_EVAPORATION_DEPTH_CM, ZERO_THRESHOLD, roundc_i8};
use crate::salinity::transport_salt_upward_after_evaporation;
use crate::soil::{PreparedSoilProfile, SoilCompartment};
use crate::water_balance::WaterBalanceError;

const MINIMUM_EVAPORATION_DEPTH_M: f64 = MINIMUM_EVAPORATION_DEPTH_CM / 100.0;
const STAGE_TWO_SUBSTEPS: i32 = 20;
const DEPTH_EXPANSION_FRACTION: f64 = 0.4;

/// Persistent state of the two-stage evaporation process.
#[derive(Debug)]
pub struct SoilEvaporationState {
    /// Readily evaporable water remaining for stage I, in millimetres.
    pub surface_water: f64,
    /// Relative water content at the start of stage II, in percent.
    pub stage_two_start: Option<i8>,
    /// Current extraction depth, in metres.
    pub extraction_depth: f64,
}

impl SoilEvaporationState {
    /// Constructs state for a profile without a preceding simulated day.
    #[must_use]
    pub fn without_previous_day() -> Self {
        Self {
            surface_water: 0.0,
            stage_two_start: None,
            extraction_depth: MINIMUM_EVAPORATION_DEPTH_M,
        }
    }

    /// Resets evaporation to stage I after rain or ordinary irrigation.
    ///
    /// All five arguments are water depths in millimetres.
    pub fn prepare_stage_one(
        &mut self,
        readily_evaporable_water: f64,
        rain: f64,
        irrigation: f64,
        runoff: f64,
        surface_storage: f64,
    ) {
        self.surface_water = if surface_storage > ZERO_THRESHOLD {
            readily_evaporable_water
        } else {
            (rain + irrigation - runoff).min(readily_evaporable_water)
        };
        self.stage_two_start = None;
        self.extraction_depth = MINIMUM_EVAPORATION_DEPTH_M;
    }
}

/// Actual soil evaporation and stage state after one daily step.
#[derive(Debug)]
pub struct SoilEvaporation {
    /// Water removed from the soil profile, in millimetres.
    pub actual: f64,
    /// Dissolved salt moved into the first compartment, in g/m2.
    pub upward_salt: f64,
}

/// Applies stage-I and stage-II soil evaporation for one day.
///
/// `potential` is daily potential evaporation in millimetres,
/// `maximum_depth` is metres, and `decline_factor` is dimensionless. Other
/// inputs retain their field-documented units.
///
/// # Errors
///
/// Returns [`Error`] for invalid extraction depths or compartment layer indices.
pub fn evaporate_soil(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    state: &mut SoilEvaporationState,
    potential: f64,
    maximum_depth: f64,
    decline_factor: f64,
) -> Result<SoilEvaporation, Error> {
    if !maximum_depth.is_finite() || maximum_depth < MINIMUM_EVAPORATION_DEPTH_M {
        return Err(WaterBalanceError::InvalidActiveDepth(maximum_depth).into());
    }
    if state.stage_two_start.is_none() && state.surface_water <= 0.0 {
        prepare_stage_two(profile, compartments, state)?;
    }
    let mut actual = 0.0;
    let mut upward_salt = 0.0;
    if potential > 0.0 && state.surface_water > 0.0 {
        let demand = state.surface_water.min(potential);
        let extracted = extract_water(profile, compartments, demand, MINIMUM_EVAPORATION_DEPTH_M)?;
        actual += extracted;
        state.surface_water -= extracted;
        if (demand - extracted).abs() > 0.000_1 {
            state.surface_water = 0.0;
        }
        if state.surface_water < ZERO_THRESHOLD {
            prepare_stage_two(profile, compartments, state)?;
        }
    }
    if potential - actual > ZERO_THRESHOLD {
        let initial_theta: Vec<f64> = compartments
            .iter()
            .map(|compartment| compartment.theta)
            .collect();
        actual += evaporate_stage_two(
            profile,
            compartments,
            state,
            potential - actual,
            maximum_depth,
            decline_factor,
        )?;
        upward_salt = transport_salt_upward_after_evaporation(
            profile,
            compartments,
            &initial_theta,
            maximum_depth,
        )?;
    }
    Ok(SoilEvaporation {
        actual,
        upward_salt,
    })
}

fn prepare_stage_two(
    profile: &PreparedSoilProfile,
    compartments: &[SoilCompartment],
    state: &mut SoilEvaporationState,
) -> Result<(), Error> {
    state.extraction_depth = MINIMUM_EVAPORATION_DEPTH_M;
    let saturation = water_in_evaporation_layer(
        profile,
        compartments,
        state.extraction_depth,
        &WaterContentKind::Saturation,
    )?;
    let field_capacity = water_in_evaporation_layer(
        profile,
        compartments,
        state.extraction_depth,
        &WaterContentKind::FieldCapacity,
    )?;
    let actual = water_in_evaporation_layer(
        profile,
        compartments,
        state.extraction_depth,
        &WaterContentKind::Actual,
    )?;
    let lower = field_capacity - f64::from(profile.readily_evaporable_water);
    state.stage_two_start = Some(if actual - lower <= f64::EPSILON {
        0
    } else {
        roundc_i8(100.0 * (actual - lower) / (saturation - lower))
    });
    Ok(())
}

fn evaporate_stage_two(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    state: &mut SoilEvaporationState,
    remaining_potential: f64,
    maximum_depth: f64,
    decline_factor: f64,
) -> Result<f64, Error> {
    let start = f64::from(state.stage_two_start.unwrap_or(0));
    let mut evaporated_water = 0.0;
    for _ in 0..STAGE_TWO_SUBSTEPS {
        let (mut upper, mut lower, mut water_content) =
            evaporation_layer_limits(profile, compartments, state.extraction_depth, start)?;
        let mut relative = (water_content - lower) / (upper - lower);
        while relative
            < DEPTH_EXPANSION_FRACTION * (maximum_depth - state.extraction_depth)
                / (maximum_depth - MINIMUM_EVAPORATION_DEPTH_M)
            && state.extraction_depth < maximum_depth
        {
            state.extraction_depth += 0.001;
            (upper, lower, water_content) =
                evaporation_layer_limits(profile, compartments, state.extraction_depth, start)?;
            relative = (water_content - lower) / (upper - lower);
        }
        let reduction = evaporation_reduction_coefficient(relative, decline_factor);
        let demand = reduction * remaining_potential / f64::from(STAGE_TWO_SUBSTEPS);
        evaporated_water += extract_water(profile, compartments, demand, state.extraction_depth)?;
    }
    Ok(evaporated_water)
}

fn evaporation_layer_limits(
    profile: &PreparedSoilProfile,
    compartments: &[SoilCompartment],
    depth: f64,
    start: f64,
) -> Result<(f64, f64, f64), Error> {
    let saturation =
        water_in_evaporation_layer(profile, compartments, depth, &WaterContentKind::Saturation)?;
    let field_capacity = water_in_evaporation_layer(
        profile,
        compartments,
        depth,
        &WaterContentKind::FieldCapacity,
    )?;
    let wilting_point = water_in_evaporation_layer(
        profile,
        compartments,
        depth,
        &WaterContentKind::WiltingPoint,
    )?;
    let actual =
        water_in_evaporation_layer(profile, compartments, depth, &WaterContentKind::Actual)?;
    let readily_evaporable = f64::from(profile.readily_evaporable_water);
    let upper = start / 100.0 * (saturation - (field_capacity - readily_evaporable))
        + (field_capacity - readily_evaporable);
    Ok((upper, wilting_point / 2.0, actual))
}

/// Reduction coefficient for stage-II soil evaporation.
///
/// `relative_water` and `decline_factor` are dimensionless; the result is a
/// dimensionless fraction.
#[must_use]
pub fn evaporation_reduction_coefficient(relative_water: f64, decline_factor: f64) -> f64 {
    if relative_water <= 0.000_01 {
        0.0
    } else if relative_water >= 0.999_99 {
        1.0
    } else {
        ((decline_factor * relative_water).exp() - 1.0) / (decline_factor.exp() - 1.0)
    }
}

fn extract_water(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    demand: f64,
    active_depth: f64,
) -> Result<f64, Error> {
    let mut extracted = 0.0;
    let mut cumulative_depth = 0.0;
    let compartment_count = compartments.len();
    for (compartment_index, compartment) in compartments.iter_mut().enumerate() {
        let layer =
            profile
                .layers
                .get(compartment.layer_index)
                .ok_or(WaterBalanceError::MissingLayer {
                    compartment: compartment_index,
                    layer: compartment.layer_index,
                })?;
        let fraction = if cumulative_depth + compartment.thickness > active_depth {
            (active_depth - cumulative_depth) / compartment.thickness
        } else {
            1.0
        };
        let air_dry_water = layer.water_storage(layer.wilting_point / 200.0, compartment.thickness);
        let current_water = layer.water_storage(compartment.theta, compartment.thickness);
        let available = (current_water - air_dry_water) * fraction;
        let still_needed = demand - extracted;
        if available > 0.0 {
            let removed = available.min(still_needed);
            extracted += removed;
            compartment.theta = (current_water - removed)
                / (1_000.0 * compartment.thickness * layer.fine_earth_fraction());
        }
        cumulative_depth += fraction * compartment.thickness;
        if compartment_index + 1 == compartment_count
            || still_needed.abs() < ZERO_THRESHOLD
            || cumulative_depth >= 0.999_999 * active_depth
        {
            break;
        }
    }
    Ok(extracted)
}

enum WaterContentKind {
    Saturation,
    FieldCapacity,
    WiltingPoint,
    Actual,
}

fn water_in_evaporation_layer(
    profile: &PreparedSoilProfile,
    compartments: &[SoilCompartment],
    depth: f64,
    kind: &WaterContentKind,
) -> Result<f64, Error> {
    let mut water = 0.0;
    let mut cumulative_depth = 0.0;
    for (compartment_index, compartment) in compartments.iter().enumerate() {
        if (depth - cumulative_depth).abs() <= 0.000_1 {
            break;
        }
        let layer =
            profile
                .layers
                .get(compartment.layer_index)
                .ok_or(WaterBalanceError::MissingLayer {
                    compartment: compartment_index,
                    layer: compartment.layer_index,
                })?;
        let fraction = if cumulative_depth + compartment.thickness > depth {
            (depth - cumulative_depth) / compartment.thickness
        } else {
            1.0
        };
        let theta = match kind {
            WaterContentKind::Saturation => layer.saturation / 100.0,
            WaterContentKind::FieldCapacity => layer.field_capacity / 100.0,
            WaterContentKind::WiltingPoint => layer.wilting_point / 100.0,
            WaterContentKind::Actual => compartment.theta,
        };
        water += layer.water_storage(theta, compartment.thickness) * fraction;
        cumulative_depth += fraction * compartment.thickness;
    }
    Ok(water)
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64;

    use super::evaporation_reduction_coefficient;

    #[test]
    fn stage_two_reduction_matches_reference_exponential() {
        let relative_water = 0.4_f64;
        let decline = 4.0_f64;
        let expected = ((decline * relative_water).exp() - 1.0) / (decline.exp() - 1.0);

        assert_eq_f64!(
            evaporation_reduction_coefficient(relative_water, decline),
            expected
        );
    }

    #[test]
    fn stage_two_reduction_clamps_reference_boundary_regions() {
        assert_eq_f64!(evaporation_reduction_coefficient(0.000_01, 4.0), 0.0_f64);
        assert_eq_f64!(evaporation_reduction_coefficient(0.999_99, 4.0), 1.0_f64);
    }
}
