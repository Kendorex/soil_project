//! Unstressed canopy development and perennial self-thinning.

use crate::compat::{roundc_i8, roundc_i32};
use crate::crop::{DevelopmentCalendar, PlantingMethod, PreparedCrop};

/// Calculates the total-canopy multiplier caused by weed cover.
///
/// `relative_cover` is weed cover as a percentage of combined crop and weed
/// cover, `crop_maximum_cover` is a soil-cover fraction, and `shape_factor` is
/// dimensionless. The returned multiplier is dimensionless.
#[must_use]
pub fn weed_canopy_multiplier(
    relative_cover: i8,
    crop_maximum_cover: f64,
    shape_factor: f64,
) -> f64 {
    if relative_cover <= 0 || !(0.001..0.9999).contains(&crop_maximum_cover) {
        return 1.0;
    }
    if relative_cover == 100 {
        return 1.0 / crop_maximum_cover;
    }
    let response = if shape_factor.abs() < f64::EPSILON {
        f64::from(relative_cover) / 100.0
    } else {
        ((shape_factor * f64::from(relative_cover) / 100.0).exp() - 1.0)
            / (shape_factor.exp() - 1.0)
    };
    let multiplier = 1.0 - (1.0 - 1.0 / crop_maximum_cover) * response;
    multiplier.min(1.0 / crop_maximum_cover)
}

/// Calculates relative weed cover for the current crop-development time.
///
/// `base_cover` and the return value are percentages. `cover_delta` is the
/// seasonal percentage change relative to `base_cover`. `development_time`,
/// `full_canopy_time`, and `senescence_time` must all use the same unit,
/// either calendar days or growing degree-days.
#[must_use]
pub fn relative_weed_cover(
    base_cover: f64,
    cover_delta: i32,
    development_time: f64,
    full_canopy_time: f64,
    senescence_time: f64,
) -> f64 {
    if base_cover <= 0.0 || cover_delta == 0 || development_time <= full_canopy_time {
        return base_cover;
    }
    let progress = if development_time >= senescence_time {
        1.0
    } else {
        (development_time - full_canopy_time) / (senescence_time - full_canopy_time)
    };
    base_cover * (1.0 + f64::from(cover_delta) / 100.0 * progress)
}

/// Weed multiplier and adjusted relative cover after perennial self-thinning.
#[derive(Debug)]
pub struct PerennialWeedAdjustment {
    /// Multiplier applied to self-thinned crop cover.
    pub canopy_multiplier: f64,
    /// Relative weed cover, in percent.
    pub relative_cover: i8,
}

/// Adjusts weed replacement for a self-thinned perennial canopy.
///
/// `relative_cover` and `replacement` are percentages, `crop_maximum_cover`
/// and `self_thinning` are fractions, `shape_factor` is dimensionless, and
/// `season_year` is a cultivation-season count. The result documents its own
/// units.
#[must_use]
pub fn perennial_weed_adjustment(
    relative_cover: i8,
    crop_maximum_cover: f64,
    shape_factor: f64,
    self_thinning: f64,
    season_year: i32,
    replacement: i8,
) -> PerennialWeedAdjustment {
    let mut adjusted_cover = relative_cover;
    let mut multiplier = weed_canopy_multiplier(relative_cover, crop_maximum_cover, shape_factor);
    if relative_cover > 0 && season_year > 1 && self_thinning < 0.995 {
        let mut minimum_shape = 10.0
            - 20.0
                * (((3.0 * self_thinning).exp() - 1.0) / (3.0_f64.exp() - 1.0)
                    + (f64::from(replacement) / 100.0).sqrt());
        if roundc_i32(minimum_shape * 10.0) == 0 {
            minimum_shape = 0.1;
        }
        let adjusted_shape = shape_factor.max(minimum_shape);
        let complete_replacement =
            weed_canopy_multiplier(relative_cover, crop_maximum_cover, adjusted_shape)
                * crop_maximum_cover;
        let no_replacement = if self_thinning > 0.005 {
            weed_canopy_multiplier(
                roundc_i8(self_thinning * f64::from(relative_cover)),
                self_thinning * crop_maximum_cover,
                adjusted_shape,
            ) * self_thinning
                * crop_maximum_cover
        } else {
            self_thinning * crop_maximum_cover
        };
        let mut total = no_replacement
            + (complete_replacement - no_replacement) * f64::from(replacement) / 100.0;
        total = total
            .max(self_thinning * crop_maximum_cover * (1.0 - f64::from(relative_cover) / 100.0));
        if self_thinning > 0.005 {
            multiplier = total / (self_thinning * crop_maximum_cover);
            multiplier = multiplier.min(1.0 / (self_thinning * crop_maximum_cover));
        }
        let mut cover = f64::from(relative_cover)
            + (1.0 - self_thinning) * crop_maximum_cover * f64::from(replacement);
        if self_thinning > 0.005 {
            cover = cover.max(100.0 * (total - self_thinning * crop_maximum_cover) / total);
            cover = cover.min(
                100.0
                    * (1.0
                        - self_thinning
                            * crop_maximum_cover
                            * (1.0 - f64::from(relative_cover) / 100.0)
                            / total),
            );
        }
        adjusted_cover = roundc_i8(cover).min(100);
    }
    PerennialWeedAdjustment {
        canopy_multiplier: multiplier,
        relative_cover: adjusted_cover,
    }
}

/// Prepared crop canopy timings derived from the initial cover and growth coefficients.
#[derive(Debug)]
pub struct CanopyTiming {
    /// Calendar days needed for a regrowing crop to reach its initial cover.
    pub days_to_initial_cover: Option<i32>,
    /// Calendar days from planting to 98 percent of maximum canopy cover.
    pub days_to_full_canopy: i32,
    /// Growing degree-days needed for a regrowing crop to reach its initial cover.
    pub gdd_to_initial_cover: Option<i32>,
    /// Growing degree-days from planting to 98 percent of maximum canopy cover.
    pub gdd_to_full_canopy: i32,
}

/// Full-canopy timing and reductions after the source fertility feasibility check.
#[derive(Debug)]
pub struct FertilityCanopyTiming {
    /// Calendar days from planting to full canopy under fertility stress.
    pub days_to_full_canopy: i32,
    /// Reduction of the canopy growth coefficient, in percent.
    pub growth_reduction: i8,
    /// Reduction of maximum canopy cover, in percent.
    pub maximum_cover_reduction: i8,
}

/// Derives invariant canopy timings from prepared crop parameters.
#[must_use]
pub fn derive_canopy_timing(crop: &PreparedCrop) -> CanopyTiming {
    let calendar_days = crop.cycle.calendar_days();
    let driving_calendar = crop.cycle.driving_calendar();

    CanopyTiming {
        days_to_initial_cover: time_to_initial_canopy(
            crop,
            calendar_days.canopy_growth_coefficient,
        ),
        days_to_full_canopy: time_to_canopy_cover(
            0.98 * crop.parameters.canopy.maximum_cover,
            crop.parameters.canopy.initial_seedling_cover,
            crop.parameters.canopy.maximum_cover,
            calendar_days.canopy_growth_coefficient,
            calendar_days.emergence,
        ),
        gdd_to_initial_cover: time_to_initial_canopy(
            crop,
            driving_calendar.canopy_growth_coefficient,
        ),
        gdd_to_full_canopy: time_to_canopy_cover(
            0.98 * crop.parameters.canopy.maximum_cover,
            crop.parameters.canopy.initial_seedling_cover,
            crop.parameters.canopy.maximum_cover,
            driving_calendar.canopy_growth_coefficient,
            driving_calendar.emergence,
        ),
    }
}

/// Calculates the rounded time needed to reach a requested canopy cover.
///
/// `target_cover`, `initial_cover`, and `maximum_cover` are soil-cover
/// fractions. `growth_coefficient` is per unit of time, and `initial_time` and
/// the return value use that same time unit (calendar days or degree-days).
#[must_use]
pub fn time_to_canopy_cover(
    target_cover: f64,
    initial_cover: f64,
    maximum_cover: f64,
    growth_coefficient: f64,
    initial_time: i32,
) -> i32 {
    let elapsed = if initial_cover > target_cover || initial_cover >= maximum_cover {
        0.0
    } else {
        let target = target_cover.min(0.98 * maximum_cover);
        if target <= maximum_cover / 2.0 {
            (target / initial_cover).ln() / growth_coefficient
        } else {
            ((0.25 * maximum_cover * maximum_cover / initial_cover) / (maximum_cover - target)).ln()
                / growth_coefficient
        }
    };
    initial_time + roundc_i32(elapsed)
}

/// Calendar days (or growing degree-days, if `decline_coefficient` is itself per-GDD) for
/// canopy cover to decline from `maximum_cover` to zero, given `decline_coefficient`.
///
/// Returns `None` when `decline_coefficient` is non-positive (matching the reference's
/// `undef_int` sentinel for a crop with no decline defined) or `maximum_cover` is
/// non-positive (no canopy to decline from).
#[must_use]
pub fn length_canopy_decline(maximum_cover: f64, decline_coefficient: f64) -> Option<i32> {
    if maximum_cover <= 0.0 || decline_coefficient <= crate::compat::ZERO_THRESHOLD {
        return None;
    }
    // `ln(1 + 1/0.05) = ln(21)`; `+ 0.5` guarantees canopy cover reaches exactly zero at the
    // computed day, not just asymptotically close to it, matching the reference exactly.
    Some(roundc_i32(
        (maximum_cover + 2.29) / (decline_coefficient * 3.33) * (1.0 + 1.0 / 0.05_f64).ln() + 0.5,
    ))
}

/// The crop cycle's true effective length in calendar days, as the reference actually
/// enforces it for `DetermineGrowthStage`'s "after cropping period" cutoff -- distinct from,
/// and sometimes shorter or longer than, the crop file's raw declared `days_to_maturity`.
///
/// Ports the reference's `DetermineLengthGrowthStages` (`global.f90:1673`) sum of four stage
/// lengths (initial, crop development, mid-season, late-season), clamped so the sum never
/// exceeds `days_to_maturity` -- but, matching the reference exactly, never *extended* to
/// reach it either: a crop whose own computed late-season length falls short of the declared
/// cycle length ends its cycle early, by design. `days_to_full_canopy` is the caller's
/// already-computed (and, for GDD crops, already calendar-adjusted) `Length12` -- this
/// function does not recompute it, to stay correct if the caller applies a GDD-crop-calendar
/// override after its own `time_to_canopy_cover` call.
///
/// Does not implement the reference's regrowth-planting adjustment branch (`ThePlanting ==
/// plant_regrowth`): no crop fixture in this project's scenario coverage uses regrowth
/// planting, so it's unverified and deliberately left unported rather than guessed at.
#[must_use]
pub fn effective_cycle_length(crop: &PreparedCrop, days_to_full_canopy: i32) -> i32 {
    let calendar = crop.cycle.calendar_days();
    let total_length = calendar.maturity;
    let length_123 = calendar.senescence.max(days_to_full_canopy);

    let (stage_1, stage_2) = if crop.parameters.canopy.initial_seedling_cover
        >= crop.parameters.canopy.maximum_cover
        || days_to_full_canopy <= calendar.emergence
    {
        (0, 0)
    } else {
        let stage_1 = time_to_canopy_cover(
            0.10,
            crop.parameters.canopy.initial_seedling_cover,
            crop.parameters.canopy.maximum_cover,
            calendar.canopy_growth_coefficient,
            calendar.emergence,
        );
        (stage_1, days_to_full_canopy - stage_1)
    };
    let stage_3 = length_123 - days_to_full_canopy;
    let stage_4 = length_canopy_decline(
        crop.parameters.canopy.maximum_cover,
        calendar.canopy_decline_coefficient,
    )
    .unwrap_or(0);

    // The reference clamps the running sum down to `total_length` at the first prefix that
    // exceeds it (StLength(1), then StLength(1)+StLength(2), and so on), zeroing every stage
    // after the one that triggered the clamp -- but every one of those four cascading branches
    // ends with the *final* sum equal to exactly `total_length`. Since each stage length here
    // is non-negative by construction (matching the reference's own unguarded assumption in
    // this non-regrowth branch), the partial sums are monotonically non-decreasing, so the
    // whole cascade is equivalent to a single `min` against the raw four-stage sum -- which is
    // left un-clamped (and possibly short of `total_length`) only when it never exceeds it.
    (stage_1 + stage_2 + stage_3 + stage_4).min(total_length)
}

/// Determines attainable full-canopy timing under soil-fertility stress.
///
/// `growth_reduction` and `maximum_cover_reduction` are integer
/// percentages. `fertility_stress` is the management stress class in
/// percent. Prepared crop canopy cover is a fraction, growth is per day, and all crop
/// development times used here are calendar days.
#[must_use]
pub fn fertility_canopy_timing(
    crop: &PreparedCrop,
    growth_reduction: i8,
    maximum_cover_reduction: i8,
    fertility_stress: i32,
) -> FertilityCanopyTiming {
    fertility_canopy_timing_with_limits(
        crop,
        growth_reduction,
        maximum_cover_reduction,
        fertility_stress,
        crop.parameters.canopy.initial_seedling_cover,
        crop.parameters.canopy.maximum_cover,
    )
}

pub(crate) fn fertility_canopy_timing_with_limits(
    crop: &PreparedCrop,
    mut growth_reduction: i8,
    mut maximum_cover_reduction: i8,
    fertility_stress: i32,
    initial_cover: f64,
    maximum_cover: f64,
) -> FertilityCanopyTiming {
    let calendar_days = crop.cycle.calendar_days();
    let unstressed = time_to_canopy_cover(
        0.98 * maximum_cover,
        initial_cover,
        maximum_cover,
        calendar_days.canopy_growth_coefficient,
        calendar_days.emergence,
    );
    if fertility_stress == 0 || growth_reduction == 0 && maximum_cover_reduction == 0 {
        return FertilityCanopyTiming {
            days_to_full_canopy: unstressed,
            growth_reduction,
            maximum_cover_reduction,
        };
    }

    let maximum_day = if crop.parameters.development.determinacy_linked_to_flowering {
        calendar_days.flowering + roundc_i32(f64::from(calendar_days.flowering_length) / 2.0)
    } else {
        calendar_days.senescence
    };
    let mut days_to_full = fertility_days_to_full_canopy(
        calendar_days,
        growth_reduction,
        maximum_cover_reduction,
        initial_cover,
        maximum_cover,
    );
    while days_to_full > maximum_day && growth_reduction > 0 {
        growth_reduction -= 1;
        days_to_full = fertility_days_to_full_canopy(
            calendar_days,
            growth_reduction,
            maximum_cover_reduction,
            initial_cover,
            maximum_cover,
        );
    }
    while days_to_full > maximum_day
        && (1.0 - f64::from(maximum_cover_reduction) / 100.0) * maximum_cover > 0.10
        && maximum_cover_reduction <= 50
    {
        maximum_cover_reduction += 1;
        days_to_full = fertility_days_to_full_canopy(
            calendar_days,
            growth_reduction,
            maximum_cover_reduction,
            initial_cover,
            maximum_cover,
        );
    }
    FertilityCanopyTiming {
        days_to_full_canopy: days_to_full,
        growth_reduction,
        maximum_cover_reduction,
    }
}

fn fertility_days_to_full_canopy(
    crop_calendar: &DevelopmentCalendar,
    growth_reduction: i8,
    maximum_cover_reduction: i8,
    initial_cover: f64,
    maximum_cover: f64,
) -> i32 {
    let maximum = (1.0 - f64::from(maximum_cover_reduction) / 100.0) * maximum_cover;
    time_to_canopy_cover(
        0.98 * maximum,
        initial_cover,
        maximum,
        crop_calendar.canopy_growth_coefficient * (1.0 - f64::from(growth_reduction) / 100.0),
        crop_calendar.emergence,
    )
}

fn time_to_initial_canopy(crop: &PreparedCrop, growth_coefficient: f64) -> Option<i32> {
    if crop.parameters.planting_method != PlantingMethod::Regrowth
        || crop.parameters.canopy.seedling_size >= crop.parameters.canopy.regrowth_plant_size
    {
        return Some(0);
    }
    if crop.parameters.canopy.initial_regrowth_cover >= 0.98 * crop.parameters.canopy.maximum_cover
    {
        return None;
    }
    Some(time_to_canopy_cover(
        crop.parameters.canopy.initial_regrowth_cover,
        crop.parameters.canopy.initial_seedling_cover,
        crop.parameters.canopy.maximum_cover,
        growth_coefficient,
        0,
    ))
}

/// Calculates potential canopy cover at the end of the current day.
///
/// `day_after_planting` and `delayed_days` are calendar days, `sum_gdd` is in
/// degree Celsius-days, and both reduction arguments are percentages. Prepared crop
/// fields retain the units documented on [`PreparedCrop`]; the result is a soil-cover
/// fraction.
#[must_use]
pub fn potential_canopy_cover(
    crop: &PreparedCrop,
    day_after_planting: i32,
    delayed_days: i32,
    sum_gdd: f64,
    growth_reduction: i8,
    maximum_cover_reduction: i8,
) -> f64 {
    potential_canopy_cover_with_timing(
        crop,
        &derive_canopy_timing(crop),
        day_after_planting,
        delayed_days,
        sum_gdd,
        growth_reduction,
        maximum_cover_reduction,
    )
}

pub(crate) fn potential_canopy_cover_with_timing(
    crop: &PreparedCrop,
    canopy_timing: &CanopyTiming,
    day_after_planting: i32,
    delayed_days: i32,
    sum_gdd: f64,
    growth_reduction: i8,
    maximum_cover_reduction: i8,
) -> f64 {
    potential_canopy_cover_with_timing_and_limits(
        crop,
        canopy_timing,
        day_after_planting,
        delayed_days,
        sum_gdd,
        growth_reduction,
        maximum_cover_reduction,
        crop.parameters.canopy.initial_seedling_cover,
        crop.parameters.canopy.maximum_cover,
    )
}

#[expect(
    clippy::too_many_arguments,
    reason = "the explicit limits preserve the distinct unrestricted and self-thinned canopies"
)]
pub(crate) fn potential_canopy_cover_with_timing_and_limits(
    crop: &PreparedCrop,
    canopy_timing: &CanopyTiming,
    day_after_planting: i32,
    delayed_days: i32,
    sum_gdd: f64,
    growth_reduction: i8,
    maximum_cover_reduction: i8,
    initial: f64,
    maximum: f64,
) -> f64 {
    let virtual_day = day_after_planting - delayed_days;
    let calendar_days = crop.cycle.calendar_days();
    if virtual_day < 1 || virtual_day > calendar_days.maturity {
        return 0.0;
    }
    let effective_day = adjusted_canopy_day_with_timing(crop, canopy_timing, virtual_day);
    let effective_gdd = adjusted_canopy_gdd_with_timing(crop, canopy_timing, sum_gdd);
    let growth_factor = 1.0 - f64::from(growth_reduction) / 100.0;
    let maximum_factor = 1.0 - f64::from(maximum_cover_reduction) / 100.0;
    if crop.cycle.uses_growing_degree_days() {
        return potential_canopy_gdd(
            crop.cycle.driving_calendar(),
            effective_gdd,
            initial,
            maximum,
            growth_factor,
            maximum_factor,
        );
    }
    potential_canopy_days(
        calendar_days,
        effective_day,
        initial,
        maximum,
        growth_factor,
        maximum_factor,
    )
}

pub(crate) fn adjusted_canopy_day_with_timing(
    crop: &PreparedCrop,
    timing: &CanopyTiming,
    day_after_planting: i32,
) -> i32 {
    if crop.parameters.planting_method != PlantingMethod::Regrowth {
        return day_after_planting;
    }
    let calendar_days = crop.cycle.calendar_days();
    let adjustment = timing
        .days_to_initial_cover
        .unwrap_or(timing.days_to_full_canopy - calendar_days.emergence);
    let mut effective = day_after_planting + adjustment + calendar_days.emergence;
    effective = effective.min(calendar_days.maturity);
    if effective > timing.days_to_full_canopy && day_after_planting <= calendar_days.senescence {
        let remaining = calendar_days.senescence - timing.days_to_full_canopy;
        let fraction =
            f64::from(remaining) / f64::from(adjustment + calendar_days.emergence + remaining);
        effective = timing.days_to_full_canopy
            + roundc_i32(
                fraction
                    * f64::from(
                        day_after_planting + adjustment + calendar_days.emergence
                            - timing.days_to_full_canopy,
                    ),
            );
    }
    if day_after_planting > calendar_days.senescence {
        day_after_planting
    } else {
        effective
    }
}

pub(crate) fn adjusted_canopy_gdd_with_timing(
    crop: &PreparedCrop,
    timing: &CanopyTiming,
    sum_gdd: f64,
) -> f64 {
    if crop.parameters.planting_method != PlantingMethod::Regrowth {
        return sum_gdd;
    }
    let driving_calendar = crop.cycle.driving_calendar();
    let adjustment = timing
        .gdd_to_initial_cover
        .unwrap_or(timing.gdd_to_full_canopy - driving_calendar.emergence);
    let mut effective = sum_gdd + f64::from(adjustment + driving_calendar.emergence);
    effective = effective.min(f64::from(driving_calendar.maturity));
    if effective > f64::from(timing.gdd_to_full_canopy)
        && sum_gdd <= f64::from(driving_calendar.senescence)
    {
        let remaining = driving_calendar.senescence - timing.gdd_to_full_canopy;
        let fraction =
            f64::from(remaining) / f64::from(adjustment + driving_calendar.emergence + remaining);
        effective = f64::from(timing.gdd_to_full_canopy)
            + f64::from(roundc_i32(
                fraction
                    * (sum_gdd + f64::from(adjustment + driving_calendar.emergence)
                        - f64::from(timing.gdd_to_full_canopy)),
            ));
    }
    if sum_gdd > f64::from(driving_calendar.senescence) {
        sum_gdd
    } else {
        effective
    }
}

/// Calculates potential canopy cover including fertility-driven mid-season decline.
///
/// `day_after_planting`, `delayed_days`, and the timing fields are calendar days.
/// `sum_gdd` and `gdd_to_full_canopy` are degree Celsius-days.
/// `canopy_decline` is the average canopy-cover decrease in
/// percentage points per day. Reduction fields in `timing` are percentages;
/// the returned canopy cover is a fraction of soil surface.
#[must_use]
pub fn fertility_adjusted_potential_canopy_cover(
    crop: &PreparedCrop,
    day_after_planting: i32,
    delayed_days: i32,
    sum_gdd: f64,
    timing: &FertilityCanopyTiming,
    gdd_to_full_canopy: i32,
    canopy_decline: f64,
) -> f64 {
    fertility_adjusted_potential_canopy_cover_with_timing(
        crop,
        &derive_canopy_timing(crop),
        day_after_planting,
        delayed_days,
        sum_gdd,
        timing,
        gdd_to_full_canopy,
        canopy_decline,
    )
}

#[expect(
    clippy::too_many_arguments,
    reason = "the explicit timing keeps runtime total canopy values separate from crop clocks"
)]
pub(crate) fn fertility_adjusted_potential_canopy_cover_with_timing(
    crop: &PreparedCrop,
    canopy_timing: &CanopyTiming,
    day_after_planting: i32,
    delayed_days: i32,
    sum_gdd: f64,
    timing: &FertilityCanopyTiming,
    gdd_to_full_canopy: i32,
    canopy_decline: f64,
) -> f64 {
    let base = potential_canopy_cover_with_timing(
        crop,
        canopy_timing,
        day_after_planting,
        delayed_days,
        sum_gdd,
        timing.growth_reduction,
        timing.maximum_cover_reduction,
    );
    if canopy_decline <= crate::compat::ZERO_THRESHOLD
        || timing.days_to_full_canopy >= crop.cycle.calendar_days().senescence
    {
        return base;
    }
    if crop.cycle.uses_growing_degree_days() {
        fertility_decline_gdd(
            crop,
            adjusted_canopy_gdd_with_timing(crop, canopy_timing, sum_gdd),
            timing,
            gdd_to_full_canopy,
            canopy_decline,
            base,
        )
    } else {
        fertility_decline_days(
            crop,
            adjusted_canopy_day_with_timing(crop, canopy_timing, day_after_planting - delayed_days),
            timing,
            canopy_decline,
            base,
        )
    }
}

fn fertility_decline_days(
    crop: &PreparedCrop,
    virtual_day: i32,
    timing: &FertilityCanopyTiming,
    decline: f64,
    base: f64,
) -> f64 {
    if virtual_day <= timing.days_to_full_canopy {
        return base;
    }
    let decline_start = timing.days_to_full_canopy;
    let calendar_days = crop.cycle.calendar_days();
    let senescence = calendar_days.senescence;
    if virtual_day < senescence {
        return (base
            - decline / 100.0 * f64::from(virtual_day - decline_start).powi(2)
                / f64::from(senescence - decline_start))
        .max(0.0);
    }
    late_fertility_decline(
        crop,
        f64::from(virtual_day - senescence),
        f64::from(senescence - calendar_days.emergence),
        f64::from(virtual_day - decline_start),
        f64::from(senescence - decline_start),
        calendar_days.canopy_growth_coefficient,
        calendar_days.canopy_decline_coefficient,
        1.0,
        timing,
        decline,
    )
}

fn fertility_decline_gdd(
    crop: &PreparedCrop,
    sum_gdd: f64,
    timing: &FertilityCanopyTiming,
    gdd_to_full_canopy: i32,
    decline: f64,
    base: f64,
) -> f64 {
    let calendar_days = crop.cycle.calendar_days();
    let driving_calendar = crop.cycle.driving_calendar();
    let senescence = f64::from(driving_calendar.senescence);
    let decline_start = f64::from(gdd_to_full_canopy);
    if sum_gdd <= decline_start || senescence <= decline_start {
        return base;
    }
    let day_per_gdd = f64::from(calendar_days.senescence - timing.days_to_full_canopy)
        / (senescence - decline_start);
    if sum_gdd < senescence {
        return (base
            - day_per_gdd * decline / 100.0 * (sum_gdd - decline_start).powi(2)
                / (senescence - decline_start))
            .max(0.0);
    }
    late_fertility_decline(
        crop,
        sum_gdd - senescence,
        senescence - f64::from(driving_calendar.emergence),
        sum_gdd - decline_start,
        senescence - decline_start,
        driving_calendar.canopy_growth_coefficient,
        driving_calendar.canopy_decline_coefficient,
        day_per_gdd,
        timing,
        decline,
    )
}

#[allow(clippy::too_many_arguments)]
fn late_fertility_decline(
    crop: &PreparedCrop,
    time_since_senescence: f64,
    growth_time_to_senescence: f64,
    time_since_full_canopy: f64,
    growth_period_after_full_canopy: f64,
    growth_coefficient: f64,
    decline_coefficient: f64,
    time_scale: f64,
    timing: &FertilityCanopyTiming,
    fertility_decline: f64,
) -> f64 {
    let adjusted_maximum = (1.0 - f64::from(timing.maximum_cover_reduction) / 100.0)
        * crop.parameters.canopy.maximum_cover;
    let cover_at_senescence = canopy_at_time(
        growth_time_to_senescence,
        initial_canopy_cover(crop),
        growth_coefficient * (1.0 - f64::from(timing.growth_reduction) / 100.0),
        adjusted_maximum,
    );
    let continuing_decline = cover_at_senescence
        - time_scale * fertility_decline / 100.0 * time_since_full_canopy.powi(2)
            / growth_period_after_full_canopy;
    if continuing_decline < 0.0 {
        return 0.0;
    }
    let adjusted_cover_at_senescence = cover_at_senescence
        - time_scale * fertility_decline / 100.0 * growth_period_after_full_canopy;
    if adjusted_cover_at_senescence < 0.001 {
        return 0.0;
    }
    let adjusted_decline = decline_coefficient * (adjusted_cover_at_senescence + 2.29)
        / (crop.parameters.canopy.maximum_cover + 2.29);
    let result = (adjusted_cover_at_senescence
        * (1.0
            - 0.05
                * ((time_since_senescence * adjusted_decline * 3.33
                    / (adjusted_cover_at_senescence + 2.29))
                    .exp()
                    - 1.0)))
        .max(0.0);
    // Apply the late-season decline ceiling every day.
    result.min(continuing_decline)
}

/// Canopy cover reached after an elapsed growth time.
///
/// `time` is in calendar days or degree-days and `growth` is per matching time
/// unit. `initial`, `maximum`, and the result are soil-cover fractions.
#[must_use]
pub fn canopy_at_time(time: f64, initial: f64, growth: f64, maximum: f64) -> f64 {
    let mut cover = initial * (growth * time).exp();
    if cover > maximum / 2.0 {
        cover = maximum - 0.25 * (maximum / initial) * maximum * (-growth * time).exp();
    }
    cover
}

/// Fraction of maximum canopy retained by perennial self-thinning.
///
/// `season_year` and `years_to_ninety` are counts of growing seasons;
/// `shape` and the result are dimensionless.
#[must_use]
pub fn perennial_maximum_cover_multiplier(
    season_year: i32,
    years_to_ninety: i32,
    shape: f64,
) -> f64 {
    if season_year <= 1 || years_to_ninety <= 1 {
        return 1.0;
    }
    let limiting_year = 1.0 + f64::from(years_to_ninety - 1) * (shape * 10.0_f64.ln()).exp();
    if f64::from(season_year) >= limiting_year {
        0.0
    } else {
        0.9 + 0.1
            * (1.0
                - ((f64::from(season_year - 1) / f64::from(years_to_ninety - 1)).ln() / shape)
                    .exp())
    }
}

/// Fraction of the initial regrowth canopy retained as a perennial ages.
///
/// `season_year` and `years_to_ninety` are counts of growing seasons;
/// `shape` and the result are dimensionless.
#[must_use]
pub fn perennial_initial_cover_multiplier(
    season_year: i32,
    years_to_ninety: i32,
    shape: f64,
) -> f64 {
    if season_year <= 1 || years_to_ninety <= 1 {
        return 1.0;
    }
    let initial_zero_year = 1.0 + f64::from(years_to_ninety - 1) * (shape * 10.0_f64.ln()).exp();
    (1.0 - f64::from(season_year - 1) / (initial_zero_year - 1.0)).max(0.0)
}

fn potential_canopy_days(
    calendar_days: &DevelopmentCalendar,
    virtual_day: i32,
    initial: f64,
    maximum: f64,
    growth_factor: f64,
    maximum_factor: f64,
) -> f64 {
    if virtual_day < 1
        || virtual_day > calendar_days.maturity
        || initial <= f64::EPSILON
        || virtual_day <= calendar_days.emergence
    {
        return 0.0;
    }
    let adjusted_maximum = maximum_factor * maximum;
    let cover = if virtual_day < calendar_days.senescence {
        canopy_at_time(
            f64::from(virtual_day - calendar_days.emergence),
            initial,
            growth_factor * calendar_days.canopy_growth_coefficient,
            adjusted_maximum,
        )
    } else if maximum < 0.001 {
        0.0
    } else {
        let cover_at_senescence = canopy_at_time(
            f64::from(calendar_days.senescence - calendar_days.emergence),
            initial,
            growth_factor * calendar_days.canopy_growth_coefficient,
            adjusted_maximum,
        );
        if cover_at_senescence < 0.001 {
            0.0
        } else {
            let adjusted_decline = calendar_days.canopy_decline_coefficient
                * (cover_at_senescence + 2.29)
                / (maximum + 2.29);
            cover_at_senescence
                * (1.0
                    - 0.05
                        * (((f64::from(virtual_day - calendar_days.senescence)
                            * 3.33
                            * adjusted_decline)
                            / (cover_at_senescence + 2.29))
                            .exp()
                            - 1.0))
        }
    };
    if cover > 1.0 {
        1.0
    } else if cover < f64::EPSILON {
        0.0
    } else {
        cover
    }
}

fn potential_canopy_gdd(
    driving_calendar: &DevelopmentCalendar,
    sum_gdd: f64,
    initial: f64,
    maximum: f64,
    growth_factor: f64,
    maximum_factor: f64,
) -> f64 {
    if sum_gdd <= 0.0
        || roundc_i32(sum_gdd) > driving_calendar.maturity
        || initial <= 0.0
        || sum_gdd <= f64::from(driving_calendar.emergence)
    {
        return 0.0;
    }
    let adjusted_maximum = maximum_factor * maximum;
    let cover = if sum_gdd < f64::from(driving_calendar.senescence) {
        canopy_at_time(
            sum_gdd - f64::from(driving_calendar.emergence),
            initial,
            growth_factor * driving_calendar.canopy_growth_coefficient,
            adjusted_maximum,
        )
    } else if maximum < 0.001 {
        0.0
    } else {
        let cover_at_senescence = canopy_at_time(
            f64::from(driving_calendar.senescence - driving_calendar.emergence),
            initial,
            growth_factor * driving_calendar.canopy_growth_coefficient,
            adjusted_maximum,
        );
        if cover_at_senescence < 0.001 {
            0.0
        } else {
            let adjusted_decline = driving_calendar.canopy_decline_coefficient
                * (cover_at_senescence + 2.29)
                / (maximum + 2.29);
            cover_at_senescence
                * (1.0
                    - 0.05
                        * ((((sum_gdd - f64::from(driving_calendar.senescence))
                            * 3.33
                            * adjusted_decline)
                            / (cover_at_senescence + 2.29))
                            .exp()
                            - 1.0))
        }
    };
    cover.clamp(0.0, 1.0)
}

fn initial_canopy_cover(crop: &PreparedCrop) -> f64 {
    crop.parameters.canopy.initial_seedling_cover
}

#[cfg(test)]
mod tests {
    use super::{canopy_at_time, relative_weed_cover, weed_canopy_multiplier};

    #[test]
    fn canopy_growth_uses_exponential_branch_before_half_cover() {
        let cover = canopy_at_time(1.0, 0.01, 0.1, 0.8);

        assert!((cover - 0.01 * 0.1_f64.exp()).abs() < 1.0e-12);
    }

    #[test]
    fn canopy_growth_uses_saturation_branch_after_half_cover() {
        let cover = canopy_at_time(50.0, 0.01, 0.1, 0.8);

        assert!(cover > 0.4);
        assert!(cover < 0.8);
    }

    #[test]
    fn complete_weed_cover_expands_total_canopy_to_complete_ground_cover() {
        let multiplier = weed_canopy_multiplier(100, 0.8, 1.0);

        assert!((multiplier * 0.8 - 1.0).abs() < 1.0e-12);
    }

    #[test]
    fn weed_cover_change_is_linear_between_full_canopy_and_senescence() {
        let cover = relative_weed_cover(20.0, 50, 60.0, 40.0, 80.0);

        assert!((cover - 25.0).abs() < 1.0e-12);
    }
}
