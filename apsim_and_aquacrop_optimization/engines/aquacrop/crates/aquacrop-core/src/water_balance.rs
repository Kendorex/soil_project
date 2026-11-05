//! Soil drainage, redistribution, and surface-runoff processes.

use crate::Error;
use crate::compat::{EQUIVALENT_WEIGHT, roundc_i8, roundc_i32};
use crate::soil::{PreparedSoilLayer, PreparedSoilProfile, SoilCompartment};

/// Rainfall representation used by the curve-number runoff calculation.
#[derive(Debug, PartialEq, Eq)]
pub enum RainfallAggregation {
    /// One observed daily rainfall depth.
    Daily,
    /// A ten-day or monthly total represented by a configured number of showers.
    Aggregated {
        /// Representative showers per ten-day period.
        showers_per_decade: i32,
    },
}

/// Failure in a soil-water-balance process.
#[derive(Debug, thiserror::Error)]
pub enum WaterBalanceError {
    /// A compartment refers to a soil layer that does not exist.
    #[error("compartment {compartment} refers to missing soil layer {layer}")]
    MissingLayer {
        /// Zero-based compartment index.
        compartment: usize,
        /// Zero-based soil-layer index.
        layer: usize,
    },
    /// The curve number is outside its defined range.
    #[error("curve number must be between 1 and 100, got {0}")]
    InvalidCurveNumber(i8),
    /// Aggregated runoff requires a positive shower count.
    #[error("showers per decade must be positive, got {0}")]
    InvalidShowerCount(i32),
    /// A weighting calculation requires a positive active depth.
    #[error("active soil depth must be positive, got {0}")]
    InvalidActiveDepth(f64),
    /// A parallel state slice does not cover every soil compartment.
    #[error("{state} state has {actual} entries for {expected} soil compartments")]
    StateLengthMismatch {
        /// Name of the incomplete state.
        state: &'static str,
        /// Required number of entries.
        expected: usize,
        /// Supplied number of entries.
        actual: usize,
    },
    /// Positive transpiration demand has no unrestricted rooted extraction capacity.
    #[error("positive transpiration demand requires nonzero rooted extraction capacity")]
    ZeroRootExtractionCapacity,
}

/// Water and salt delivered by capillary rise during one daily step.
#[derive(Debug)]
pub struct CapillaryRise {
    /// Water added to the profile, in millimetres.
    pub water: f64,
    /// Salt added to the profile, in grams per square metre.
    pub salt: f64,
}

/// Configuration needed by the infiltration process for one time step.
#[derive(Debug)]
pub struct InfiltrationParameters {
    /// Representation of the rainfall input.
    pub rainfall_aggregation: RainfallAggregation,
    /// Current rooting depth, in metres. Non-positive values select the evaporation depth.
    pub rooting_depth: f64,
    /// Maximum evaporation depth, in metres.
    pub evaporation_depth: f64,
    /// Bund height, in metres. Values below 0.01 m behave as no bund.
    pub bund_height: f64,
}

/// Mutable water fluxes and surface state used by infiltration.
#[derive(Debug)]
pub struct InfiltrationState {
    /// Rain already selected for infiltration, in millimetres.
    pub infiltrated_rain: f64,
    /// Irrigation already selected for infiltration, in millimetres.
    pub infiltrated_irrigation: f64,
    /// Water released from surface storage for infiltration, in millimetres.
    pub infiltrated_storage: f64,
    /// Effective rainfall routed into the subsoil, in millimetres.
    pub subsoil_drainage: f64,
    /// Accumulated surface runoff, in millimetres.
    pub runoff: f64,
    /// Water remaining behind bunds, in millimetres.
    pub surface_storage: f64,
    /// Water leaving the bottom of the simulated profile, in millimetres.
    pub deep_drainage: f64,
}

/// Calculates the daily decrease in volumetric water content above adjusted field capacity.
///
/// `theta` and `adjusted_field_capacity` are volumetric fractions. Layer fields
/// retain their documented units; the result is a volumetric fraction per day.
#[must_use]
pub fn drainage_delta_theta(
    theta: f64,
    adjusted_field_capacity: f64,
    layer: &PreparedSoilLayer,
) -> f64 {
    let theta = f64::min(theta, layer.saturation / 100.0);
    if theta <= adjusted_field_capacity {
        return 0.0;
    }
    let field_capacity = layer.field_capacity / 100.0;
    let saturation = layer.saturation / 100.0;
    let mut delta = layer.drainage_coefficient
        * (saturation - field_capacity)
        * ((theta - field_capacity).exp() - 1.0)
        / ((saturation - field_capacity).exp() - 1.0);
    if theta - delta < adjusted_field_capacity {
        delta = theta - adjusted_field_capacity;
    }
    delta
}

/// Inverts the drainage relation for a specified volumetric-water-content decrease.
///
/// `delta_theta` and `adjusted_field_capacity` are volumetric fractions. Layer
/// fields retain their documented units; the result is a volumetric fraction.
#[must_use]
pub fn theta_for_drainage(
    delta_theta: f64,
    adjusted_field_capacity: f64,
    layer: &PreparedSoilLayer,
) -> f64 {
    if delta_theta <= f64::EPSILON {
        return adjusted_field_capacity;
    }
    let field_capacity = layer.field_capacity / 100.0;
    let saturation = layer.saturation / 100.0;
    if layer.drainage_coefficient <= 0.0 {
        return saturation + 0.1;
    }
    let theta = field_capacity
        + (1.0
            + delta_theta * ((saturation - field_capacity).exp() - 1.0)
                / (layer.drainage_coefficient * (saturation - field_capacity)))
            .ln();
    f64::max(theta, adjusted_field_capacity)
}

/// Drains and redistributes a complete compartment profile from top to bottom.
///
/// Returns drainage leaving the bottom compartment, in millimetres.
/// Profile and compartment fields retain their field-documented units.
///
/// # Errors
///
/// Returns [`Error`] when a compartment contains an invalid soil-layer index.
pub fn drain_profile(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
) -> Result<f64, Error> {
    let mut drainage_sum = 0.0;
    for compartment_index in 0..compartments.len() {
        let (layer_index, theta, adjusted_fc, thickness) = {
            let compartment = &compartments[compartment_index];
            (
                compartment.layer_index,
                compartment.theta,
                compartment.adjusted_field_capacity / 100.0,
                compartment.thickness,
            )
        };
        let layer = profile
            .layers
            .get(layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: compartment_index,
                layer: layer_index,
            })?;
        let fine_earth = layer.fine_earth_fraction();
        let mut delta_theta = if theta > adjusted_fc {
            drainage_delta_theta(theta, adjusted_fc, layer)
        } else {
            0.0
        };
        let mut compartment_drainage = delta_theta * 1_000.0 * thickness * fine_earth;
        let preceding_thickness: f64 = compartments
            .iter()
            .take(compartment_index)
            .map(|compartment| compartment.thickness)
            .sum();
        let maximum_incoming = delta_theta * 1_000.0 * preceding_thickness * fine_earth;
        let mut excess = 0.0;

        if drainage_sum <= maximum_incoming {
            compartments[compartment_index].theta -= delta_theta;
            drainage_sum += compartment_drainage;
            restrict_drainage(layer, &mut drainage_sum, &mut excess);
        } else {
            delta_theta = drainage_sum / (1_000.0 * preceding_thickness * fine_earth);
            let theta_x = theta_for_drainage(delta_theta, adjusted_fc, layer);
            let saturation = layer.saturation / 100.0;

            if theta_x <= saturation {
                compartments[compartment_index].theta +=
                    drainage_sum / (1_000.0 * thickness * fine_earth);
                let updated_theta = compartments[compartment_index].theta;
                if updated_theta > theta_x {
                    drainage_sum = (updated_theta - theta_x) * 1_000.0 * thickness * fine_earth;
                    delta_theta = drainage_delta_theta(theta_x, adjusted_fc, layer);
                    drainage_sum += delta_theta * 1_000.0 * thickness * fine_earth;
                    restrict_drainage(layer, &mut drainage_sum, &mut excess);
                    compartments[compartment_index].theta = theta_x - delta_theta;
                } else if updated_theta > adjusted_fc {
                    delta_theta = drainage_delta_theta(updated_theta, adjusted_fc, layer);
                    compartments[compartment_index].theta -= delta_theta;
                    drainage_sum = delta_theta * 1_000.0 * thickness * fine_earth;
                    restrict_drainage(layer, &mut drainage_sum, &mut excess);
                } else {
                    drainage_sum = 0.0;
                }
            }

            if theta_x > saturation {
                compartments[compartment_index].theta +=
                    drainage_sum / (1_000.0 * thickness * fine_earth);
                if compartments[compartment_index].theta <= saturation {
                    if compartments[compartment_index].theta > adjusted_fc {
                        delta_theta = drainage_delta_theta(
                            compartments[compartment_index].theta,
                            adjusted_fc,
                            layer,
                        );
                        compartments[compartment_index].theta -= delta_theta;
                        drainage_sum = delta_theta * 1_000.0 * thickness * fine_earth;
                        restrict_drainage(layer, &mut drainage_sum, &mut excess);
                    } else {
                        drainage_sum = 0.0;
                    }
                }
                if compartments[compartment_index].theta > saturation {
                    excess = (compartments[compartment_index].theta - saturation)
                        * 1_000.0
                        * thickness
                        * fine_earth;
                    delta_theta = drainage_delta_theta(
                        compartments[compartment_index].theta,
                        adjusted_fc,
                        layer,
                    );
                    compartments[compartment_index].theta = saturation - delta_theta;
                    compartment_drainage = delta_theta * 1_000.0 * thickness * fine_earth;
                    let maximum_redistribution =
                        (delta_theta * 1_000.0 * preceding_thickness * fine_earth).min(excess);
                    excess -= maximum_redistribution;
                    drainage_sum = maximum_redistribution + compartment_drainage;
                    restrict_drainage(layer, &mut drainage_sum, &mut excess);
                }
            }
        }

        compartments[compartment_index].drainage = drainage_sum;
        redistribute_excess(profile, compartments, compartment_index, &mut excess)?;
    }
    Ok(drainage_sum)
}

fn restrict_drainage(layer: &PreparedSoilLayer, drainage_sum: &mut f64, excess: &mut f64) {
    if *drainage_sum > layer.saturated_hydraulic_conductivity {
        *excess += *drainage_sum - layer.saturated_hydraulic_conductivity;
        *drainage_sum = layer.saturated_hydraulic_conductivity;
    }
}

fn redistribute_excess(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    current_index: usize,
    excess: &mut f64,
) -> Result<(), Error> {
    if *excess <= 0.0 {
        return Ok(());
    }
    for preceding_index in (0..=current_index).rev() {
        let layer_index = compartments[preceding_index].layer_index;
        let layer = profile
            .layers
            .get(layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: preceding_index,
                layer: layer_index,
            })?;
        if preceding_index < current_index {
            compartments[preceding_index].drainage -= *excess;
        }
        compartments[preceding_index].theta += *excess
            / (1_000.0 * compartments[preceding_index].thickness * layer.fine_earth_fraction());
        let saturation = layer.saturation / 100.0;
        if compartments[preceding_index].theta > saturation {
            *excess = (compartments[preceding_index].theta - saturation)
                * 1_000.0
                * compartments[preceding_index].thickness
                * layer.fine_earth_fraction();
            compartments[preceding_index].theta = saturation;
        } else {
            *excess = 0.0;
        }
        if excess.abs() < f64::EPSILON || preceding_index == 0 {
            break;
        }
    }
    Ok(())
}

/// Stores infiltrating rain, irrigation, surface water, and aggregated effective rain.
///
/// This is the water-only infiltration step. Dissolved-salt transport uses the resulting
/// compartment drainage fluxes in the subsequent salt-balance process.
/// Profile, compartment, parameter, and state fields retain their
/// field-documented units; water depths and drainage fluxes are in millimetres.
///
/// # Errors
///
/// Returns [`Error`] when a compartment contains an invalid soil-layer index.
pub fn infiltrate(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    parameters: &InfiltrationParameters,
    state: &mut InfiltrationState,
) -> Result<(), Error> {
    let active_depth = active_root_depth(parameters);
    if !active_depth.is_finite() || active_depth <= 0.0 {
        return Err(WaterBalanceError::InvalidActiveDepth(active_depth).into());
    }
    if compartments.is_empty() {
        return Ok(());
    }
    let mut effective_rain = if parameters.rainfall_aggregation == RainfallAggregation::Daily {
        let amount =
            state.infiltrated_rain + state.infiltrated_irrigation + state.infiltrated_storage;
        store_surface_infiltration(profile, compartments, parameters, state, amount)?;
        0.0
    } else {
        let amount = state.infiltrated_irrigation + state.infiltrated_storage;
        store_surface_infiltration(profile, compartments, parameters, state, amount)?;
        state.infiltrated_rain - state.subsoil_drainage
    };

    store_subsoil_infiltration(
        profile,
        compartments,
        parameters,
        state,
        &mut effective_rain,
    )?;
    store_root_zone_infiltration(profile, compartments, parameters, state, effective_rain)
}

fn store_surface_infiltration(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    parameters: &InfiltrationParameters,
    state: &mut InfiltrationState,
    mut amount: f64,
) -> Result<(), Error> {
    if amount <= 0.0 {
        return Ok(());
    }
    let initial_runoff = state.runoff;
    for compartment_index in 0..compartments.len() {
        let layer_index = compartments[compartment_index].layer_index;
        let layer = profile
            .layers
            .get(layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: compartment_index,
                layer: layer_index,
            })?;
        let storage_factor =
            1_000.0 * compartments[compartment_index].thickness * layer.fine_earth_fraction();
        let saturation = layer.saturation / 100.0;
        let field_capacity = layer.field_capacity / 100.0;
        let saturation_delta = drainage_delta_theta(saturation, field_capacity, layer);
        let factor = if saturation_delta > 0.0 {
            layer.saturated_hydraulic_conductivity / (saturation_delta * storage_factor)
        } else {
            1.0
        };
        let mut target_delta = amount / storage_factor;
        let mut target_theta = if target_delta < saturation_delta {
            let mut theta = theta_for_drainage(target_delta, field_capacity, layer);
            let adjusted_fc = compartments[compartment_index].adjusted_field_capacity / 100.0;
            if theta <= adjusted_fc {
                theta = adjusted_fc;
                target_delta = drainage_delta_theta(theta, field_capacity, layer);
            }
            theta.min(saturation)
        } else {
            target_delta = saturation_delta;
            saturation
        };
        target_theta = target_theta.min(saturation);
        let mut maximum_drainage = factor * target_delta * storage_factor;
        if compartments[compartment_index].drainage + maximum_drainage
            > layer.saturated_hydraulic_conductivity
        {
            maximum_drainage =
                layer.saturated_hydraulic_conductivity - compartments[compartment_index].drainage;
        }

        if target_theta > compartments[compartment_index].theta {
            compartments[compartment_index].theta += amount / storage_factor;
            if compartments[compartment_index].theta > target_theta {
                amount = (compartments[compartment_index].theta - target_theta) * storage_factor;
                compartments[compartment_index].theta = target_theta;
            } else {
                amount = 0.0;
            }
        }
        compartments[compartment_index].drainage += amount;
        let mut excess = (amount - maximum_drainage).max(0.0);
        amount -= excess;
        redistribute_infiltration_excess(profile, compartments, compartment_index, &mut excess)?;
        state.runoff += excess;
        if amount <= f64::EPSILON {
            break;
        }
    }
    if amount > 0.0 {
        state.deep_drainage += amount;
    }
    adjust_infiltration_fluxes(parameters, state, initial_runoff);
    Ok(())
}

fn redistribute_infiltration_excess(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    current_index: usize,
    excess: &mut f64,
) -> Result<(), Error> {
    if *excess <= 0.0 {
        return Ok(());
    }
    for preceding_index in (0..=current_index).rev() {
        let layer_index = compartments[preceding_index].layer_index;
        let layer = profile
            .layers
            .get(layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: preceding_index,
                layer: layer_index,
            })?;
        compartments[preceding_index].drainage -= *excess;
        let storage_factor =
            1_000.0 * compartments[preceding_index].thickness * layer.fine_earth_fraction();
        compartments[preceding_index].theta += *excess / storage_factor;
        let saturation = layer.saturation / 100.0;
        if compartments[preceding_index].theta > saturation {
            *excess = (compartments[preceding_index].theta - saturation) * storage_factor;
            compartments[preceding_index].theta = saturation;
        } else {
            *excess = 0.0;
        }
        if *excess < f64::EPSILON || preceding_index == 0 {
            break;
        }
    }
    Ok(())
}

fn adjust_infiltration_fluxes(
    parameters: &InfiltrationParameters,
    state: &mut InfiltrationState,
    initial_runoff: f64,
) {
    if state.runoff <= initial_runoff {
        return;
    }
    let additional_runoff = state.runoff - initial_runoff;
    if parameters.bund_height >= 0.01 {
        state.surface_storage += additional_runoff;
        state.infiltrated_storage -= additional_runoff;
        let bund_capacity = parameters.bund_height * 1_000.0;
        if state.surface_storage > bund_capacity {
            state.runoff = initial_runoff + state.surface_storage - bund_capacity;
            state.surface_storage = bund_capacity;
        } else {
            state.runoff = initial_runoff;
        }
    } else {
        state.infiltrated_rain -= additional_runoff;
        if state.infiltrated_rain < 0.0 {
            state.infiltrated_irrigation += state.infiltrated_rain;
            state.infiltrated_rain = 0.0;
        }
    }
}

fn store_subsoil_infiltration(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    parameters: &InfiltrationParameters,
    state: &mut InfiltrationState,
    effective_rain: &mut f64,
) -> Result<(), Error> {
    let mut amount = state.subsoil_drainage;
    if amount <= 0.0 {
        return Ok(());
    }
    let root_depth = active_root_depth(parameters);
    let (mut compartment_index, mut partial_depth) = compartment_at_depth(compartments, root_depth);
    while amount > 0.0 && (compartment_index + 1 < compartments.len() || partial_depth > 0.0) {
        if partial_depth.abs() < f64::EPSILON {
            compartment_index += 1;
            partial_depth = compartments[compartment_index].thickness;
        }
        let layer_index = compartments[compartment_index].layer_index;
        let layer = profile
            .layers
            .get(layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: compartment_index,
                layer: layer_index,
            })?;
        let storable = (layer.saturation / 100.0 - compartments[compartment_index].theta)
            * 1_000.0
            * partial_depth
            * layer.fine_earth_fraction();
        let stored = storable.min(amount);
        compartments[compartment_index].theta += stored
            / (1_000.0 * compartments[compartment_index].thickness * layer.fine_earth_fraction());
        amount -= stored;
        partial_depth = 0.0;
        if amount > layer.saturated_hydraulic_conductivity {
            let rejected = amount - layer.saturated_hydraulic_conductivity;
            state.subsoil_drainage -= rejected;
            *effective_rain += rejected;
            amount = layer.saturated_hydraulic_conductivity;
        }
    }
    if amount > 0.0 {
        state.deep_drainage += amount;
    }
    Ok(())
}

fn store_root_zone_infiltration(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    parameters: &InfiltrationParameters,
    state: &mut InfiltrationState,
    mut amount: f64,
) -> Result<(), Error> {
    if amount <= 0.0 {
        return Ok(());
    }
    let root_depth = active_root_depth(parameters);
    let (last_index, _) = compartment_at_depth(compartments, root_depth);
    fill_root_zone_to_field_capacity(profile, compartments, root_depth, last_index, &mut amount)?;
    if amount > 0.0 {
        fill_root_zone_to_saturation(profile, compartments, root_depth, last_index, &mut amount)?;
    }
    if amount > 0.0 {
        if state.infiltrated_rain > 0.0 {
            state.infiltrated_rain -= amount;
        }
        if parameters.bund_height >= 0.01 {
            state.surface_storage += amount;
            let capacity = parameters.bund_height * 1_000.0;
            if state.surface_storage > capacity {
                state.runoff += state.surface_storage - capacity;
                state.surface_storage = capacity;
            }
        } else {
            state.runoff += amount;
        }
    }
    Ok(())
}

fn fill_root_zone_to_field_capacity(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    root_depth: f64,
    last_index: usize,
    amount: &mut f64,
) -> Result<(), Error> {
    let mut depth_before = 0.0;
    for (compartment_index, compartment) in compartments.iter_mut().enumerate().take(last_index + 1)
    {
        let layer_index = compartment.layer_index;
        let layer = profile
            .layers
            .get(layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: compartment_index,
                layer: layer_index,
            })?;
        let active_thickness = (root_depth - depth_before)
            .min(compartment.thickness)
            .max(0.0);
        let limit = compartment.adjusted_field_capacity / 100.0;
        store_to_water_content(compartment, layer, active_thickness, limit, amount);
        if *amount <= f64::EPSILON {
            break;
        }
        depth_before += compartment.thickness;
    }
    Ok(())
}

fn fill_root_zone_to_saturation(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    root_depth: f64,
    last_index: usize,
    amount: &mut f64,
) -> Result<(), Error> {
    let mut compartment_index = last_index;
    let mut cumulative_depth: f64 = compartments
        .iter()
        .take(last_index + 1)
        .map(|compartment| compartment.thickness)
        .sum();
    loop {
        let layer_index = compartments[compartment_index].layer_index;
        let layer = profile
            .layers
            .get(layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: compartment_index,
                layer: layer_index,
            })?;
        let active_thickness = if cumulative_depth > root_depth {
            compartments[compartment_index].thickness - (cumulative_depth - root_depth)
        } else {
            compartments[compartment_index].thickness
        };
        store_to_water_content(
            &mut compartments[compartment_index],
            layer,
            active_thickness,
            layer.saturation / 100.0,
            amount,
        );
        if compartment_index == 0 || *amount <= f64::EPSILON {
            break;
        }
        compartment_index -= 1;
        cumulative_depth -= compartments[compartment_index].thickness;
    }
    Ok(())
}

fn store_to_water_content(
    compartment: &mut SoilCompartment,
    layer: &PreparedSoilLayer,
    active_thickness: f64,
    limit: f64,
    amount: &mut f64,
) {
    let storable =
        ((limit - compartment.theta) * 1_000.0 * active_thickness * layer.fine_earth_fraction())
            .max(0.0);
    let stored = storable.min(*amount);
    compartment.theta += stored / (1_000.0 * compartment.thickness * layer.fine_earth_fraction());
    *amount -= stored;
}

fn active_root_depth(parameters: &InfiltrationParameters) -> f64 {
    if parameters.rooting_depth <= f64::EPSILON {
        parameters.evaporation_depth
    } else {
        parameters.rooting_depth
    }
}

fn compartment_at_depth(compartments: &[SoilCompartment], depth: f64) -> (usize, f64) {
    let mut cumulative_depth = 0.0;
    for (index, compartment) in compartments.iter().enumerate() {
        cumulative_depth += compartment.thickness;
        if cumulative_depth >= depth || index + 1 == compartments.len() {
            return (index, (cumulative_depth - depth).max(0.0));
        }
    }
    (0, 0.0)
}

/// Assigns the reference exponential weighting factors down to an active soil depth.
///
/// `depth` is in metres; compartment fields retain their documented units.
///
/// # Errors
///
/// Returns [`Error`] when the depth is not positive.
pub fn assign_weighting_factors(
    depth: f64,
    compartments: &mut [SoilCompartment],
) -> Result<(), Error> {
    if !depth.is_finite() || depth <= 0.0 {
        return Err(WaterBalanceError::InvalidActiveDepth(depth).into());
    }
    let mut cumulative_depth = 0.0;
    let mut previous_weight = 0.0;
    let mut visited = 0;
    for compartment in compartments.iter_mut() {
        cumulative_depth += compartment.thickness;
        cumulative_depth = cumulative_depth.min(depth);
        let cumulative_weight = 1.016 * (1.0 - (-4.16 * cumulative_depth / depth).exp());
        compartment.weighting_factor = (cumulative_weight - previous_weight).clamp(0.0, 1.0);
        previous_weight = cumulative_weight;
        visited += 1;
        if cumulative_depth >= depth {
            break;
        }
    }
    for compartment in compartments.iter_mut().skip(visited) {
        compartment.weighting_factor = 0.0;
    }
    Ok(())
}

/// Calculates relative wetness of the weighted upper soil profile.
///
/// `depth` is in metres. Profile and compartment fields retain their
/// documented units; the result is a dimensionless fraction.
///
/// # Errors
///
/// Returns [`Error`] for a non-positive depth or an invalid compartment layer index.
pub fn relative_wetness_topsoil(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    depth: f64,
) -> Result<f64, Error> {
    assign_weighting_factors(depth, compartments)?;
    let mut wetness = 0.0;
    let mut cumulative_depth = 0.0;
    for (index, compartment) in compartments.iter().enumerate() {
        let layer =
            profile
                .layers
                .get(compartment.layer_index)
                .ok_or(WaterBalanceError::MissingLayer {
                    compartment: index,
                    layer: compartment.layer_index,
                })?;
        cumulative_depth += compartment.thickness;
        let wilting_point = layer.wilting_point / 100.0;
        let field_capacity = layer.field_capacity / 100.0;
        let theta = compartment.theta.max(wilting_point);
        wetness += compartment.weighting_factor * (theta - wilting_point)
            / (field_capacity - wilting_point);
        if cumulative_depth >= depth {
            break;
        }
    }
    Ok(wetness.clamp(0.0, 1.0))
}

/// Adjusts compartment field capacity for a shallow groundwater table.
///
/// `aquifer_depth` is depth below the soil surface in metres. Profile and
/// compartment fields retain their documented units.
///
/// # Errors
///
/// Returns [`Error`] when a compartment contains an invalid layer index.
pub fn adjust_field_capacity_for_groundwater(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    aquifer_depth: f64,
) -> Result<(), Error> {
    let mut profile_depth: f64 = compartments
        .iter()
        .map(|compartment| compartment.thickness)
        .sum();
    for compartment_index in (0..compartments.len()).rev() {
        let layer_index = compartments[compartment_index].layer_index;
        let layer = profile
            .layers
            .get(layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: compartment_index,
                layer: layer_index,
            })?;
        let midpoint = profile_depth - compartments[compartment_index].thickness / 2.0;
        let maximum_distance = field_capacity_adjustment_distance(layer.field_capacity);
        if aquifer_depth < 0.0 || aquifer_depth - midpoint >= maximum_distance {
            for (upper_index, upper_compartment) in compartments
                .iter_mut()
                .enumerate()
                .take(compartment_index + 1)
            {
                let upper_layer_index = upper_compartment.layer_index;
                let upper_layer = profile.layers.get(upper_layer_index).ok_or(
                    WaterBalanceError::MissingLayer {
                        compartment: upper_index,
                        layer: upper_layer_index,
                    },
                )?;
                upper_compartment.adjusted_field_capacity = upper_layer.field_capacity;
            }
            break;
        }
        compartments[compartment_index].adjusted_field_capacity =
            if layer.field_capacity >= layer.saturation {
                layer.field_capacity
            } else if midpoint >= aquifer_depth {
                layer.saturation
            } else {
                let available_increase = layer.saturation - layer.field_capacity;
                let delta = available_increase / maximum_distance.powi(2)
                    * (midpoint - (aquifer_depth - maximum_distance)).powi(2);
                layer.field_capacity + delta
            };
        profile_depth -= compartments[compartment_index].thickness;
    }
    Ok(())
}

fn field_capacity_adjustment_distance(field_capacity: f64) -> f64 {
    if field_capacity <= 10.0 {
        1.0
    } else if field_capacity >= 30.0 {
        2.0
    } else {
        let exponent = 2.0 + 0.3 * (field_capacity - 10.0) / 20.0;
        (exponent * 10.0_f64.ln()).exp() / 100.0
    }
}

/// Maximum capillary rise at a profile depth, in millimetres per day.
///
/// `profile_depth` and `aquifer_depth` are depths below the soil surface in
/// metres; layer fields retain their documented units.
#[must_use]
pub fn maximum_capillary_rise(
    layer: &PreparedSoilLayer,
    profile_depth: f64,
    aquifer_depth: f64,
) -> f64 {
    let mut maximum = 0.0;
    let Some((capillary_rise_a, capillary_rise_b)) = layer.capillary_rise_parameters else {
        return maximum;
    };
    if layer.saturated_hydraulic_conductivity > 0.0
        && aquifer_depth > 0.0
        && aquifer_depth - profile_depth < 4.0
    {
        maximum = if profile_depth >= aquifer_depth {
            99.0
        } else {
            (((aquifer_depth - profile_depth).ln() - capillary_rise_b) / capillary_rise_a)
                .exp()
                .min(99.0)
        };
    }
    maximum
}

/// Adds groundwater and its dissolved salt from the bottom of the profile upward.
///
/// `aquifer_depth` is metres below the soil surface,
/// `groundwater_conductivity` is decisiemens per metre, and
/// `driving_force_exponent` is dimensionless. Other inputs retain their
/// field-documented units.
///
/// # Errors
///
/// Returns [`Error`] when a compartment contains an invalid layer index.
pub fn apply_capillary_rise(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    aquifer_depth: f64,
    groundwater_conductivity: f64,
    driving_force_exponent: i8,
) -> Result<CapillaryRise, Error> {
    let Some(last_index) = compartments.len().checked_sub(1) else {
        return Ok(CapillaryRise {
            water: 0.0,
            salt: 0.0,
        });
    };
    let mut bottom_depth: f64 = compartments
        .iter()
        .map(|compartment| compartment.thickness)
        .sum();
    let mut maximum = initial_capillary_limit(
        profile,
        compartments,
        last_index,
        bottom_depth,
        aquifer_depth,
    )?;

    let mut result = CapillaryRise {
        water: 0.0,
        salt: 0.0,
    };
    for compartment_index in (0..=last_index).rev() {
        if roundc_i32(maximum * 1_000.0) <= 0
            || roundc_i32(compartments[compartment_index].drainage * 1_000.0) != 0
        {
            break;
        }
        let layer_index = compartments[compartment_index].layer_index;
        let layer = profile
            .layers
            .get(layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: compartment_index,
                layer: layer_index,
            })?;
        let adjusted_field_capacity =
            compartments[compartment_index].adjusted_field_capacity / 100.0;
        let theta = compartments[compartment_index].theta;
        let (driving_force, relative_conductivity) = capillary_driving_factors(
            &compartments[compartment_index],
            layer,
            driving_force_exponent,
        );
        let available_theta = adjusted_field_capacity - theta;
        let midpoint = bottom_depth - compartments[compartment_index].thickness / 2.0;
        if available_theta > 0.0 && midpoint < aquifer_depth {
            let maximum_theta = relative_conductivity * driving_force * maximum
                / (1_000.0 * compartments[compartment_index].thickness);
            let added_water = if available_theta >= maximum_theta {
                compartments[compartment_index].theta += maximum_theta;
                maximum = 0.0;
                maximum_theta
                    * 1_000.0
                    * compartments[compartment_index].thickness
                    * layer.fine_earth_fraction()
            } else {
                compartments[compartment_index].theta = adjusted_field_capacity;
                let water = available_theta
                    * 1_000.0
                    * compartments[compartment_index].thickness
                    * layer.fine_earth_fraction();
                maximum = relative_conductivity * maximum - water;
                water
            };
            result.water += added_water;
            let salt = EQUIVALENT_WEIGHT * added_water * groundwater_conductivity;
            let active_cell = active_salinity_cell(layer, compartments[compartment_index].theta);
            if let Some(cell) = compartments[compartment_index]
                .dissolved_salt
                .get_mut(active_cell)
            {
                *cell += salt;
            }
            result.salt += salt;
        }
        bottom_depth -= compartments[compartment_index].thickness;
        if compartment_index > 0 {
            let next_layer_index = compartments[compartment_index - 1].layer_index;
            let next_layer =
                profile
                    .layers
                    .get(next_layer_index)
                    .ok_or(WaterBalanceError::MissingLayer {
                        compartment: compartment_index - 1,
                        layer: next_layer_index,
                    })?;
            maximum = maximum.min(maximum_capillary_rise(
                next_layer,
                bottom_depth - compartments[compartment_index - 1].thickness / 2.0,
                aquifer_depth,
            ));
        }
    }
    Ok(result)
}

fn initial_capillary_limit(
    profile: &PreparedSoilProfile,
    compartments: &[SoilCompartment],
    last_index: usize,
    bottom_depth: f64,
    aquifer_depth: f64,
) -> Result<f64, Error> {
    let bottom_layer_index = compartments[last_index].layer_index;
    let bottom_layer =
        profile
            .layers
            .get(bottom_layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: last_index,
                layer: bottom_layer_index,
            })?;
    let mut maximum = maximum_capillary_rise(
        bottom_layer,
        bottom_depth - compartments[last_index].thickness / 2.0,
        aquifer_depth,
    );
    let mut next_layer_top: f64 = profile
        .layers
        .iter()
        .take(bottom_layer_index + 1)
        .map(|layer| layer.thickness)
        .sum();
    let mut restriction_layer_index = bottom_layer_index;
    while next_layer_top < aquifer_depth && restriction_layer_index + 1 < profile.layers.len() {
        restriction_layer_index += 1;
        let restriction_layer = &profile.layers[restriction_layer_index];
        maximum = maximum.min(maximum_capillary_rise(
            restriction_layer,
            next_layer_top,
            aquifer_depth,
        ));
        next_layer_top += restriction_layer.thickness;
    }
    Ok(maximum)
}

#[expect(
    clippy::manual_midpoint,
    reason = "addition before division preserves the reference Fortran operation order"
)]
fn capillary_driving_factors(
    compartment: &SoilCompartment,
    layer: &PreparedSoilLayer,
    driving_force_exponent: i8,
) -> (f64, f64) {
    let wilting_point = layer.wilting_point / 100.0;
    let field_capacity = layer.field_capacity / 100.0;
    let adjusted_field_capacity = compartment.adjusted_field_capacity / 100.0;
    let theta = compartment.theta;
    let driving_force = if theta >= wilting_point && driving_force_exponent > 0 {
        1.0 - (f64::from(driving_force_exponent) * (theta - wilting_point).ln()).exp()
            / (f64::from(driving_force_exponent) * (adjusted_field_capacity - wilting_point).ln())
                .exp()
    } else {
        1.0
    };
    let threshold = (wilting_point + field_capacity) / 2.0;
    let relative_conductivity = if theta < threshold {
        if theta <= wilting_point || threshold <= wilting_point {
            0.0
        } else {
            (theta - wilting_point) / (threshold - wilting_point)
        }
    } else {
        1.0
    };
    (driving_force, relative_conductivity)
}

fn active_salinity_cell(layer: &PreparedSoilLayer, theta: f64) -> usize {
    let one_based = if theta <= layer.salinity_upper_limit {
        let mut cell = 1_i32;
        while theta > layer.salinity_cell_width * f64::from(cell) {
            cell += 1;
        }
        cell
    } else {
        i32::from(layer.salinity_cell_count_with_saturation)
    };
    usize::try_from(one_based - 1).unwrap_or(0)
}

/// Derives dry- and wet-antecedent curve numbers from the normal curve number.
///
/// `curve_number` and both returned curve numbers are dimensionless integers.
#[must_use]
pub fn antecedent_curve_numbers(curve_number: i8) -> (i8, i8) {
    let normal_curve_number = curve_number;
    let curve_number = f64::from(normal_curve_number);
    let dry = roundc_i8(
        1.4 * (-14.0 * 10.0_f64.ln()).exp() + 0.507 * curve_number
            - 0.003_74 * curve_number * curve_number
            + 0.000_086_7 * curve_number * curve_number * curve_number,
    )
    .clamp(1, 100);
    let wet = roundc_i8(
        5.6 * (-14.0 * 10.0_f64.ln()).exp() + 2.33 * curve_number
            - 0.020_9 * curve_number * curve_number
            + 0.000_076 * curve_number * curve_number * curve_number,
    )
    .clamp(normal_curve_number.max(1), 100);
    (dry, wet)
}

/// Applies a field-management percentage correction to a normal curve number.
///
/// `curve_number` is dimensionless and `correction` is percent; the
/// result is a dimensionless curve number.
#[must_use]
pub fn corrected_curve_number(curve_number: i8, correction: i32) -> i8 {
    roundc_i8(f64::from(curve_number) * f64::from(100 + correction) / 100.0)
}

/// Interpolates a daily curve number from weighted antecedent soil wetness.
///
/// `curve_number` is dimensionless and `relative_wetness` is a dimensionless
/// fraction; the result is a dimensionless curve number.
#[must_use]
pub fn curve_number_for_wetness(curve_number: i8, relative_wetness: f64) -> i8 {
    let (dry, wet) = antecedent_curve_numbers(curve_number);
    roundc_i8(f64::from(dry) + f64::from(wet - dry) * relative_wetness.clamp(0.0, 1.0))
}

/// Calculates surface runoff using the reference curve-number procedure.
///
/// `rainfall` and the result are millimetres, `curve_number` is
/// dimensionless, and `initial_abstraction` is percent. Aggregation
/// fields retain their documented units.
///
/// # Errors
///
/// Returns [`Error`] for an invalid curve number or non-positive aggregated shower count.
pub fn runoff(
    rainfall: f64,
    curve_number: i8,
    initial_abstraction: i8,
    aggregation: &RainfallAggregation,
) -> Result<f64, Error> {
    if !(1..=100).contains(&curve_number) {
        return Err(WaterBalanceError::InvalidCurveNumber(curve_number).into());
    }
    let (shower, showers_per_decade) = match aggregation {
        RainfallAggregation::Daily => (rainfall, None),
        RainfallAggregation::Aggregated { showers_per_decade } => {
            if *showers_per_decade <= 0 {
                return Err(WaterBalanceError::InvalidShowerCount(*showers_per_decade).into());
            }
            (
                rainfall * 10.0 / f64::from(*showers_per_decade),
                Some(*showers_per_decade),
            )
        }
    };
    let storage = 254.0 * (100.0 / f64::from(curve_number) - 1.0);
    let abstraction_fraction = f64::from(initial_abstraction) / 100.0;
    let term = shower - abstraction_fraction * storage;
    let mut runoff = if term <= f64::EPSILON {
        0.0
    } else {
        term.powi(2) / (shower + (1.0 - abstraction_fraction) * storage)
    };
    if let Some(showers) = showers_per_decade
        && runoff > 0.0
    {
        if runoff >= shower {
            runoff = rainfall;
        } else {
            runoff *= f64::from(showers) / 10.14;
            runoff = runoff.min(rainfall);
        }
    }
    Ok(runoff)
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64;

    use super::*;

    #[test]
    fn antecedent_curve_numbers_match_reference_polynomials() {
        assert_eq!(antecedent_curve_numbers(46), (24, 70));
    }

    #[test]
    fn rainfall_below_initial_abstraction_has_no_runoff() {
        let value = runoff(1.0, 46, 5, &RainfallAggregation::Daily)
            .expect("the runoff inputs should be valid");
        assert_eq_f64!(value, 0.0_f64);
    }
}
