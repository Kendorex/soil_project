//! Field-management and cutting parameters.

/// Criterion used to generate repeated cuttings.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum CuttingCriterion {
    /// No generated-cutting criterion.
    #[default]
    NotApplicable,
    /// Interval in calendar days.
    CalendarDays,
    /// Interval in growing degree-days.
    GrowingDegreeDays,
    /// Produced dry above-ground biomass, in tonnes per hectare.
    DryBiomass,
    /// Produced dry yield, in tonnes per hectare.
    DryYield,
    /// Produced fresh yield, in tonnes per hectare.
    FreshYield,
}

/// One fixed or generated cutting instruction.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CuttingRecord {
    /// First day in the crop cycle on which the instruction applies.
    pub from_day: i32,
    /// Interval or mass criterion for generated schedules; absent for fixed harvest days.
    pub target: Option<f64>,
}

/// Multiple-cutting configuration.
///
/// [`Default`] disables cutting and uses AquaCrop's inert values for the remaining fields.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CuttingManagement {
    /// Whether multiple cuttings are considered.
    pub considered: bool,
    /// Canopy cover retained after cutting, in percent.
    pub canopy_cover_after_cutting: i32,
    /// First day in the generation window.
    pub first_window_day: i32,
    /// Window length in days, or `None` for the full crop cycle.
    pub window_length: Option<i32>,
    /// Whether cuttings are generated from a criterion instead of explicitly listed.
    pub generated: bool,
    /// Criterion for a generated schedule.
    pub criterion: CuttingCriterion,
    /// Whether crop maturity triggers a final harvest.
    pub harvest_at_maturity: bool,
    /// Absolute day number corresponding to day one of listed cuttings, when explicitly set.
    pub first_list_day_number: Option<i32>,
    /// Fixed cutting days or generated-schedule records.
    pub records: Vec<CuttingRecord>,
}

impl Default for CuttingManagement {
    fn default() -> Self {
        Self {
            considered: false,
            canopy_cover_after_cutting: 30,
            first_window_day: 1,
            window_length: None,
            generated: false,
            criterion: CuttingCriterion::default(),
            harvest_at_maturity: false,
            first_list_day_number: None,
            records: Vec::new(),
        }
    }
}

/// Field-management configuration.
///
/// [`Default`] reproduces AquaCrop's `NoManagement` state for the supported version. Its public
/// fields, including [`Self::fertility_stress`], can be replaced by direct consumers before
/// initializing a simulation.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct Management {
    /// Ground surface covered by mulch during the crop period, in percent.
    pub mulch_cover: i8,
    /// Mulch reduction of soil evaporation during the crop period, in percent.
    pub mulch_evaporation_reduction: i8,
    /// Soil-fertility stress level, in percent.
    pub fertility_stress: i32,
    /// Soil-bund height, in metres.
    pub bund_height: f64,
    /// Whether surface runoff remains enabled.
    pub runoff_enabled: bool,
    /// Curve-number correction, in percent.
    pub curve_number_correction: i32,
    /// Relative weed cover at canopy closure, in percent.
    pub weed_cover: i8,
    /// Mid-season change in relative weed cover, in percentage points.
    pub weed_cover_delta: i32,
    /// Shape factor for canopy expansion under weed infestation.
    pub weed_canopy_shape: f64,
    /// Replacement by weeds of self-thinned perennial cover, in percent.
    pub weed_self_thinning_replacement: i8,
    /// Multiple-cutting configuration.
    pub cuttings: CuttingManagement,
}

impl Default for Management {
    fn default() -> Self {
        Self {
            mulch_cover: 0,
            mulch_evaporation_reduction: 50,
            fertility_stress: 0,
            bund_height: 0.0,
            runoff_enabled: true,
            curve_number_correction: 0,
            weed_cover: 0,
            weed_cover_delta: 0,
            weed_canopy_shape: -0.01,
            weed_self_thinning_replacement: 100,
            cuttings: CuttingManagement::default(),
        }
    }
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64_as_result;

    use super::*;

    #[test]
    fn default_disables_field_management_practices() {
        let management = Management::default();

        assert!(
            management.mulch_cover == 0
                && management.mulch_evaporation_reduction == 50
                && management.fertility_stress == 0
                && assert_eq_f64_as_result!(management.bund_height, 0.0_f64).is_ok()
                && management.runoff_enabled
                && management.curve_number_correction == 0
                && management.weed_cover == 0
                && management.weed_cover_delta == 0
                && assert_eq_f64_as_result!(management.weed_canopy_shape, (-0.01_f64)).is_ok()
                && management.weed_self_thinning_replacement == 100
                && !management.cuttings.considered
                && management.cuttings.canopy_cover_after_cutting == 30
                && management.cuttings.first_window_day == 1
                && management.cuttings.window_length.is_none()
                && !management.cuttings.generated
                && management.cuttings.criterion == CuttingCriterion::NotApplicable
                && !management.cuttings.harvest_at_maturity
                && management.cuttings.first_list_day_number.is_none()
                && management.cuttings.records.is_empty()
        );
    }

    #[test]
    fn default_fertility_stress_can_be_replaced() {
        let management = Management {
            fertility_stress: 35,
            ..Management::default()
        };

        assert_eq!(management.fertility_stress, 35);
    }
}
