//! Root-zone water accounting and generic stress-response functions.

use crate::Error;
use crate::compat::roundc_i32;
use crate::soil::{PreparedSoilLayer, PreparedSoilProfile, SoilCompartment};
use crate::water_balance::WaterBalanceError;

/// Crop depletion thresholds used in root-zone water accounting.
#[derive(Debug)]
pub struct DepletionThresholds {
    /// Fraction of total available water where leaf expansion responds.
    pub leaf_expansion: f64,
    /// Fraction of total available water where stomata respond.
    pub stomatal_closure: f64,
    /// Fraction of total available water where senescence responds.
    pub senescence: f64,
}

/// Integrated water stores and thresholds in the root and upper soil zones.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct RootZoneWater {
    /// Actual root-zone water, in millimetres.
    pub actual: f64,
    /// Root-zone water at field capacity, in millimetres.
    pub field_capacity: f64,
    /// Root-zone water at wilting point, in millimetres.
    pub wilting_point: f64,
    /// Root-zone water at saturation, in millimetres.
    pub saturation: f64,
    /// Root-zone leaf-expansion threshold, in millimetres.
    pub leaf_threshold: f64,
    /// Root-zone stomatal threshold, in millimetres.
    pub stomatal_threshold: f64,
    /// Root-zone senescence threshold, in millimetres.
    pub senescence_threshold: f64,
    /// Actual water in the considered upper soil zone, in millimetres.
    pub upper_actual: f64,
    /// Upper-zone water at field capacity, in millimetres.
    pub upper_field_capacity: f64,
    /// Upper-zone water at wilting point, in millimetres.
    pub upper_wilting_point: f64,
    /// Upper-zone stomatal threshold, in millimetres.
    pub upper_stomatal_threshold: f64,
    /// Whether the upper zone is relatively wetter than the full root zone.
    pub upper_zone_is_wetter: bool,
}

/// Tests whether root-zone water permits germination.
///
/// `actual_water`, `wilting_point_water`, and `field_capacity_water` are in
/// millimetres. `required_available_water` is the required percentage of total
/// available water between wilting point and field capacity.
#[must_use]
pub fn germination_is_possible(
    actual_water: f64,
    wilting_point_water: f64,
    field_capacity_water: f64,
    required_available_water: i8,
) -> bool {
    let threshold = wilting_point_water
        + (field_capacity_water - wilting_point_water) * f64::from(required_available_water)
            / 100.0;
    actual_water >= threshold
}

/// Applies the daily FAO reference-ET correction to a depletion threshold.
///
/// `threshold` is a fraction of total available soil water, `reference_et`
/// is the day's reference evapotranspiration in millimetres, and
/// `adjustment_factor` is the dimensionless program-parameter multiplier.
#[must_use]
pub fn adjust_depletion_threshold_for_eto(
    threshold: f64,
    reference_et: f64,
    adjustment_factor: f64,
) -> f64 {
    (threshold + adjustment_factor * 0.04 * (5.0 - reference_et) * (10.0 - 9.0 * threshold).log10())
        .clamp(0.0, 1.0)
}

/// Advances the whole-root-zone deficient-aeration response by one day.
///
/// `saturation_water` and `actual_water` are in millimetres,
/// `anaerobic_point` is a volume percentage below saturation,
/// `rooting_depth` is in metres, and `full_effect_delay` and `exposure` are in
/// calendar days. The returned coefficient is dimensionless.
#[must_use]
pub fn advance_root_zone_anaerobic_stress(
    saturation_water: f64,
    actual_water: f64,
    anaerobic_point: i32,
    rooting_depth: f64,
    full_effect_delay: i32,
    exposure: &mut i32,
) -> f64 {
    if anaerobic_point <= 0 || rooting_depth <= 0.0 || full_effect_delay <= 0 {
        *exposure = 0;
        return 1.0;
    }
    let saturation = saturation_water / (10.0 * rooting_depth);
    let actual = (actual_water / (10.0 * rooting_depth)).min(saturation);
    if actual <= saturation - f64::from(anaerobic_point) {
        *exposure = 0;
        return 1.0;
    }
    *exposure = (*exposure + 1).min(full_effect_delay);
    let saturated_fraction = (saturation - actual) / f64::from(anaerobic_point);
    1.0 - (1.0 - saturated_fraction) * f64::from(*exposure) / f64::from(full_effect_delay)
}

/// Advances deficient-aeration stress in one soil compartment.
///
/// `anaerobic_point` is a volume percentage below saturation,
/// `full_effect_delay` and `submerged_exposure` are in calendar days, and
/// `incoming_coefficient` is dimensionless. Compartment and layer fields
/// retain their documented units. The returned coefficient is dimensionless.
#[must_use]
pub fn advance_local_anaerobic_stress(
    compartment: &mut SoilCompartment,
    layer: &PreparedSoilLayer,
    anaerobic_point: i32,
    full_effect_delay: i32,
    submerged_exposure: i32,
    incoming_coefficient: f64,
) -> f64 {
    if anaerobic_point <= 0 || full_effect_delay <= 0 {
        compartment.anaerobic_exposure = 0;
        return incoming_coefficient;
    }
    let anaerobic_coefficient = if submerged_exposure >= full_effect_delay {
        0.0
    } else if compartment.theta > (layer.saturation - f64::from(anaerobic_point)) / 100.0 {
        compartment.anaerobic_exposure += 1;
        let newly_saturated = compartment.anaerobic_exposure >= full_effect_delay;
        compartment.anaerobic_exposure = compartment.anaerobic_exposure.min(full_effect_delay);
        let mut coefficient = ((layer.saturation / 100.0 - compartment.theta)
            / (f64::from(anaerobic_point) / 100.0))
            .max(0.0);
        if full_effect_delay > 1 {
            let initial = i32::from(!newly_saturated);
            coefficient = (f64::from(initial)
                + f64::from(compartment.anaerobic_exposure - 1) * coefficient)
                / f64::from(initial + compartment.anaerobic_exposure - 1);
        }
        coefficient
    } else {
        compartment.anaerobic_exposure = 0;
        1.0
    };
    incoming_coefficient.min(anaerobic_coefficient)
}

/// Integrates root-zone and upper-zone water stores using partial compartments.
///
/// `rooting_depth` and `upper_zone_depth` are depths in metres. Threshold
/// fields are fractions of total available soil water; profile and compartment
/// water contents retain their field-documented units.
///
/// # Errors
///
/// Returns [`Error`] for a non-positive rooting depth or an invalid layer index.
pub fn determine_root_zone_water(
    profile: &PreparedSoilProfile,
    compartments: &[SoilCompartment],
    rooting_depth: f64,
    upper_zone_depth: f64,
    thresholds: &DepletionThresholds,
) -> Result<RootZoneWater, Error> {
    if !rooting_depth.is_finite() || rooting_depth <= 0.0 {
        return Err(WaterBalanceError::InvalidActiveDepth(rooting_depth).into());
    }
    let root = integrate_zone(profile, compartments, rooting_depth, thresholds, true)?;
    let upper = if rooting_depth * 100.0 <= upper_zone_depth * 100.0 {
        ZoneWater {
            actual: root.actual,
            field_capacity: root.field_capacity,
            wilting_point: root.wilting_point,
            saturation: root.saturation,
            leaf_threshold: root.leaf_threshold,
            stomatal_threshold: root.stomatal_threshold,
            senescence_threshold: root.senescence_threshold,
        }
    } else {
        integrate_zone(profile, compartments, upper_zone_depth, thresholds, false)?
    };
    let root_depletion = relative_depletion(&root);
    let upper_depletion = relative_depletion(&upper);
    Ok(RootZoneWater {
        actual: root.actual,
        field_capacity: root.field_capacity,
        wilting_point: root.wilting_point,
        saturation: root.saturation,
        leaf_threshold: root.leaf_threshold,
        stomatal_threshold: root.stomatal_threshold,
        senescence_threshold: root.senescence_threshold,
        upper_actual: upper.actual,
        upper_field_capacity: upper.field_capacity,
        upper_wilting_point: upper.wilting_point,
        upper_stomatal_threshold: upper.stomatal_threshold,
        upper_zone_is_wetter: upper_depletion < root_depletion,
    })
}

/// Generic AquaCrop stress coefficient between upper and lower depletion limits.
///
/// `relative_depletion`, `upper_limit`, and `lower_limit` are dimensionless
/// fractions. `shape_factor` and the returned coefficient are dimensionless.
#[must_use]
pub fn stress_coefficient(
    relative_depletion: f64,
    mut upper_limit: f64,
    lower_limit: f64,
    shape_factor: f64,
) -> f64 {
    if lower_limit - upper_limit < 0.000_1 {
        upper_limit = lower_limit - 0.000_1;
    }
    let relative = (relative_depletion - upper_limit) / (lower_limit - upper_limit);
    if relative <= f64::EPSILON {
        1.0
    } else if relative >= 1.0 {
        0.0
    } else {
        let shape = if roundc_i32(10.0 * shape_factor) == 0 {
            0.01
        } else {
            shape_factor
        };
        (1.0 - ((relative * shape).exp() - 1.0) / (shape.exp() - 1.0)).clamp(0.0, 1.0)
    }
}

/// Local water-stress factor for root extraction in one compartment.
///
/// `stomatal_depletion` is a fraction of total available water,
/// `stomatal_shape` is dimensionless, and `maximum_reduction` is
/// percent. Compartment and layer fields retain their documented units; the
/// returned factor is dimensionless.
#[must_use]
pub fn local_root_water_stress(
    compartment: &SoilCompartment,
    layer: &PreparedSoilLayer,
    stomatal_depletion: f64,
    stomatal_shape: f64,
    maximum_reduction: f64,
) -> f64 {
    let field_capacity = layer.field_capacity / 100.0;
    let wilting_point = layer.wilting_point / 100.0;
    let critical = field_capacity - (field_capacity - wilting_point) * stomatal_depletion;
    let unstressed_maximum = 1.0 - maximum_reduction / 100.0;
    if compartment.theta >= critical {
        unstressed_maximum
    } else if compartment.theta > wilting_point {
        let depletion = (field_capacity - compartment.theta) / (field_capacity - wilting_point);
        unstressed_maximum * stress_coefficient(depletion, stomatal_depletion, 1.0, stomatal_shape)
    } else {
        0.0
    }
}

struct ZoneWater {
    actual: f64,
    field_capacity: f64,
    wilting_point: f64,
    saturation: f64,
    leaf_threshold: f64,
    stomatal_threshold: f64,
    senescence_threshold: f64,
}

fn integrate_zone(
    profile: &PreparedSoilProfile,
    compartments: &[SoilCompartment],
    depth: f64,
    thresholds: &DepletionThresholds,
    all_thresholds: bool,
) -> Result<ZoneWater, Error> {
    let mut result = ZoneWater {
        actual: 0.0,
        field_capacity: 0.0,
        wilting_point: 0.0,
        saturation: 0.0,
        leaf_threshold: 0.0,
        stomatal_threshold: 0.0,
        senescence_threshold: 0.0,
    };
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
        let fraction = if cumulative_depth <= depth {
            1.0
        } else {
            ((depth - (cumulative_depth - compartment.thickness)) / compartment.thickness).max(0.0)
        };
        let thickness = compartment.thickness;
        let fine_earth = layer.fine_earth_fraction();
        result.actual += fraction * 1_000.0 * compartment.theta * thickness * fine_earth;
        result.field_capacity += fraction * 10.0 * layer.field_capacity * thickness * fine_earth;
        result.wilting_point += fraction * 10.0 * layer.wilting_point * thickness * fine_earth;
        result.saturation += fraction * 10.0 * layer.saturation * thickness * fine_earth;
        result.stomatal_threshold += fraction
            * 10.0
            * thickness
            * threshold(layer, thresholds.stomatal_closure)
            * fine_earth;
        if all_thresholds {
            result.leaf_threshold += fraction
                * 10.0
                * thickness
                * threshold(layer, thresholds.leaf_expansion)
                * fine_earth;
            result.senescence_threshold +=
                fraction * 10.0 * thickness * threshold(layer, thresholds.senescence) * fine_earth;
        }
        if cumulative_depth >= depth {
            break;
        }
    }
    Ok(result)
}

fn threshold(layer: &PreparedSoilLayer, depletion: f64) -> f64 {
    layer.field_capacity - depletion * (layer.field_capacity - layer.wilting_point)
}

fn relative_depletion(zone: &ZoneWater) -> f64 {
    if roundc_i32(1_000.0 * (zone.field_capacity - zone.wilting_point)) > 0 {
        (zone.field_capacity - zone.actual) / (zone.field_capacity - zone.wilting_point)
    } else {
        0.0
    }
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64;

    use super::{
        adjust_depletion_threshold_for_eto, advance_local_anaerobic_stress,
        advance_root_zone_anaerobic_stress, germination_is_possible,
    };
    use crate::soil::{SoilCompartment, SoilLayer};

    #[test]
    fn germination_uses_the_configured_fraction_of_available_water() {
        assert!(!germination_is_possible(29.9, 20.0, 60.0, 25));
        assert!(germination_is_possible(30.0, 20.0, 60.0, 25));
    }

    #[test]
    fn fao_adjustment_uses_reference_et_equation() {
        let adjusted = adjust_depletion_threshold_for_eto(0.25, 3.0, 1.0);
        let expected = 0.25 + 0.04 * 2.0 * (10.0_f64 - 9.0 * 0.25).log10();
        assert_eq_f64!(adjusted, expected);
    }

    #[test]
    fn fao_adjustment_is_limited_to_fraction_bounds() {
        assert_eq_f64!(adjust_depletion_threshold_for_eto(0.99, 0.0, 10.0), 1.0_f64);
        assert_eq_f64!(
            adjust_depletion_threshold_for_eto(0.01, 20.0, 10.0),
            0.0_f64
        );
    }

    #[test]
    fn root_zone_anaerobic_stress_reaches_full_strength_after_the_delay() {
        let mut exposure = 0;
        let first = advance_root_zone_anaerobic_stress(500.0, 490.0, 2, 1.0, 3, &mut exposure);
        let second = advance_root_zone_anaerobic_stress(500.0, 490.0, 2, 1.0, 3, &mut exposure);
        let third = advance_root_zone_anaerobic_stress(500.0, 490.0, 2, 1.0, 3, &mut exposure);

        assert!((first - 5.0 / 6.0).abs() <= f64::EPSILON);
        assert!((second - 2.0 / 3.0).abs() <= f64::EPSILON);
        assert!((third - 0.5).abs() <= f64::EPSILON);
        assert_eq!(exposure, 3);
    }

    #[test]
    fn local_anaerobic_stress_uses_the_source_delay_ramp() {
        let layer = SoilLayer {
            thickness: 1.0,
            saturation: 50.0,
            field_capacity: 30.0,
            wilting_point: 10.0,
            saturated_hydraulic_conductivity: 100.0,
            penetrability: 100,
            gravel_mass: 0,
            capillary_rise_parameters: None,
        }
        .prepare()
        .expect("test soil layer should be valid");
        let mut compartment = SoilCompartment::at_field_capacity(1.0, 0, 30.0);
        compartment.theta = 0.49;

        let first = advance_local_anaerobic_stress(&mut compartment, &layer, 2, 3, 0, 1.0);
        let second = advance_local_anaerobic_stress(&mut compartment, &layer, 2, 3, 0, 1.0);
        let third = advance_local_anaerobic_stress(&mut compartment, &layer, 2, 3, 0, 1.0);

        assert_eq_f64!(first, 1.0_f64);
        assert!((second - 0.75).abs() <= 1.0e-12);
        assert!((third - 0.5).abs() <= 1.0e-12);
        assert_eq!(compartment.anaerobic_exposure, 3);
    }
}
