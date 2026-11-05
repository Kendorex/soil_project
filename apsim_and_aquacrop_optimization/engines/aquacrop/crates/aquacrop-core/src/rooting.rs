//! AquaCrop 7.3 root-depth progression and soil restrictions.

use crate::compat::roundc_i32;
use crate::crop::PreparedCrop;
use crate::soil::{PreparedSoilLayer, PreparedSoilProfile, SoilCompartment};
use crate::stress::stress_coefficient;

/// Time values needed to advance rooting by one day.
#[derive(Debug)]
pub struct RootingTime {
    /// Current calendar day after planting, starting at one.
    pub day_after_planting: i32,
    /// Days by which crop development has been delayed.
    pub delayed_days: i32,
    /// Growing degree-days accumulated through the previous day.
    pub previous_sum_gdd: f64,
    /// Growing degree-days accumulated through the current day.
    pub sum_gdd: f64,
    /// Growing degree-days accumulated on the current day.
    pub daily_gdd: f64,
}

/// Prepared crop and water state that can restrict the current root-depth increment.
#[derive(Debug)]
pub struct RootingStress {
    /// Potential canopy cover, in percent.
    pub potential_canopy: f64,
    /// Actual canopy cover, in percent.
    pub actual_canopy: f64,
    /// Potential crop transpiration for the previous feedback step, in millimetres.
    pub potential_transpiration: f64,
    /// Actual crop transpiration for the previous feedback step, in millimetres.
    pub actual_transpiration: f64,
    /// Cumulative potential crop transpiration, in millimetres.
    pub cumulative_potential_transpiration: f64,
    /// Cumulative actual crop transpiration, in millimetres.
    pub cumulative_actual_transpiration: f64,
    /// Root-expansion response shape; `None` disables transpiration feedback.
    pub root_shape_factor: Option<i32>,
    /// Whether germination has occurred.
    pub germinated: bool,
}

/// Rooting state produced for the current day.
#[derive(Debug)]
pub struct RootingResult {
    /// Actual rooting depth, in metres.
    pub depth: f64,
    /// Unrestricted depth that could have been reached, in metres.
    pub unrestricted_depth: f64,
    /// Correction applied later to the root-extraction density profile.
    pub density_correction: f64,
}

/// Advances the actual rooting depth by one daily model step.
///
/// Passing `None` as `previous_depth` initializes the depth directly from the
/// unrestricted root function. A previous depth enables the source model's
/// stress feedback and the version 7.3 effective-time recovery.
/// `previous_depth` and the returned depths are in metres, while
/// `initial_root_depth` is percent. Fields of `time`, `stress`, `crop`,
/// `profile`, and `compartments` retain their field-documented units.
#[must_use]
pub fn advance_rooting_depth(
    crop: &PreparedCrop,
    profile: &PreparedSoilProfile,
    compartments: &[SoilCompartment],
    previous_depth: Option<f64>,
    time: &RootingTime,
    stress: &RootingStress,
    initial_root_depth: i32,
) -> RootingResult {
    let (minimum_time_days, minimum_time_gdd) = time_to_minimum_depth(crop, initial_root_depth);
    let (effective_day, effective_previous_gdd, effective_gdd) = effective_rooting_time(
        crop,
        previous_depth,
        time,
        initial_root_depth,
        minimum_time_days,
        minimum_time_gdd,
    );

    let Some(previous_depth) = previous_depth else {
        let depth = actual_rooting_depth(
            crop,
            profile,
            time.day_after_planting,
            time.delayed_days,
            time.sum_gdd,
            initial_root_depth,
            true,
        );
        return RootingResult {
            depth,
            unrestricted_depth: unrestricted_rooting_depth(
                crop,
                time.day_after_planting,
                time.delayed_days,
                time.sum_gdd,
                initial_root_depth,
            ),
            density_correction: 1.0,
        };
    };

    let unrestricted_depth = unrestricted_rooting_depth(
        crop,
        effective_day,
        time.delayed_days,
        effective_gdd,
        initial_root_depth,
    );
    let previous_unrestricted = actual_rooting_depth(
        crop,
        profile,
        effective_day - 1,
        time.delayed_days,
        effective_previous_gdd,
        initial_root_depth,
        true,
    );
    let current_restricted = actual_rooting_depth(
        crop,
        profile,
        effective_day,
        time.delayed_days,
        effective_gdd,
        initial_root_depth,
        true,
    );
    let mut increment = current_restricted - previous_unrestricted;

    if stress.potential_transpiration > 0.0
        && stress.actual_transpiration < stress.potential_transpiration
        && let Some(shape) = stress.root_shape_factor
    {
        let ratio = stress.actual_transpiration / stress.potential_transpiration;
        let reduction = if shape >= 0 {
            ratio
        } else {
            let shape = f64::from(shape);
            ((ratio * shape).exp() - 1.0) / (shape.exp() - 1.0)
        };
        increment *= reduction;
    }

    if increment > 0.001 {
        increment *= dry_front_reduction(crop, profile, compartments, previous_depth + increment);
    }
    if (stress.actual_canopy <= f64::EPSILON && stress.potential_canopy > 50.0)
        || !stress.germinated
    {
        increment = 0.0;
    }

    let depth = (previous_depth + increment).max(previous_depth);
    let density_correction = root_density_correction(crop, depth, unrestricted_depth, stress);
    RootingResult {
        depth,
        unrestricted_depth,
        density_correction,
    }
}

/// Returns the unrestricted root depth before soil penetrability is applied.
///
/// `day_after_planting` and `delayed_days` are calendar-day counts, `sum_gdd`
/// is in degree Celsius-days, and `initial_root_depth` is percent. Prepared crop
/// fields retain their documented units; the result is in metres.
#[must_use]
pub fn unrestricted_rooting_depth(
    crop: &PreparedCrop,
    day_after_planting: i32,
    delayed_days: i32,
    sum_gdd: f64,
    initial_root_depth: i32,
) -> f64 {
    let virtual_day = day_after_planting - delayed_days;
    let calendar = crop.cycle.calendar_days();
    if virtual_day < 1 || virtual_day > calendar.maturity {
        return 0.0;
    }
    let rooting = &crop.parameters.rooting;
    let driving_calendar = crop.cycle.driving_calendar();
    let minimum = rooting.minimum_depth;
    let maximum = rooting.maximum_depth;
    if crop.cycle.uses_growing_degree_days() {
        if sum_gdd >= f64::from(driving_calendar.maximum_rooting) {
            return maximum;
        }
        if minimum >= maximum {
            return maximum;
        }
        let initial = minimum * f64::from(initial_root_depth) / 100.0;
        let start = f64::from(driving_calendar.emergence) / 2.0;
        let depth = if f64::from(driving_calendar.maximum_rooting) <= start {
            initial + (maximum - initial) * sum_gdd / f64::from(driving_calendar.maximum_rooting)
        } else if sum_gdd <= start {
            initial
        } else {
            initial
                + (maximum - initial)
                    * time_root_function(
                        sum_gdd,
                        rooting.shape,
                        f64::from(driving_calendar.maximum_rooting),
                        start,
                    )
        };
        return depth.max(minimum);
    }

    if virtual_day >= calendar.maximum_rooting {
        return maximum;
    }
    if minimum >= maximum {
        return maximum;
    }
    let initial = minimum * f64::from(initial_root_depth) / 100.0;
    let start = roundc_i32(f64::from(calendar.emergence) / 2.0);
    let depth = if calendar.maximum_rooting <= start {
        initial + (maximum - initial) * f64::from(virtual_day) / f64::from(calendar.maximum_rooting)
    } else if virtual_day <= start {
        initial
    } else {
        initial
            + (maximum - initial)
                * time_root_function(
                    f64::from(virtual_day),
                    rooting.shape,
                    f64::from(calendar.maximum_rooting),
                    f64::from(start),
                )
    };
    depth.max(minimum)
}

/// Applies successive soil-layer penetrability factors to a potential depth.
///
/// `depth`, layer thicknesses, and the result are in metres; layer
/// penetrability values are percentages.
#[must_use]
pub fn depth_through_restrictive_layers(depth: f64, layers: &[PreparedSoilLayer]) -> f64 {
    let Some(first) = layers.first() else {
        return depth;
    };
    let mut layer_index = 0;
    let mut soil_depth = first.thickness;
    let mut adjusted = 0.0;
    let mut remaining = depth;
    let mut layer_thickness = soil_depth;
    loop {
        let penetrability = f64::from(layers[layer_index].penetrability) / 100.0;
        let test_depth = adjusted + remaining * penetrability;
        if layer_index + 1 == layers.len()
            || layers[layer_index].penetrability == 0
            || roundc_i32(test_depth * 10_000.0) <= roundc_i32(soil_depth * 10_000.0)
        {
            return test_depth;
        }
        adjusted = soil_depth;
        remaining -= layer_thickness / penetrability;
        layer_index += 1;
        layer_thickness = layers[layer_index].thickness;
        soil_depth += layer_thickness;
    }
}

fn actual_rooting_depth(
    crop: &PreparedCrop,
    profile: &PreparedSoilProfile,
    day_after_planting: i32,
    delayed_days: i32,
    sum_gdd: f64,
    initial_root_depth: i32,
    apply_restrictions: bool,
) -> f64 {
    let depth = unrestricted_rooting_depth(
        crop,
        day_after_planting,
        delayed_days,
        sum_gdd,
        initial_root_depth,
    );
    if apply_restrictions {
        depth_through_restrictive_layers(depth, &profile.layers)
    } else {
        depth
    }
}

fn time_to_minimum_depth(crop: &PreparedCrop, initial_root_depth: i32) -> (f64, f64) {
    let rooting = &crop.parameters.rooting;
    let calendar = crop.cycle.calendar_days();
    let driving_calendar = crop.cycle.driving_calendar();
    let mut days = f64::from(calendar.emergence);
    let mut gdd = f64::from(driving_calendar.emergence);
    if initial_root_depth < 100 && rooting.minimum_depth < rooting.maximum_depth {
        let initial = rooting.minimum_depth * f64::from(initial_root_depth) / 100.0;
        let function = ((f64::from(rooting.shape) / 10.0)
            * ((rooting.minimum_depth - initial) / (rooting.maximum_depth - initial)).ln())
        .exp();
        let emergence_days = f64::from(calendar.emergence);
        if crop.cycle.uses_growing_degree_days() {
            let emergence_gdd = f64::from(driving_calendar.emergence);
            if f64::from(driving_calendar.maximum_rooting) > emergence_gdd / 2.0 {
                gdd = function
                    * (f64::from(driving_calendar.maximum_rooting) - emergence_gdd / 2.0)
                    + emergence_gdd / 2.0;
            }
        } else if f64::from(calendar.maximum_rooting) > emergence_days / 2.0 {
            days = function * (f64::from(calendar.maximum_rooting) - emergence_days / 2.0)
                + emergence_days / 2.0;
        }
    }
    (days, gdd)
}

fn effective_rooting_time(
    crop: &PreparedCrop,
    previous_depth: Option<f64>,
    time: &RootingTime,
    initial_root_depth: i32,
    minimum_time_days: f64,
    minimum_time_gdd: f64,
) -> (i32, f64, f64) {
    let mut day = time.day_after_planting;
    let mut previous_gdd = time.previous_sum_gdd;
    let mut gdd = time.sum_gdd;
    let Some(previous_depth) = previous_depth else {
        return (day, previous_gdd, gdd);
    };
    let rooting = &crop.parameters.rooting;
    let calendar_days = crop.cycle.calendar_days();
    let driving_calendar = crop.cycle.driving_calendar();
    let initial = rooting.minimum_depth * f64::from(initial_root_depth) / 100.0;
    if rooting.minimum_depth < rooting.maximum_depth && previous_depth > initial {
        let function = (((previous_depth - initial) / (rooting.maximum_depth - initial)).ln()
            * (f64::from(rooting.shape) / 10.0))
            .exp();
        if crop.cycle.uses_growing_degree_days() {
            if time.sum_gdd > minimum_time_gdd {
                let emergence = f64::from(driving_calendar.emergence) / 2.0;
                previous_gdd = function * (f64::from(driving_calendar.maximum_rooting) - emergence)
                    + emergence;
                gdd = (previous_gdd + time.daily_gdd).min(time.sum_gdd);
            }
        } else if f64::from(time.day_after_planting) > minimum_time_days {
            let emergence = f64::from(calendar_days.emergence) / 2.0;
            let previous_day =
                function * (f64::from(calendar_days.maximum_rooting) - emergence) + emergence;
            day = (roundc_i32(previous_day) + 1).min(time.day_after_planting);
        }
    }
    (day, previous_gdd, gdd)
}

fn dry_front_reduction(
    crop: &PreparedCrop,
    profile: &PreparedSoilProfile,
    compartments: &[SoilCompartment],
    test_depth: f64,
) -> f64 {
    let mut soil_depth = 0.0;
    let Some(compartment) = compartments.iter().find(|compartment| {
        soil_depth += compartment.thickness;
        soil_depth >= test_depth
    }) else {
        return 1.0;
    };
    let Some(layer) = profile.layers.get(compartment.layer_index) else {
        return 1.0;
    };
    let field_capacity = layer.field_capacity / 100.0;
    let wilting_point = layer.wilting_point / 100.0;
    let total_available = field_capacity - wilting_point;
    let stomatal = crop.parameters.water_stress.stomatal_closure_upper;
    let expansion_depletion = stomatal + (1.0 - stomatal) / 2.0;
    let threshold = field_capacity - expansion_depletion * total_available;
    if compartment.theta >= threshold {
        1.0
    } else if compartment.theta <= wilting_point {
        0.0
    } else {
        let depletion = (field_capacity - compartment.theta) / total_available;
        stress_coefficient(
            depletion,
            expansion_depletion,
            1.0,
            crop.parameters.water_stress.stomatal_closure_shape,
        )
    }
}

fn root_density_correction(
    crop: &PreparedCrop,
    depth: f64,
    unrestricted_depth: f64,
    stress: &RootingStress,
) -> f64 {
    if roundc_i32(depth * 1_000.0) >= roundc_i32(unrestricted_depth * 1_000.0) {
        return 1.0;
    }
    let top = crop.parameters.rooting.extraction_top;
    let bottom = crop.parameters.rooting.extraction_bottom;
    #[expect(
        clippy::manual_midpoint,
        reason = "the reference adds extraction limits before dividing by two"
    )]
    let mut correction =
        (2.0 * (unrestricted_depth / depth) * ((top + bottom) / 2.0) - top) / bottom;
    if stress.cumulative_potential_transpiration > 0.0 {
        correction *=
            stress.cumulative_actual_transpiration / stress.cumulative_potential_transpiration;
        correction = correction.max(1.0);
    }
    correction
}

fn time_root_function(time: f64, shape: i8, maximum_time: f64, start_time: f64) -> f64 {
    ((10.0 / f64::from(shape)) * ((time - start_time) / (maximum_time - start_time)).ln()).exp()
}
