//! Cutting-schedule criteria and post-cutting state resets.

use crate::management::{CuttingCriterion, CuttingManagement};

/// Production accumulated since the preceding cutting.
#[derive(Debug)]
pub struct CuttingIntervalState {
    /// Calendar days since the preceding cutting.
    pub days: i32,
    /// Growing degree-days since the preceding cutting.
    pub growing_degree_days: f64,
    /// Cumulative biomass recorded at the preceding cutting, in tonnes per hectare.
    pub previous_biomass: f64,
    /// Cumulative dry yield recorded at the preceding cutting, in tonnes per hectare.
    pub previous_yield: f64,
    /// Number of cuttings completed in the current crop cycle.
    pub count: i32,
    /// Crop-cycle day of the latest cutting.
    pub last_cut_day: i32,
}

/// Current production values used by generated cutting criteria.
#[derive(Debug)]
pub struct CuttingInputs {
    /// Current crop-cycle day.
    pub day_in_season: i32,
    /// Growing degree-days accumulated today.
    pub daily_gdd: f64,
    /// Cumulative dry biomass, in tonnes per hectare.
    pub biomass: f64,
    /// Cumulative dry yield, in tonnes per hectare.
    pub dry_yield: f64,
    /// Dry matter of fresh yield, in percent; `None` disables fresh-yield triggering.
    pub dry_matter: Option<i8>,
}

impl CuttingIntervalState {
    /// Creates an empty cutting interval.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            days: 0,
            growing_degree_days: 0.0,
            previous_biomass: 0.0,
            previous_yield: 0.0,
            count: 0,
            last_cut_day: 0,
        }
    }
}

impl Default for CuttingIntervalState {
    fn default() -> Self {
        Self::new()
    }
}

/// Advances interval counters before testing the current cutting criterion.
///
/// `daily_gdd` is the current day's thermal time in degree Celsius-days.
pub fn advance_cutting_interval(state: &mut CuttingIntervalState, daily_gdd: f64) {
    state.days += 1;
    state.growing_degree_days += daily_gdd;
}

/// Tests one generated cutting target within its active calendar window.
///
/// `from_day` and `through_day` are one-based crop-cycle days. `target` uses
/// the unit selected by `criterion`: days, degree Celsius-days, or tonnes per
/// hectare. State and input fields retain their documented units.
#[must_use]
pub fn generated_cutting_due(
    state: &CuttingIntervalState,
    inputs: &CuttingInputs,
    criterion: &CuttingCriterion,
    target: f64,
    from_day: i32,
    through_day: i32,
) -> bool {
    if inputs.day_in_season < from_day || inputs.day_in_season > through_day {
        return false;
    }
    match criterion {
        CuttingCriterion::CalendarDays => f64::from(state.days) >= target,
        CuttingCriterion::GrowingDegreeDays => state.growing_degree_days >= target,
        CuttingCriterion::DryBiomass => inputs.biomass - state.previous_biomass >= target,
        CuttingCriterion::DryYield => inputs.dry_yield - state.previous_yield >= target,
        CuttingCriterion::FreshYield => inputs.dry_matter.is_some_and(|dry_matter| {
            (inputs.dry_yield - state.previous_yield) / (f64::from(dry_matter) / 100.0) >= target
        }),
        CuttingCriterion::NotApplicable => false,
    }
}

/// Tests the current record of an explicitly listed cutting schedule.
///
/// `record_index` is a zero-based record index. Both day-number arguments are
/// absolute AquaCrop day numbers.
#[must_use]
pub fn fixed_cutting_due(
    management: &CuttingManagement,
    record_index: usize,
    current_day_number: i32,
    crop_first_day_number: i32,
) -> bool {
    let Some(record) = management.records.get(record_index) else {
        return false;
    };
    let origin = management
        .first_list_day_number
        .unwrap_or(crop_first_day_number);
    let day_in_schedule = current_day_number - origin + 1;
    day_in_schedule >= record.from_day
}

/// Records a cutting and resets all interval-based criteria.
///
/// `previous_canopy_cover` is a soil-cover fraction and
/// `canopy_after_cutting` is percent soil cover. Input fields retain
/// the units documented on [`CuttingInputs`].
pub fn record_cutting(
    state: &mut CuttingIntervalState,
    inputs: &CuttingInputs,
    previous_canopy_cover: &mut f64,
    canopy_after_cutting: i32,
) {
    state.count += 1;
    state.last_cut_day = inputs.day_in_season;
    state.days = 0;
    state.growing_degree_days = 0.0;
    state.previous_biomass = inputs.biomass;
    state.previous_yield = inputs.dry_yield;
    *previous_canopy_cover = previous_canopy_cover.min(f64::from(canopy_after_cutting) / 100.0);
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64;

    use super::{CuttingInputs, CuttingIntervalState, generated_cutting_due, record_cutting};
    use crate::management::CuttingCriterion;

    #[test]
    fn dry_biomass_criterion_uses_production_since_the_previous_cut() {
        let mut state = CuttingIntervalState::new();
        state.previous_biomass = 2.0;
        let inputs = CuttingInputs {
            day_in_season: 50,
            daily_gdd: 12.0,
            biomass: 3.5,
            dry_yield: 0.0,
            dry_matter: None,
        };

        assert!(generated_cutting_due(
            &state,
            &inputs,
            &CuttingCriterion::DryBiomass,
            1.5,
            40,
            60
        ));
    }

    #[test]
    fn recording_a_cut_resets_interval_and_caps_canopy() {
        let mut state = CuttingIntervalState::new();
        state.days = 10;
        state.growing_degree_days = 100.0;
        let inputs = CuttingInputs {
            day_in_season: 50,
            daily_gdd: 12.0,
            biomass: 3.5,
            dry_yield: 2.0,
            dry_matter: Some(20),
        };
        let mut canopy = 0.8;
        record_cutting(&mut state, &inputs, &mut canopy, 30);

        assert_eq!(state.days, 0);
        assert_eq_f64!(state.growing_degree_days, 0.0_f64);
        assert_eq_f64!(canopy, 0.3_f64);
    }
}
