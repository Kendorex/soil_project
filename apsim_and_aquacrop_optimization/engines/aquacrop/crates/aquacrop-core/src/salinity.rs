//! Dissolved and deposited soil-salt state and concentration calculations.

use crate::Error;
use crate::compat::{EQUIVALENT_WEIGHT, roundc_i32};
use crate::et::temperature_stress_coefficient;
use crate::soil::{PreparedSoilLayer, PreparedSoilProfile, SoilCompartment};
use crate::stress::stress_coefficient;
use crate::water_balance::{InfiltrationState, WaterBalanceError};

/// Water-quality inputs used by daily salt transport.
#[derive(Debug)]
pub struct SaltTransportParameters {
    /// Electrical conductivity of infiltrating irrigation, in dS/m.
    pub irrigation_conductivity: f64,
    /// Electrical conductivity of infiltrating surface storage, in dS/m.
    pub surface_storage_conductivity: f64,
    /// Current rooting depth, in metres.
    pub rooting_depth: f64,
    /// Maximum evaporation depth, in metres.
    pub evaporation_depth: f64,
    /// Maximum dissolved salt concentration, in g/l.
    pub solubility: f64,
}

/// Salt entering and leaving the profile during infiltration and drainage.
#[derive(Debug)]
pub struct SaltTransport {
    /// Salt added with irrigation and surface storage, in g/m2.
    pub infiltrated_salt: f64,
    /// Salt leaving the bottom of the profile, in g/m2.
    pub drained_salt: f64,
    /// Electrical conductivity of bottom drainage, in dS/m.
    pub drainage_conductivity: f64,
}

/// Water and salt supplied horizontally by a groundwater table.
#[derive(Debug)]
pub struct GroundwaterInflow {
    /// Water added to saturated compartments, in millimetres.
    pub water: f64,
    /// Change in profile salt required to match groundwater quality, in g/m2.
    pub salt: f64,
}

/// Salt concentrations and crop response integrated over the root zone.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct RootZoneSalinity {
    /// Saturated-paste extract electrical conductivity, in dS/m.
    pub extract_conductivity: f64,
    /// Soil-water electrical conductivity at current water content, in dS/m.
    pub water_conductivity: f64,
    /// Soil-water electrical conductivity at field capacity, in dS/m.
    pub field_capacity_conductivity: f64,
    /// Crop salinity-stress coefficient as a dimensionless fraction.
    pub stress_coefficient: f64,
}

/// Crop thresholds controlling the salinity-stress coefficient.
#[derive(Debug)]
pub struct SalinityResponse {
    /// Saturated-paste extract conductivity where stress begins, in dS/m.
    pub minimum: Option<i8>,
    /// Saturated-paste extract conductivity where growth ceases, in dS/m.
    pub maximum: Option<i8>,
    /// Dimensionless response shape; zero is linear and 99 is logistic.
    pub shape: f64,
}

/// Total dissolved and deposited salt in a compartment, in grams per square metre.
#[must_use]
pub fn total_salt(compartment: &SoilCompartment, layer: &PreparedSoilLayer) -> f64 {
    let count = salinity_cell_count(layer);
    compartment.dissolved_salt[..count]
        .iter()
        .chain(&compartment.deposited_salt[..count])
        .sum()
}

/// Zero-based salt cell containing the compartment's current water content.
#[must_use]
pub fn active_salinity_cell(compartment: &SoilCompartment, layer: &PreparedSoilLayer) -> usize {
    if compartment.theta <= layer.salinity_upper_limit {
        let mut cell = 1_i8;
        while compartment.theta > layer.salinity_cell_width * f64::from(cell) {
            cell += 1;
        }
        usize::try_from(cell.saturating_sub(1))
            .unwrap_or(0)
            .min(salinity_cell_count(layer) - 1)
    } else {
        salinity_cell_count(layer) - 1
    }
}

/// Electrical conductivity of soil water at current water content, in dS/m.
///
/// `solubility` is salt solubility in grams per litre. Compartment and
/// layer fields retain their documented units.
#[must_use]
pub fn soil_water_conductivity(
    compartment: &SoilCompartment,
    layer: &PreparedSoilLayer,
    solubility: f64,
) -> f64 {
    let water = layer.water_storage(compartment.theta, compartment.thickness);
    concentration_as_conductivity(total_salt(compartment, layer), water, solubility)
}

/// Electrical conductivity of the saturated-paste extract, in dS/m.
///
/// `solubility` is salt solubility in grams per litre. Compartment and
/// layer fields retain their documented units.
#[must_use]
pub fn saturated_extract_conductivity(
    compartment: &SoilCompartment,
    layer: &PreparedSoilLayer,
    solubility: f64,
) -> f64 {
    let saturated_water = layer.water_storage(layer.saturation / 100.0, compartment.thickness);
    concentration_as_conductivity(total_salt(compartment, layer), saturated_water, solubility)
}

/// Electrical conductivity of soil water at field capacity, in dS/m.
///
/// `solubility` is salt solubility in grams per litre. Compartment and layer
/// fields retain their documented units.
#[must_use]
pub fn field_capacity_water_conductivity(
    compartment: &SoilCompartment,
    layer: &PreparedSoilLayer,
    solubility: f64,
) -> f64 {
    let water = layer.water_storage(layer.field_capacity / 100.0, compartment.thickness);
    concentration_as_conductivity(total_salt(compartment, layer), water, solubility)
}

/// Calculates the crop response to saturated-paste extract salinity.
///
/// Defined thresholds and `conductivity` are electrical conductivities in dS/m,
/// and `shape` is dimensionless. Missing thresholds disable salinity stress.
/// The result is a dimensionless coefficient from zero for complete stress to
/// one for no stress. A shape of zero selects the linear reference response;
/// 99 selects its logistic response.
#[must_use]
pub fn salinity_stress_coefficient(
    minimum: Option<i8>,
    maximum: Option<i8>,
    conductivity: f64,
    shape: f64,
) -> f64 {
    let (Some(minimum), Some(maximum)) = (minimum, maximum) else {
        return 1.0;
    };
    if minimum >= maximum {
        return 1.0;
    }
    if conductivity <= f64::from(minimum) {
        return 1.0;
    }
    if conductivity >= f64::from(maximum) {
        return 0.0;
    }
    if roundc_i32(shape * 10.0) == 990 {
        temperature_stress_coefficient(
            Some(f64::from(maximum)),
            Some(f64::from(minimum)),
            conductivity,
        )
    } else if roundc_i32(shape * 10.0) == 0 {
        1.0 - (conductivity - f64::from(minimum)) / f64::from(maximum - minimum)
    } else {
        stress_coefficient(conductivity, f64::from(minimum), f64::from(maximum), shape)
    }
}

/// Adjusts a stomatal-stress coefficient for soil-water salinity.
///
/// `extract_conductivity`, `water_conductivity`, and
/// `field_capacity_conductivity` are in dS/m. `relative_depletion` and
/// `incoming_coefficient` are dimensionless. Calibration coefficients map
/// salinity-stress percent to stomatal-closure percent. The result is a
/// dimensionless coefficient no greater than `incoming_coefficient`.
#[must_use]
#[expect(
    clippy::too_many_arguments,
    reason = "the arguments reproduce the auditable source equation without hidden state"
)]
pub fn adjust_stomatal_for_soil_water_salinity(
    minimum: Option<i8>,
    maximum: Option<i8>,
    response: i32,
    extract_conductivity: f64,
    water_conductivity: f64,
    field_capacity_conductivity: f64,
    relative_depletion: f64,
    reduction_intercept: f64,
    reduction_linear: f64,
    reduction_quadratic: f64,
    incoming_coefficient: f64,
) -> f64 {
    let (Some(minimum), Some(maximum)) = (minimum, maximum) else {
        return incoming_coefficient;
    };
    if response <= 0 || relative_depletion <= f64::EPSILON {
        return incoming_coefficient;
    }
    let relative_conductivity = water_conductivity
        - (field_capacity_conductivity - extract_conductivity)
        + f64::from(response - 100) * relative_depletion;
    if relative_conductivity >= f64::from(maximum) {
        return 0.0;
    }
    if relative_conductivity <= f64::from(minimum) {
        return incoming_coefficient;
    }
    let salt_coefficient =
        salinity_stress_coefficient(Some(minimum), Some(maximum), relative_conductivity, 3.0);
    let stress = 100.0 * (1.0 - salt_coefficient);
    let closure =
        reduction_intercept + reduction_linear * stress + reduction_quadratic * stress * stress;
    (1.0 - closure / 100.0).max(0.0).min(incoming_coefficient)
}

/// Integrates soil salinity over the active root depth.
///
/// `rooting_depth` and `minimum_rooting_depth` are in metres, `solubility` is
/// grams per litre, and other inputs retain their documented units. `None` is
/// returned while the active root depth is below the crop's minimum rooting
/// depth.
///
/// # Errors
///
/// Returns [`Error`] when a compartment contains an invalid soil-layer index.
pub fn determine_root_zone_salinity(
    profile: &PreparedSoilProfile,
    compartments: &[SoilCompartment],
    rooting_depth: f64,
    minimum_rooting_depth: f64,
    response: &SalinityResponse,
    solubility: f64,
) -> Result<Option<RootZoneSalinity>, Error> {
    if rooting_depth <= 0.0 || rooting_depth < minimum_rooting_depth {
        return Ok(None);
    }
    let mut result = RootZoneSalinity {
        extract_conductivity: 0.0,
        water_conductivity: 0.0,
        field_capacity_conductivity: 0.0,
        stress_coefficient: 1.0,
    };
    let mut cumulative_depth = 0.0;
    for (index, compartment) in compartments.iter().enumerate() {
        cumulative_depth += compartment.thickness;
        let active_depth = if cumulative_depth <= rooting_depth {
            compartment.thickness
        } else {
            (rooting_depth - (cumulative_depth - compartment.thickness)).max(0.0)
        };
        let weight = active_depth / rooting_depth;
        let layer =
            profile
                .layers
                .get(compartment.layer_index)
                .ok_or(WaterBalanceError::MissingLayer {
                    compartment: index,
                    layer: compartment.layer_index,
                })?;
        result.extract_conductivity +=
            weight * saturated_extract_conductivity(compartment, layer, solubility);
        result.water_conductivity +=
            weight * soil_water_conductivity(compartment, layer, solubility);
        result.field_capacity_conductivity +=
            weight * field_capacity_water_conductivity(compartment, layer, solubility);
        if cumulative_depth >= rooting_depth {
            break;
        }
    }
    result.stress_coefficient = salinity_stress_coefficient(
        response.minimum,
        response.maximum,
        result.extract_conductivity,
        response.shape,
    );
    Ok(Some(result))
}

fn concentration_as_conductivity(salt: f64, water: f64, solubility: f64) -> f64 {
    (salt / water).min(solubility) / EQUIVALENT_WEIGHT
}

/// Reconciles dissolved and deposited salt at a specified cell-water volume.
///
/// `water` is millimetres of water, both salt masses are grams per square
/// metre, and `solubility` is grams per litre.
pub fn reconcile_solution_and_deposit(
    water: f64,
    dissolved: &mut f64,
    deposited: &mut f64,
    solubility: f64,
) {
    *dissolved += *deposited;
    let dissolved_limit = solubility * water;
    if *dissolved > dissolved_limit {
        *deposited = *dissolved - dissolved_limit;
        *dissolved = dissolved_limit;
    } else {
        *deposited = 0.0;
    }
}

/// Initializes a compartment's cell masses from saturated-extract conductivity.
///
/// `conductivity` is in decisiemens per metre and
/// `solubility` is grams per litre. Compartment and layer fields retain
/// their documented units.
pub fn initialize_salt_from_extract_conductivity(
    compartment: &mut SoilCompartment,
    layer: &PreparedSoilLayer,
    conductivity: f64,
    solubility: f64,
) {
    let total_salt =
        conductivity * EQUIVALENT_WEIGHT * layer.saturation * 10.0 * compartment.thickness;
    let active_count = active_salinity_cell(compartment, layer) + 1;
    let mobility_sum: f64 = layer.salt_mobility[..active_count].iter().sum();
    let cell_count = salinity_cell_count(layer);
    compartment.dissolved_salt[..cell_count].fill(0.0);
    compartment.deposited_salt[..cell_count].fill(0.0);
    for cell in 0..active_count {
        compartment.dissolved_salt[cell] = total_salt * layer.salt_mobility[cell] / mobility_sum;
        let water = salinity_cell_water(layer, compartment.thickness, cell);
        reconcile_solution_and_deposit(
            water,
            &mut compartment.dissolved_salt[cell],
            &mut compartment.deposited_salt[cell],
            solubility,
        );
    }
}

/// Saturates compartments at or below a groundwater table before daily accounting.
///
/// `aquifer_depth` is metres below the soil surface; a negative value means
/// that no groundwater table is present. `conductivity` is in decisiemens per
/// metre, and `solubility` is grams per litre. Profile and compartment fields
/// retain their documented units.
///
/// # Errors
///
/// Returns [`Error`] when a compartment contains an invalid soil-layer index.
pub fn initialize_groundwater_profile(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    aquifer_depth: f64,
    conductivity: f64,
    solubility: f64,
) -> Result<(), Error> {
    if aquifer_depth < 0.0 {
        return Ok(());
    }
    let mut bottom_depth = 0.0;
    for (index, compartment) in compartments.iter_mut().enumerate() {
        bottom_depth += compartment.thickness;
        let midpoint = bottom_depth - compartment.thickness / 2.0;
        if midpoint < aquifer_depth {
            continue;
        }
        let layer =
            profile
                .layers
                .get(compartment.layer_index)
                .ok_or(WaterBalanceError::MissingLayer {
                    compartment: index,
                    layer: compartment.layer_index,
                })?;
        compartment.theta = layer.saturation / 100.0;
        initialize_salt_from_extract_conductivity(compartment, layer, conductivity, solubility);
    }
    Ok(())
}

/// Restores saturation and groundwater salinity after evaporation and transpiration.
///
/// `aquifer_depth` is metres below the soil surface; a negative value means
/// that no groundwater table is present. `conductivity` is in decisiemens per
/// metre, and `solubility` is grams per litre. Returned water is in millimetres
/// and returned salt is in grams per square metre.
///
/// # Errors
///
/// Returns [`Error`] when a compartment contains an invalid soil-layer index.
pub fn horizontal_groundwater_inflow(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    aquifer_depth: f64,
    conductivity: f64,
    solubility: f64,
) -> Result<GroundwaterInflow, Error> {
    let mut result = GroundwaterInflow {
        water: 0.0,
        salt: 0.0,
    };
    if aquifer_depth < 0.0 {
        return Ok(result);
    }
    let mut bottom_depth = 0.0;
    for (index, compartment) in compartments.iter_mut().enumerate() {
        bottom_depth += compartment.thickness;
        let midpoint = bottom_depth - compartment.thickness / 2.0;
        if midpoint < aquifer_depth {
            continue;
        }
        let layer =
            profile
                .layers
                .get(compartment.layer_index)
                .ok_or(WaterBalanceError::MissingLayer {
                    compartment: index,
                    layer: compartment.layer_index,
                })?;
        let saturation = layer.saturation / 100.0;
        if compartment.theta < saturation {
            result.water +=
                layer.water_storage(saturation - compartment.theta, compartment.thickness);
            compartment.theta = saturation;
        }
        if (saturated_extract_conductivity(compartment, layer, solubility) - conductivity).abs()
            > 0.000_1
        {
            let previous_salt = total_salt(compartment, layer);
            initialize_salt_from_extract_conductivity(compartment, layer, conductivity, solubility);
            result.salt += total_salt(compartment, layer) - previous_salt;
        }
    }
    Ok(result)
}

/// Transports salt with infiltration and drainage, then diffuses it vertically.
///
/// Profile, compartment, infiltration, and parameter fields retain their
/// field-documented units. Returned salt masses are in grams per square metre.
///
/// # Errors
///
/// Returns [`Error`] when a compartment contains an invalid soil-layer index.
pub fn transport_salts(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    infiltration: &InfiltrationState,
    parameters: &SaltTransportParameters,
) -> Result<SaltTransport, Error> {
    let mut incoming_water = infiltration.infiltrated_rain
        + infiltration.infiltrated_irrigation
        + infiltration.infiltrated_storage;
    let infiltrated_salt = EQUIVALENT_WEIGHT
        * (infiltration.infiltrated_irrigation * parameters.irrigation_conductivity
            + infiltration.infiltrated_storage * parameters.surface_storage_conductivity);
    let mut incoming_salt = infiltrated_salt;
    let mut drained_salt = 0.0;

    for (compartment_index, compartment) in compartments.iter_mut().enumerate() {
        let layer =
            profile
                .layers
                .get(compartment.layer_index)
                .ok_or(WaterBalanceError::MissingLayer {
                    compartment: compartment_index,
                    layer: compartment.layer_index,
                })?;
        let storage_factor = 1_000.0 * compartment.thickness * layer.fine_earth_fraction();
        let infiltration_delta = incoming_water / storage_factor;
        let theta_before_drainage = compartment.theta - infiltration_delta
            + compartment.drainage / (1_000.0 * compartment.thickness);
        let theta_after_infiltration = theta_before_drainage + infiltration_delta;
        let mut active_cell = active_cell_for_theta(theta_after_infiltration, layer);
        if infiltration_delta > 0.0 {
            compartment.dissolved_salt[active_cell] += incoming_salt;
        }
        mix_active_cells(
            compartment,
            layer,
            active_cell,
            theta_after_infiltration,
            parameters.solubility,
        );
        drained_salt = drain_salt(
            compartment,
            layer,
            &mut active_cell,
            theta_after_infiltration,
            parameters.solubility,
        );
        incoming_water = compartment.drainage;
        incoming_salt = drained_salt;
    }

    diffuse_salts(profile, compartments, parameters.solubility)?;
    drained_salt = move_subsoil_salt(
        profile,
        compartments,
        infiltration,
        parameters,
        drained_salt,
    )?;
    let drainage_conductivity = if infiltration.deep_drainage > 0.001 {
        drained_salt / (infiltration.deep_drainage * EQUIVALENT_WEIGHT)
    } else {
        0.0
    };
    Ok(SaltTransport {
        infiltrated_salt,
        drained_salt,
        drainage_conductivity,
    })
}

fn mix_active_cells(
    compartment: &mut SoilCompartment,
    layer: &PreparedSoilLayer,
    active_cell: usize,
    theta: f64,
    solubility: f64,
) {
    for cell in 0..active_cell {
        let first_water = regular_salinity_cell_water(layer, compartment.thickness);
        let second_water = if cell + 1 < usize::try_from(layer.salinity_cell_count).unwrap_or(0) {
            first_water
        } else if theta > layer.saturation / 100.0 {
            (theta - layer.salinity_upper_limit)
                * 1_000.0
                * compartment.thickness
                * layer.fine_earth_fraction()
        } else {
            (layer.saturation / 100.0 - layer.salinity_upper_limit)
                * 1_000.0
                * compartment.thickness
                * layer.fine_earth_fraction()
        };
        mix_cell_pair(
            compartment,
            layer,
            cell,
            first_water,
            second_water,
            solubility,
        );
    }
}

fn mix_cell_pair(
    compartment: &mut SoilCompartment,
    layer: &PreparedSoilLayer,
    cell: usize,
    first_water: f64,
    second_water: f64,
    solubility: f64,
) {
    let (first_salt, second_salt) = compartment.dissolved_salt.split_at_mut(cell + 1);
    let (first_deposit, second_deposit) = compartment.deposited_salt.split_at_mut(cell + 1);
    reconcile_solution_and_deposit(
        first_water,
        &mut first_salt[cell],
        &mut first_deposit[cell],
        solubility,
    );
    reconcile_solution_and_deposit(
        second_water,
        &mut second_salt[0],
        &mut second_deposit[0],
        solubility,
    );
    let first_ec = first_salt[cell] / (first_water * EQUIVALENT_WEIGHT);
    let second_ec = second_salt[0] / (second_water * EQUIVALENT_WEIGHT);
    let mixed_ec =
        (first_ec * first_water + second_ec * second_water) / (first_water + second_water);
    let mobility = layer.salt_mobility[cell];
    first_salt[cell] =
        (first_ec + (mixed_ec - first_ec) * mobility) * first_water * EQUIVALENT_WEIGHT;
    second_salt[0] =
        (second_ec + (mixed_ec - second_ec) * mobility) * second_water * EQUIVALENT_WEIGHT;
    reconcile_solution_and_deposit(
        first_water,
        &mut first_salt[cell],
        &mut first_deposit[cell],
        solubility,
    );
    reconcile_solution_and_deposit(
        second_water,
        &mut second_salt[0],
        &mut second_deposit[0],
        solubility,
    );
}

fn drain_salt(
    compartment: &mut SoilCompartment,
    layer: &PreparedSoilLayer,
    active_cell: &mut usize,
    mut theta: f64,
    solubility: f64,
) -> f64 {
    if compartment.drainage <= 0.0 {
        return 0.0;
    }
    let storage_factor = 1_000.0 * compartment.thickness * layer.fine_earth_fraction();
    let mut remaining_delta = compartment.drainage / storage_factor;
    let mut salt_out = 0.0;
    while remaining_delta > 0.0 {
        let limit = if *active_cell + 1 < salinity_cell_count(layer) {
            f64::from(i32::try_from(*active_cell).unwrap_or(0)) * layer.salinity_cell_width
        } else {
            layer.salinity_upper_limit
        };
        let cell_span = theta - limit;
        if remaining_delta <= cell_span {
            let fraction = remaining_delta / cell_span;
            let cell_total =
                compartment.dissolved_salt[*active_cell] + compartment.deposited_salt[*active_cell];
            salt_out += cell_total * fraction;
            compartment.dissolved_salt[*active_cell] *= 1.0 - fraction;
            compartment.deposited_salt[*active_cell] *= 1.0 - fraction;
            limit_exported_salt(
                compartment,
                *active_cell,
                &mut salt_out,
                remaining_delta * storage_factor,
                solubility,
            );
            remaining_delta = 0.0;
            let water = salinity_cell_water(layer, compartment.thickness, *active_cell);
            reconcile_solution_and_deposit(
                water,
                &mut compartment.dissolved_salt[*active_cell],
                &mut compartment.deposited_salt[*active_cell],
                solubility,
            );
        } else {
            salt_out +=
                compartment.dissolved_salt[*active_cell] + compartment.deposited_salt[*active_cell];
            compartment.dissolved_salt[*active_cell] = 0.0;
            compartment.deposited_salt[*active_cell] = 0.0;
            let exported_water = cell_span * storage_factor;
            limit_exported_salt(
                compartment,
                *active_cell,
                &mut salt_out,
                exported_water,
                solubility,
            );
            remaining_delta -= cell_span;
            theta = limit;
            if *active_cell == 0 {
                break;
            }
            *active_cell -= 1;
        }
    }
    salt_out
}

fn limit_exported_salt(
    compartment: &mut SoilCompartment,
    cell: usize,
    salt_out: &mut f64,
    exported_water: f64,
    solubility: f64,
) {
    let limit = solubility * exported_water;
    if *salt_out > limit {
        compartment.deposited_salt[cell] += *salt_out - limit;
        *salt_out = limit;
    } else if compartment.dissolved_salt[cell] == 0.0 {
        compartment.deposited_salt[cell] = 0.0;
    }
}

fn diffuse_salts(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    solubility: f64,
) -> Result<(), Error> {
    let Some(first) = compartments.first() else {
        return Ok(());
    };
    let first_layer =
        profile
            .layers
            .get(first.layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: 0,
                layer: first.layer_index,
            })?;
    let mut upper_cell = active_salinity_cell(first, first_layer);
    let mut upper_water = first_layer.water_storage(first.theta, first.thickness);
    let mut upper_ec = soil_water_conductivity(first, first_layer, solubility);
    let mut upper_mobility = first_layer.salt_mobility[upper_cell] / 4.0;
    for lower_index in 1..compartments.len() {
        let upper_index = lower_index - 1;
        let upper_layer_index = compartments[upper_index].layer_index;
        let lower_layer_index = compartments[lower_index].layer_index;
        let upper_layer =
            profile
                .layers
                .get(upper_layer_index)
                .ok_or(WaterBalanceError::MissingLayer {
                    compartment: upper_index,
                    layer: upper_layer_index,
                })?;
        let lower_layer =
            profile
                .layers
                .get(lower_layer_index)
                .ok_or(WaterBalanceError::MissingLayer {
                    compartment: lower_index,
                    layer: lower_layer_index,
                })?;
        let lower_cell = active_salinity_cell(&compartments[lower_index], lower_layer);
        let lower_water = lower_layer.water_storage(
            compartments[lower_index].theta,
            compartments[lower_index].thickness,
        );
        let lower_ec = soil_water_conductivity(&compartments[lower_index], lower_layer, solubility);
        let mixed_ec =
            (upper_ec * upper_water + lower_ec * lower_water) / (upper_water + lower_water);
        let lower_mobility = lower_layer.salt_mobility[lower_cell] / 4.0;
        let upper_change = (upper_ec - (upper_ec + (mixed_ec - upper_ec) * upper_mobility))
            * upper_water
            * EQUIVALENT_WEIGHT;
        let lower_change = (lower_ec - (lower_ec + (mixed_ec - lower_ec) * lower_mobility))
            * lower_water
            * EQUIVALENT_WEIGHT;
        let mut transfer = upper_change.abs().min(lower_change.abs());
        if transfer > 0.0 {
            if upper_ec > mixed_ec {
                transfer = -transfer;
            }
            let (upper_compartments, lower_compartments) = compartments.split_at_mut(lower_index);
            move_salt(
                &mut upper_compartments[upper_index],
                upper_layer,
                upper_cell,
                transfer,
                solubility,
            );
            move_salt(
                &mut lower_compartments[0],
                lower_layer,
                lower_cell,
                -transfer,
                solubility,
            );
        }
        upper_cell = lower_cell;
        upper_water = lower_water;
        upper_ec = lower_ec;
        upper_mobility = lower_mobility;
    }
    Ok(())
}

fn move_salt(
    compartment: &mut SoilCompartment,
    layer: &PreparedSoilLayer,
    active_cell: usize,
    delta: f64,
    solubility: f64,
) {
    if delta >= f64::EPSILON {
        compartment.dissolved_salt[active_cell] += delta;
        let water = salinity_cell_water(layer, compartment.thickness, active_cell);
        reconcile_solution_and_deposit(
            water,
            &mut compartment.dissolved_salt[active_cell],
            &mut compartment.deposited_salt[active_cell],
            solubility,
        );
        return;
    }
    let mut cell = salinity_cell_count(layer) - 1;
    compartment.dissolved_salt[cell] += delta;
    loop {
        let water = salinity_cell_water(layer, compartment.thickness, cell);
        reconcile_solution_and_deposit(
            water,
            &mut compartment.dissolved_salt[cell],
            &mut compartment.deposited_salt[cell],
            solubility,
        );
        if compartment.dissolved_salt[cell] >= 0.0 || cell == 0 {
            break;
        }
        let deficit = compartment.dissolved_salt[cell];
        compartment.dissolved_salt[cell] = 0.0;
        cell -= 1;
        compartment.dissolved_salt[cell] += deficit;
    }
}

fn active_cell_for_theta(theta: f64, layer: &PreparedSoilLayer) -> usize {
    if theta <= layer.salinity_upper_limit {
        let mut one_based = 0_i8;
        while theta > layer.salinity_cell_width * f64::from(one_based) {
            one_based += 1;
        }
        usize::try_from(one_based.max(1) - 1).unwrap_or(0)
    } else {
        salinity_cell_count(layer) - 1
    }
}

/// Moves dissolved salt toward the surface after stage-II evaporation.
///
/// Returns the salt mass moved into the first compartment, in g/m2.
/// `initial_theta` contains volumetric water-content fractions and
/// `maximum_extraction_depth` is in metres; other fields retain their
/// documented units.
///
/// # Errors
///
/// Returns [`Error`] for incomplete initial-water state or invalid layer indices.
pub fn transport_salt_upward_after_evaporation(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    initial_theta: &[f64],
    maximum_extraction_depth: f64,
) -> Result<f64, Error> {
    if initial_theta.len() < compartments.len() {
        return Err(WaterBalanceError::StateLengthMismatch {
            state: "initial evaporation water content",
            expected: compartments.len(),
            actual: initial_theta.len(),
        }
        .into());
    }
    let Some(first) = compartments.first() else {
        return Ok(0.0);
    };
    let first_layer =
        profile
            .layers
            .get(first.layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: 0,
                layer: first.layer_index,
            })?;
    let factor = salt_evaporation_transport_factor(first.theta, first_layer);
    if factor <= 0.01 {
        return Ok(0.0);
    }
    let receiving_cell = active_salinity_cell(first, first_layer);
    let included_count =
        evaporation_snapshot_compartment_count(compartments, maximum_extraction_depth);
    let included_depth: f64 = compartments
        .iter()
        .take(included_count)
        .map(|compartment| compartment.thickness)
        .sum();
    let mut moved_total = 0.0;
    let mut cumulative_depth = compartments[0].thickness;
    for compartment_index in 1..included_count {
        cumulative_depth += compartments[compartment_index].thickness;
        if roundc_i32(cumulative_depth * 100.0) > roundc_i32(included_depth * 100.0)
            || roundc_i32(initial_theta[compartment_index] * 100_000.0)
                == roundc_i32(compartments[compartment_index].theta * 100_000.0)
        {
            continue;
        }
        let layer_index = compartments[compartment_index].layer_index;
        let layer = profile
            .layers
            .get(layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: compartment_index,
                layer: layer_index,
            })?;
        let final_cell = active_salinity_cell(&compartments[compartment_index], layer);
        let mut initial_cell = active_cell_for_theta(initial_theta[compartment_index], layer);
        let mut cell_initial_theta = initial_theta[compartment_index];
        loop {
            let displaced = if final_cell < initial_cell {
                factor * compartments[compartment_index].dissolved_salt[initial_cell]
            } else {
                let lower_limit = if final_cell + 1 == salinity_cell_count(layer) {
                    layer.salinity_upper_limit
                } else {
                    layer.salinity_cell_width * f64::from(i32::try_from(final_cell).unwrap_or(0))
                };
                factor
                    * compartments[compartment_index].dissolved_salt[initial_cell]
                    * (cell_initial_theta - compartments[compartment_index].theta)
                    / (cell_initial_theta - lower_limit)
            };
            compartments[compartment_index].dissolved_salt[initial_cell] -= displaced;
            compartments[0].dissolved_salt[receiving_cell] += displaced;
            moved_total += displaced;
            if final_cell >= initial_cell {
                break;
            }
            initial_cell -= 1;
            cell_initial_theta =
                layer.salinity_cell_width * f64::from(i32::try_from(initial_cell + 1).unwrap_or(0));
        }
    }
    Ok(moved_total)
}

/// Fraction of evaporatively displaced dissolved salt moved upward.
///
/// `theta` is volumetric water content as a fraction; layer fields retain their
/// documented units and the result is dimensionless.
#[must_use]
pub fn salt_evaporation_transport_factor(theta: f64, layer: &PreparedSoilLayer) -> f64 {
    if theta <= layer.wilting_point / 200.0 {
        0.0
    } else {
        let relative = (theta * 100.0 - layer.wilting_point / 2.0)
            / (layer.saturation - layer.wilting_point / 2.0);
        (relative * 10.0_f64.ln() + (relative / 10.0).ln()).exp()
    }
}

fn evaporation_snapshot_compartment_count(
    compartments: &[SoilCompartment],
    maximum_depth: f64,
) -> usize {
    let mut count = usize::from(!compartments.is_empty());
    let mut depth = compartments
        .first()
        .map_or(0.0, |compartment| compartment.thickness);
    while depth < maximum_depth && count < compartments.len() {
        depth += compartments[count].thickness;
        count += 1;
    }
    count
}

fn move_subsoil_salt(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    infiltration: &InfiltrationState,
    parameters: &SaltTransportParameters,
    drained_salt: f64,
) -> Result<f64, Error> {
    let subsoil_water = infiltration.subsoil_drainage;
    if subsoil_water <= 0.0 || compartments.is_empty() {
        return Ok(drained_salt);
    }
    let mixing_depth = if parameters.rooting_depth >= f64::EPSILON {
        parameters.evaporation_depth
    } else {
        parameters.rooting_depth
    };
    let mut cumulative_depth = 0.0;
    let mut subsoil_ec = 0.0;
    let mut last_index = 0;
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
        cumulative_depth += compartment.thickness;
        let active_thickness = if cumulative_depth <= mixing_depth {
            compartment.thickness
        } else {
            compartment.thickness - (cumulative_depth - mixing_depth)
        };
        let active_cell = active_salinity_cell(compartment, layer);
        let cell_water = salinity_cell_water(layer, compartment.thickness, active_cell);
        let cell_ec = compartment.dissolved_salt[active_cell] / (cell_water * EQUIVALENT_WEIGHT);
        let active_fraction = active_thickness / compartment.thickness;
        let active_water = cell_water * active_fraction;
        subsoil_ec =
            (cell_ec * active_water + subsoil_ec * subsoil_water) / (active_water + subsoil_water);
        compartment.dissolved_salt[active_cell] = (1.0 - active_fraction)
            * compartment.dissolved_salt[active_cell]
            + active_fraction * subsoil_ec * cell_water * EQUIVALENT_WEIGHT;
        reconcile_solution_and_deposit(
            cell_water,
            &mut compartment.dissolved_salt[active_cell],
            &mut compartment.deposited_salt[active_cell],
            parameters.solubility,
        );
        last_index = compartment_index;
        if cumulative_depth >= mixing_depth || compartment_index + 1 == compartment_count {
            break;
        }
    }

    if last_index + 1 >= compartments.len() {
        return Ok(drained_salt + subsoil_ec * subsoil_water * EQUIVALENT_WEIGHT);
    }
    let receiving_index = last_index + 1;
    let receiving = &mut compartments[receiving_index];
    let layer =
        profile
            .layers
            .get(receiving.layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: receiving_index,
                layer: receiving.layer_index,
            })?;
    let active_cell = active_salinity_cell(receiving, layer);
    receiving.dissolved_salt[active_cell] += subsoil_ec * subsoil_water * EQUIVALENT_WEIGHT;
    let cell_water = salinity_cell_water(layer, receiving.thickness, active_cell);
    reconcile_solution_and_deposit(
        cell_water,
        &mut receiving.dissolved_salt[active_cell],
        &mut receiving.deposited_salt[active_cell],
        parameters.solubility,
    );
    Ok(drained_salt)
}

/// Moves salt from cells above the current wet cell into that active cell.
///
/// `solubility` is salt solubility in grams per litre; profile and
/// compartment fields retain their documented units.
///
/// # Errors
///
/// Returns [`Error`] when a compartment contains an invalid soil-layer index.
pub fn concentrate_salts(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    solubility: f64,
) -> Result<(), Error> {
    for (compartment_index, compartment) in compartments.iter_mut().enumerate() {
        let layer =
            profile
                .layers
                .get(compartment.layer_index)
                .ok_or(WaterBalanceError::MissingLayer {
                    compartment: compartment_index,
                    layer: compartment.layer_index,
                })?;
        let active_cell = active_salinity_cell(compartment, layer);
        let cell_count = salinity_cell_count(layer);
        let mut moved_salt = 0.0;
        for cell in (active_cell + 1)..cell_count {
            moved_salt += compartment.dissolved_salt[cell] + compartment.deposited_salt[cell];
            compartment.dissolved_salt[cell] = 0.0;
            compartment.deposited_salt[cell] = 0.0;
        }
        if moved_salt > 0.0 {
            compartment.dissolved_salt[active_cell] += moved_salt;
            let water = regular_salinity_cell_water(layer, compartment.thickness);
            reconcile_solution_and_deposit(
                water,
                &mut compartment.dissolved_salt[active_cell],
                &mut compartment.deposited_salt[active_cell],
                solubility,
            );
        }
    }
    Ok(())
}

#[must_use]
pub(crate) fn salinity_cell_water(
    layer: &PreparedSoilLayer,
    compartment_thickness: f64,
    cell: usize,
) -> f64 {
    let regular = regular_salinity_cell_water(layer, compartment_thickness);
    if cell + 1 == salinity_cell_count(layer) {
        2.0 * regular
    } else {
        regular
    }
}

fn regular_salinity_cell_water(layer: &PreparedSoilLayer, compartment_thickness: f64) -> f64 {
    layer.salinity_cell_width * 1_000.0 * compartment_thickness * layer.fine_earth_fraction()
}

fn salinity_cell_count(layer: &PreparedSoilLayer) -> usize {
    usize::try_from(layer.salinity_cell_count_with_saturation).unwrap_or(1)
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64;

    use super::{adjust_stomatal_for_soil_water_salinity, salinity_stress_coefficient};

    #[test]
    fn linear_salinity_response_matches_threshold_boundaries() {
        assert_eq_f64!(
            salinity_stress_coefficient(Some(2), Some(10), 2.0, 0.0),
            1.0_f64
        );
        assert_eq_f64!(
            salinity_stress_coefficient(Some(2), Some(10), 6.0, 0.0),
            0.5_f64
        );
        assert_eq_f64!(
            salinity_stress_coefficient(Some(2), Some(10), 10.0, 0.0),
            0.0_f64
        );
    }

    #[test]
    fn invalid_salinity_thresholds_disable_the_response() {
        assert_eq_f64!(salinity_stress_coefficient(None, None, 100.0, 0.0), 1.0_f64);
    }

    #[test]
    fn soil_water_salinity_can_only_reduce_the_incoming_stomatal_coefficient() {
        let adjusted = adjust_stomatal_for_soil_water_salinity(
            Some(2),
            Some(10),
            100,
            4.0,
            11.5,
            6.0,
            0.5,
            0.0,
            1.0,
            0.0,
            1.0,
        );
        assert!(adjusted >= 0.0);
        assert!(adjusted < 1.0);
        assert_eq_f64!(
            adjust_stomatal_for_soil_water_salinity(
                Some(2),
                Some(10),
                0,
                4.0,
                8.0,
                6.0,
                0.5,
                0.0,
                1.0,
                0.0,
                0.8,
            ),
            0.8_f64
        );
    }
}
