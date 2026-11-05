//! Crop input and prepared parameters in reference-model units.

use crate::Error;
use crate::canopy::time_to_canopy_cover;
use crate::climate::{GrowingDegreeDayMethod, TemperatureExtrema, growing_degree_days};
use crate::compat::roundc_i32;

/// Crop product category.
#[derive(Clone, Debug, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum CropKind {
    /// Leaf or stem biomass is harvested.
    Vegetative,
    /// Grain or fruit is harvested.
    Grain,
    /// Underground storage organ is harvested.
    Tuber,
    /// Forage crop with a perennial growing-period definition.
    Forage,
}

/// Establishment method used by the crop.
#[derive(Clone, Debug, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum PlantingMethod {
    /// Direct seeding.
    Seed,
    /// Transplanting.
    Transplant,
    /// Regrowth from an established crop.
    Regrowth,
}

/// Method used to adjust soil-water depletion thresholds for reference ET.
#[derive(Clone, Debug, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum DepletionAdjustment {
    /// Keep the crop-file thresholds unchanged.
    None,
    /// Apply the FAO reference-ET correction.
    Fao,
}

/// Crop temperature thresholds.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CropTemperature {
    /// Base temperature for development, in degrees Celsius.
    pub base: f64,
    /// Upper temperature threshold for development, in degrees Celsius.
    pub upper: f64,
    /// Minimum temperature for pollination, in degrees Celsius, when cold stress is considered.
    pub pollination_minimum: Option<i8>,
    /// Maximum temperature for pollination, in degrees Celsius, when heat stress is considered.
    pub pollination_maximum: Option<i8>,
    /// Minimum daily GDD for full transpiration, in degree Celsius-days, when considered.
    pub full_transpiration_gdd: Option<f64>,
}

/// Crop water-stress thresholds and response shapes.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CropWaterStress {
    /// Upper depletion threshold for canopy expansion.
    pub canopy_expansion_upper: f64,
    /// Lower depletion threshold for canopy expansion.
    pub canopy_expansion_lower: f64,
    /// Canopy-expansion stress-response shape.
    pub canopy_expansion_shape: f64,
    /// Upper depletion threshold for stomatal closure.
    pub stomatal_closure_upper: f64,
    /// Stomatal-closure stress-response shape.
    pub stomatal_closure_shape: f64,
    /// Upper depletion threshold for canopy senescence.
    pub senescence_upper: f64,
    /// Senescence stress-response shape.
    pub senescence_shape: f64,
    /// Cumulative reference ET that delays permanent wilting, in millimetres.
    pub eto_delay_senescence: i32,
    /// Upper depletion threshold for pollination failure, when considered.
    pub pollination_upper: Option<f64>,
    /// Difference below saturation at which aeration stress begins, in volume percent.
    pub anaerobic_point_volume: i32,
}

/// Calibrated crop response to soil-fertility stress.
#[derive(Clone, Debug)]
pub struct PreparedCropFertilityStress {
    /// Fertility stress used during calibration, in percent.
    pub calibration_stress: i8,
    /// Response shape for canopy growth.
    pub canopy_growth_shape: f64,
    /// Response shape for maximum canopy cover.
    pub maximum_canopy_shape: f64,
    /// Response shape for water productivity.
    pub water_productivity_shape: f64,
    /// Response shape for canopy decline.
    pub canopy_decline_shape: f64,
    /// Whether at least one response shape declares a calibrated response.
    pub calibrated: bool,
}

/// User-supplied crop response to soil-fertility stress.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CropFertilityStress {
    /// Fertility stress used during calibration, in percent.
    pub calibration_stress: i8,
    /// Response shape for canopy growth.
    pub canopy_growth_shape: f64,
    /// Response shape for maximum canopy cover.
    pub maximum_canopy_shape: f64,
    /// Response shape for water productivity.
    pub water_productivity_shape: f64,
    /// Response shape for canopy decline.
    pub canopy_decline_shape: f64,
}

/// Crop response parameters for salinity stress.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CropSalinityStress {
    /// Soil `ECe` at which stress begins, in dS/m, when salinity stress is considered.
    pub ece_minimum: Option<i8>,
    /// Soil `ECe` at which growth ceases, in dS/m, when salinity stress is considered.
    pub ece_maximum: Option<i8>,
    /// Calibrated canopy-cover distortion, in percent.
    pub canopy_distortion: i8,
    /// Calibrated stomatal response to soil-water EC, in percent.
    pub stomatal_response: i32,
}

/// Root-depth and extraction parameters.
#[derive(Clone, Debug)]
pub struct PreparedCropRooting {
    /// Minimum effective rooting depth, in metres.
    pub minimum_depth: f64,
    /// Maximum effective rooting depth, in metres.
    pub maximum_depth: f64,
    /// Ten times the root-function shape parameter.
    pub shape: i8,
    /// Maximum extraction in the top quarter, in m3/m3/day.
    pub extraction_top_quarter: f64,
    /// Maximum extraction in the bottom quarter, in m3/m3/day.
    pub extraction_bottom_quarter: f64,
    /// Derived maximum extraction at the top, in m3/m3/day.
    pub extraction_top: f64,
    /// Derived maximum extraction at the bottom, in m3/m3/day.
    pub extraction_bottom: f64,
    /// Minimum effective rooting depth in the first year, in metres.
    pub first_year_minimum_depth: f64,
    /// Whether a perennial is sown rather than transplanted in its first year.
    pub sown_in_first_year: bool,
}

/// User-supplied root-depth and extraction parameters.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CropRooting {
    /// Minimum effective rooting depth, in metres.
    pub minimum_depth: f64,
    /// Maximum effective rooting depth, in metres.
    pub maximum_depth: f64,
    /// Ten times the root-function shape parameter.
    pub shape: i8,
    /// Maximum extraction in the top quarter, in m3/m3/day.
    pub extraction_top_quarter: f64,
    /// Maximum extraction in the bottom quarter, in m3/m3/day.
    pub extraction_bottom_quarter: f64,
    /// Minimum effective rooting depth in the first year, in metres.
    pub first_year_minimum_depth: f64,
    /// Whether a perennial is sown rather than transplanted in its first year.
    pub sown_in_first_year: bool,
}

/// Transfer of perennial assimilates between shoots and roots.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct AssimilateTransfer {
    /// Whether transfer is enabled.
    pub enabled: bool,
    /// Storage period at the end of the season, in days.
    pub storage_period: i32,
    /// Assimilates stored in roots on the last day, in percent.
    pub stored: i8,
    /// Stored assimilates mobilized next season, in percent.
    pub mobilized: i8,
}

/// Crop evapotranspiration parameters.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CropEvapotranspiration {
    /// Maximum transpiration crop coefficient before senescence.
    pub kc_top: f64,
    /// Cumulative decrease in `KcTr,x` at maturity, in percent.
    pub kc_decline_cumulative: f64,
    /// Canopy effect reducing late-season soil evaporation, in percent.
    pub late_season_evaporation_reduction: i32,
}

/// Canopy-development parameters.
#[derive(Clone, Debug)]
pub struct PreparedCropCanopy {
    /// Soil area covered by one seedling at 90 percent emergence, in cm2.
    pub seedling_size: f64,
    /// Canopy size of one regrowing plant on its first day, in cm2.
    pub regrowth_plant_size: f64,
    /// Plant population per hectare.
    pub planting_density: i32,
    /// Derived initial canopy cover for seedlings, as a soil-cover fraction.
    pub initial_seedling_cover: f64,
    /// Derived initial canopy cover for regrowth, as a soil-cover fraction.
    pub initial_regrowth_cover: f64,
    /// Years until self-thinning reduces maximum cover to 90 percent, for perennials.
    pub self_thinning_years: Option<i8>,
    /// Shape of the perennial self-thinning response, for perennials.
    pub self_thinning_shape: Option<f64>,
    /// Maximum canopy cover as a soil-cover fraction.
    pub maximum_cover: f64,
}

/// User-supplied canopy-development parameters.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CropCanopy {
    /// Soil area covered by one seedling at 90 percent emergence, in cm2.
    pub seedling_size: f64,
    /// Canopy size of one regrowing plant on its first day, in cm2.
    pub regrowth_plant_size: f64,
    /// Plant population per hectare.
    pub planting_density: i32,
    /// Years until self-thinning reduces maximum cover to 90 percent, for perennials.
    pub self_thinning_years: Option<i8>,
    /// Shape of the perennial self-thinning response, for perennials.
    pub self_thinning_shape: Option<f64>,
    /// Maximum canopy cover as a soil-cover fraction.
    pub maximum_cover: f64,
}

/// Crop-development milestones and canopy rates expressed in one time scale.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct DevelopmentCalendar {
    /// Time from planting to emergence.
    pub emergence: i32,
    /// Time from planting to maximum rooting depth.
    pub maximum_rooting: i32,
    /// Time from planting to senescence.
    pub senescence: i32,
    /// Time in the complete crop cycle.
    pub maturity: i32,
    /// Time from planting to flowering or yield formation.
    pub flowering: i32,
    /// Length of the flowering stage.
    pub flowering_length: i32,
    /// Time to build the reference harvest index, when applicable.
    pub harvest_index: Option<i32>,
    /// Canopy growth coefficient per unit of this calendar's time scale.
    pub canopy_growth_coefficient: f64,
    /// Canopy decline coefficient per unit of this calendar's time scale.
    pub canopy_decline_coefficient: f64,
}

impl DevelopmentCalendar {
    /// Time between the onset of senescence and maturity.
    #[must_use]
    pub fn senescence_to_maturity(&self) -> i32 {
        self.maturity - self.senescence
    }
}

/// Optional calendar-day representation supplied alongside a GDD crop cycle.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct PartialDevelopmentCalendar {
    /// Calendar days from planting to emergence, when supplied.
    pub emergence: Option<i32>,
    /// Calendar days from planting to maximum rooting depth, when supplied.
    pub maximum_rooting: Option<i32>,
    /// Calendar days from planting to senescence, when supplied.
    pub senescence: Option<i32>,
    /// Calendar days in the complete crop cycle, when supplied.
    pub maturity: Option<i32>,
    /// Calendar days from planting to flowering or yield formation, when supplied.
    pub flowering: Option<i32>,
    /// Length of the flowering stage in calendar days, when supplied.
    pub flowering_length: Option<i32>,
    /// Calendar days needed to build the reference harvest index, when supplied.
    pub harvest_index: Option<i32>,
    /// Canopy growth coefficient per calendar day, when supplied.
    pub canopy_growth_coefficient: Option<f64>,
    /// Canopy decline coefficient per calendar day, when supplied.
    pub canopy_decline_coefficient: Option<f64>,
}

impl PartialDevelopmentCalendar {
    /// Converts supplied values into a complete calendar-day representation.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when a required calendar-day milestone or canopy
    /// coefficient was not supplied.
    pub fn try_into_complete(self) -> Result<DevelopmentCalendar, Error> {
        Ok(DevelopmentCalendar {
            emergence: self.emergence.ok_or(Error::InvalidCropConfiguration(
                "GDD crop calendar is missing calendar-day emergence",
            ))?,
            maximum_rooting: self.maximum_rooting.ok_or(Error::InvalidCropConfiguration(
                "GDD crop calendar is missing calendar-day maximum rooting",
            ))?,
            senescence: self.senescence.ok_or(Error::InvalidCropConfiguration(
                "GDD crop calendar is missing calendar-day senescence",
            ))?,
            maturity: self.maturity.ok_or(Error::InvalidCropConfiguration(
                "GDD crop calendar is missing calendar-day maturity",
            ))?,
            flowering: self.flowering.ok_or(Error::InvalidCropConfiguration(
                "GDD crop calendar is missing calendar-day flowering",
            ))?,
            flowering_length: self
                .flowering_length
                .ok_or(Error::InvalidCropConfiguration(
                    "GDD crop calendar is missing calendar-day flowering length",
                ))?,
            harvest_index: self.harvest_index,
            canopy_growth_coefficient: self.canopy_growth_coefficient.ok_or(
                Error::InvalidCropConfiguration(
                    "GDD crop calendar is missing its daily canopy-growth coefficient",
                ),
            )?,
            canopy_decline_coefficient: self.canopy_decline_coefficient.ok_or(
                Error::InvalidCropConfiguration(
                    "GDD crop calendar is missing its daily canopy-decline coefficient",
                ),
            )?,
        })
    }
}

/// User-supplied time basis, development calendar, and canopy rates for a crop cycle.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(
    feature = "serde",
    serde(
        tag = "type",
        rename_all = "snake_case",
        rename_all_fields = "camelCase"
    )
)]
pub enum CropCycle {
    /// Development is driven by calendar days.
    CalendarDays {
        /// Calendar-day development milestones and canopy rates.
        calendar_days: DevelopmentCalendar,
    },
    /// Development is driven by growing degree-days.
    GrowingDegreeDays {
        /// Complete development milestones and canopy rates in calendar days.
        calendar_days: DevelopmentCalendar,
        /// Development milestones and canopy rates in growing degree-days.
        gdd: DevelopmentCalendar,
    },
    /// Development is driven by growing degree-days, but its calendar-day
    /// representation still depends on seasonal temperatures.
    UnresolvedGrowingDegreeDays {
        /// Supplied calendar-day values, with season-dependent values omitted.
        calendar_days: PartialDevelopmentCalendar,
        /// Development milestones and canopy rates in growing degree-days.
        gdd: DevelopmentCalendar,
    },
}

impl CropCycle {
    /// Returns whether crop development is driven by growing degree-days.
    #[must_use]
    pub const fn uses_growing_degree_days(&self) -> bool {
        matches!(
            self,
            Self::GrowingDegreeDays { .. } | Self::UnresolvedGrowingDegreeDays { .. }
        )
    }

    /// Returns the complete calendar-day representation when one is available.
    #[must_use]
    pub const fn calendar_days(&self) -> Option<&DevelopmentCalendar> {
        match self {
            Self::CalendarDays { calendar_days }
            | Self::GrowingDegreeDays { calendar_days, .. } => Some(calendar_days),
            Self::UnresolvedGrowingDegreeDays { .. } => None,
        }
    }

    /// Returns the mutable complete calendar-day representation when available.
    #[must_use]
    pub const fn calendar_days_mut(&mut self) -> Option<&mut DevelopmentCalendar> {
        match self {
            Self::CalendarDays { calendar_days }
            | Self::GrowingDegreeDays { calendar_days, .. } => Some(calendar_days),
            Self::UnresolvedGrowingDegreeDays { .. } => None,
        }
    }

    /// Returns the partial calendar-day representation supplied for a GDD crop.
    #[must_use]
    pub const fn partial_calendar_days(&self) -> Option<&PartialDevelopmentCalendar> {
        match self {
            Self::CalendarDays { .. } | Self::GrowingDegreeDays { .. } => None,
            Self::UnresolvedGrowingDegreeDays { calendar_days, .. } => Some(calendar_days),
        }
    }

    /// Returns the mutable partial calendar-day representation supplied for a GDD crop.
    #[must_use]
    pub const fn partial_calendar_days_mut(&mut self) -> Option<&mut PartialDevelopmentCalendar> {
        match self {
            Self::CalendarDays { .. } | Self::GrowingDegreeDays { .. } => None,
            Self::UnresolvedGrowingDegreeDays { calendar_days, .. } => Some(calendar_days),
        }
    }

    /// Returns the calendar that drives crop development.
    #[must_use]
    pub const fn driving_calendar(&self) -> &DevelopmentCalendar {
        match self {
            Self::CalendarDays { calendar_days } => calendar_days,
            Self::GrowingDegreeDays { gdd, .. } | Self::UnresolvedGrowingDegreeDays { gdd, .. } => {
                gdd
            }
        }
    }

    /// Returns the mutable calendar that drives crop development.
    #[must_use]
    pub const fn driving_calendar_mut(&mut self) -> &mut DevelopmentCalendar {
        match self {
            Self::CalendarDays { calendar_days } => calendar_days,
            Self::GrowingDegreeDays { gdd, .. } | Self::UnresolvedGrowingDegreeDays { gdd, .. } => {
                gdd
            }
        }
    }

    /// Returns the growing-degree-day representation when it drives development.
    #[must_use]
    pub const fn gdd(&self) -> Option<&DevelopmentCalendar> {
        match self {
            Self::CalendarDays { .. } => None,
            Self::GrowingDegreeDays { gdd, .. } | Self::UnresolvedGrowingDegreeDays { gdd, .. } => {
                Some(gdd)
            }
        }
    }

    /// Returns the mutable growing-degree-day representation when it drives development.
    #[must_use]
    pub const fn gdd_mut(&mut self) -> Option<&mut DevelopmentCalendar> {
        match self {
            Self::CalendarDays { .. } => None,
            Self::GrowingDegreeDays { gdd, .. } | Self::UnresolvedGrowingDegreeDays { gdd, .. } => {
                Some(gdd)
            }
        }
    }
}

/// GDD convention and optional temperature data used to adapt a crop to one season.
#[derive(Clone, Copy, Debug)]
pub struct CropSeason<'a, T: TemperatureExtrema = (f64, f64)> {
    /// Method used to calculate daily growing degree-days.
    pub gdd_method: GrowingDegreeDayMethod,
    /// Daily temperatures beginning on the first day of the crop cycle, when required.
    pub temperatures: Option<&'a [T]>,
}

impl Default for CropSeason<'_, (f64, f64)> {
    fn default() -> Self {
        Self {
            gdd_method: GrowingDegreeDayMethod::default(),
            temperatures: None,
        }
    }
}

impl CropSeason<'_, (f64, f64)> {
    /// Creates seasonal input for a complete GDD crop calendar.
    #[must_use]
    pub const fn new(gdd_method: GrowingDegreeDayMethod) -> Self {
        Self {
            gdd_method,
            temperatures: None,
        }
    }
}

impl<'a, T: TemperatureExtrema> CropSeason<'a, T> {
    /// Creates seasonal input for an incomplete GDD crop calendar.
    ///
    /// Temperatures begin on the first day of the crop cycle.
    #[must_use]
    pub const fn new_with_temperatures(
        gdd_method: GrowingDegreeDayMethod,
        temperatures: &'a [T],
    ) -> Self {
        Self {
            gdd_method,
            temperatures: Some(temperatures),
        }
    }

    fn require_temperatures(self) -> Result<&'a [T], Error> {
        self.temperatures.ok_or(Error::InvalidCropConfiguration(
            "an incomplete GDD crop calendar requires seasonal temperatures",
        ))
    }
}

/// Fully defined crop-cycle calendars selected for one season.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(
    feature = "serde",
    serde(
        tag = "type",
        rename_all = "snake_case",
        rename_all_fields = "camelCase"
    )
)]
pub enum SeasonCropCycle {
    /// Development is driven by calendar days.
    CalendarDays {
        /// Calendar-day development milestones and canopy rates.
        calendar_days: DevelopmentCalendar,
    },
    /// Development is driven by growing degree-days.
    GrowingDegreeDays {
        /// Complete calendar-day development milestones and canopy rates.
        calendar_days: DevelopmentCalendar,
        /// Development milestones and canopy rates in growing degree-days.
        gdd: DevelopmentCalendar,
        /// Method used to calculate growing degree-days for this seasonal calendar.
        gdd_method: GrowingDegreeDayMethod,
    },
}

impl SeasonCropCycle {
    /// Returns whether crop development is driven by growing degree-days.
    #[must_use]
    pub const fn uses_growing_degree_days(&self) -> bool {
        matches!(self, Self::GrowingDegreeDays { .. })
    }

    /// Returns the calendar-day representation.
    #[must_use]
    pub const fn calendar_days(&self) -> &DevelopmentCalendar {
        match self {
            Self::CalendarDays { calendar_days }
            | Self::GrowingDegreeDays { calendar_days, .. } => calendar_days,
        }
    }

    /// Returns the calendar that drives crop development.
    #[must_use]
    pub const fn driving_calendar(&self) -> &DevelopmentCalendar {
        match self {
            Self::CalendarDays { calendar_days } => calendar_days,
            Self::GrowingDegreeDays { gdd, .. } => gdd,
        }
    }

    /// Returns the GDD calendar when development is GDD-driven.
    #[must_use]
    pub const fn gdd(&self) -> Option<&DevelopmentCalendar> {
        match self {
            Self::CalendarDays { .. } => None,
            Self::GrowingDegreeDays { gdd, .. } => Some(gdd),
        }
    }

    /// Returns the GDD calculation method when development is GDD-driven.
    #[must_use]
    pub const fn gdd_method(&self) -> Option<GrowingDegreeDayMethod> {
        match self {
            Self::CalendarDays { .. } => None,
            Self::GrowingDegreeDays { gdd_method, .. } => Some(*gdd_method),
        }
    }
}

/// Fully defined crop-cycle calendars used by prepared numerical parameters.
#[derive(Clone, Debug)]
pub enum PreparedCropCycle {
    /// Development is driven by calendar days.
    CalendarDays {
        /// Calendar-day development milestones and canopy rates.
        calendar_days: DevelopmentCalendar,
    },
    /// Development is driven by growing degree-days.
    GrowingDegreeDays {
        /// Complete calendar-day development milestones and canopy rates.
        calendar_days: DevelopmentCalendar,
        /// Development milestones and canopy rates in growing degree-days.
        gdd: DevelopmentCalendar,
        /// Method used to calculate growing degree-days.
        gdd_method: GrowingDegreeDayMethod,
    },
}

impl PreparedCropCycle {
    /// Returns whether crop development is driven by growing degree-days.
    #[must_use]
    pub const fn uses_growing_degree_days(&self) -> bool {
        matches!(self, Self::GrowingDegreeDays { .. })
    }

    /// Returns the calendar-day representation.
    #[must_use]
    pub const fn calendar_days(&self) -> &DevelopmentCalendar {
        match self {
            Self::CalendarDays { calendar_days }
            | Self::GrowingDegreeDays { calendar_days, .. } => calendar_days,
        }
    }

    /// Returns the mutable calendar-day representation.
    #[must_use]
    pub const fn calendar_days_mut(&mut self) -> &mut DevelopmentCalendar {
        match self {
            Self::CalendarDays { calendar_days }
            | Self::GrowingDegreeDays { calendar_days, .. } => calendar_days,
        }
    }

    /// Returns the calendar that drives crop development.
    #[must_use]
    pub const fn driving_calendar(&self) -> &DevelopmentCalendar {
        match self {
            Self::CalendarDays { calendar_days } => calendar_days,
            Self::GrowingDegreeDays { gdd, .. } => gdd,
        }
    }

    /// Returns the mutable calendar that drives crop development.
    #[must_use]
    pub const fn driving_calendar_mut(&mut self) -> &mut DevelopmentCalendar {
        match self {
            Self::CalendarDays { calendar_days } => calendar_days,
            Self::GrowingDegreeDays { gdd, .. } => gdd,
        }
    }

    /// Returns the growing-degree-day representation when it drives development.
    #[must_use]
    pub const fn gdd(&self) -> Option<&DevelopmentCalendar> {
        match self {
            Self::CalendarDays { .. } => None,
            Self::GrowingDegreeDays { gdd, .. } => Some(gdd),
        }
    }

    /// Returns the mutable growing-degree-day representation when it drives development.
    #[must_use]
    pub const fn gdd_mut(&mut self) -> Option<&mut DevelopmentCalendar> {
        match self {
            Self::CalendarDays { .. } => None,
            Self::GrowingDegreeDays { gdd, .. } => Some(gdd),
        }
    }

    /// Returns the GDD calculation method when development is GDD-driven.
    #[must_use]
    pub const fn gdd_method(&self) -> Option<GrowingDegreeDayMethod> {
        match self {
            Self::CalendarDays { .. } => None,
            Self::GrowingDegreeDays { gdd_method, .. } => Some(*gdd_method),
        }
    }
}

/// Crop-development properties independent of the cycle time scale.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CropDevelopment {
    /// Whether determinacy is linked to flowering.
    pub determinacy_linked_to_flowering: bool,
    /// Potential excess fruits in percent, when applicable.
    pub potential_excess_fruits: Option<i16>,
    /// Dry-matter content of fresh yield, when available.
    pub dry_matter: Option<i8>,
}

/// Biomass and harvest-index parameters.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CropProductivity {
    /// Normalized water productivity, in g/m2.
    pub normalized_water_productivity: f64,
    /// Water productivity during yield formation, in percent of normalized WP.
    pub yield_formation_water_productivity: i32,
    /// Biomass sink response to elevated carbon dioxide, in percent.
    pub elevated_co2_adaptation: i8,
    /// Reference harvest index, in percent.
    pub reference_harvest_index: i32,
    /// Potential harvest-index increase before flowering, in percent, when applicable.
    pub pre_flowering_harvest_index_increase: Option<i8>,
    /// Impact coefficient for restricted vegetative growth during yield formation, when enabled.
    pub vegetative_growth_impact_coefficient: Option<f64>,
    /// Impact coefficient for stomatal closure during yield formation, when enabled.
    pub stomatal_closure_impact_coefficient: Option<f64>,
    /// Maximum allowable harvest-index increase, in percent, when applicable.
    pub maximum_harvest_index_increase: Option<i8>,
}

/// Air-temperature criterion used to generate perennial onset or end dates.
#[derive(Clone, Debug, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum AirTemperatureCriterion {
    /// Mean air temperature over a period.
    MeanTemperature,
    /// Accumulated growing degree-days over a period.
    GrowingDegreeDays,
}

/// Rule for the onset of a perennial growing period.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct PerennialOnset {
    /// Criterion used to generate onset, or `None` for a fixed onset.
    pub criterion: Option<AirTemperatureCriterion>,
    /// First day of the onset time window.
    pub first_day: i32,
    /// First month of the onset time window.
    pub first_month: i32,
    /// Length of the search period, in days.
    pub search_length: i32,
    /// Temperature or GDD threshold.
    pub threshold: f64,
    /// Required number of successive days.
    pub successive: i32,
    /// Required occurrences, with at most three supported by the model.
    pub occurrences: i8,
}

/// Rule for the end of a perennial growing period.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct PerennialEnd {
    /// Criterion used to generate the end, or `None` for a fixed end.
    pub criterion: Option<AirTemperatureCriterion>,
    /// Last day of the end time window.
    pub last_day: i32,
    /// Last month of the end time window.
    pub last_month: i32,
    /// Years added to the onset year.
    pub extra_years: i32,
    /// Length of the search period, in days.
    pub search_length: i32,
    /// Temperature or GDD threshold.
    pub threshold: f64,
    /// Required number of successive days.
    pub successive: i32,
    /// Required occurrences, with at most three supported by the model.
    pub occurrences: i8,
}

/// Growing-period rules for forage crops.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct PerennialPeriod {
    /// Growing-period onset rule.
    pub onset: PerennialOnset,
    /// Growing-period end rule.
    pub end: PerennialEnd,
}

/// Crop parameters shared by reusable, seasonal, and prepared crop configurations.
///
/// The generic fields use user-supplied parameter types by default. Crop
/// preparation replaces them with their validated, derived counterparts.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CropParameters<
    FertilityStress = CropFertilityStress,
    Rooting = CropRooting,
    Canopy = CropCanopy,
> {
    /// Crop product category.
    pub kind: CropKind,
    /// Establishment method.
    pub planting_method: PlantingMethod,
    /// Reference-ET adjustment method for depletion thresholds.
    pub depletion_adjustment: DepletionAdjustment,
    /// Temperature parameters.
    pub temperature: CropTemperature,
    /// Water-stress parameters.
    pub water_stress: CropWaterStress,
    /// Soil-fertility stress parameters.
    pub fertility_stress: FertilityStress,
    /// Salinity-stress parameters.
    pub salinity_stress: CropSalinityStress,
    /// Root-system parameters.
    pub rooting: Rooting,
    /// Perennial assimilate-transfer parameters.
    pub assimilate_transfer: AssimilateTransfer,
    /// Evapotranspiration parameters.
    pub evapotranspiration: CropEvapotranspiration,
    /// Canopy-development parameters.
    pub canopy: Canopy,
    /// Development timings.
    pub development: CropDevelopment,
    /// Biomass and yield parameters.
    pub productivity: CropProductivity,
    /// Annual-crop termination day counted from 1 January, when enabled.
    pub premature_end_day: Option<i32>,
    /// Perennial growing-period definition for forage crops.
    pub perennial_period: Option<PerennialPeriod>,
}

/// Reusable crop configuration before adaptation to a particular season.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct Crop {
    /// Crop-development calendars and their driving time basis.
    pub cycle: CropCycle,
    /// Parameters independent of the selected seasonal calendar.
    #[cfg_attr(feature = "serde", serde(flatten))]
    pub parameters: CropParameters,
}

/// Crop configuration with complete calendars selected for one season.
///
/// Unlike [`Crop`], this type contains no unresolved seasonal calendar values
/// and can therefore be supplied directly to a simulation configuration.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct SeasonCrop {
    /// Complete crop-development calendars and their driving time basis.
    pub cycle: SeasonCropCycle,
    /// Parameters independent of the selected seasonal calendar.
    #[cfg_attr(feature = "serde", serde(flatten))]
    pub parameters: CropParameters,
}

/// Prepared crop parameters after validation and invariant derivation.
pub type PreparedCropParameters =
    CropParameters<PreparedCropFertilityStress, PreparedCropRooting, PreparedCropCanopy>;

/// Validated crop parameters with values derived from [`SeasonCrop`].
#[derive(Clone, Debug)]
pub struct PreparedCrop {
    /// Crop-development calendars and their driving time basis.
    pub cycle: PreparedCropCycle,
    /// Validated parameters and invariant derived values.
    pub parameters: PreparedCropParameters,
}

impl Crop {
    /// Completes the crop calendars for a particular season.
    ///
    /// Temperature observations start on the first day of the crop cycle. They are
    /// consulted only when a GDD crop has missing calendar-day values.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when crop values are invalid or the temperature
    /// sequence cannot complete a required GDD stage.
    pub fn for_season<T: TemperatureExtrema + Copy>(
        self,
        season: CropSeason<'_, T>,
    ) -> Result<SeasonCrop, Error> {
        validate_crop_config(&self)?;
        let Self { cycle, parameters } = self;
        let cycle = match cycle {
            CropCycle::CalendarDays { calendar_days } => {
                SeasonCropCycle::CalendarDays { calendar_days }
            }
            CropCycle::GrowingDegreeDays { calendar_days, gdd } => {
                SeasonCropCycle::GrowingDegreeDays {
                    calendar_days,
                    gdd,
                    gdd_method: season.gdd_method,
                }
            }
            CropCycle::UnresolvedGrowingDegreeDays { calendar_days, gdd } => {
                let temperatures = season.require_temperatures()?;
                let calendar_days = complete_gdd_calendar(
                    &parameters.kind,
                    &parameters.temperature,
                    &parameters.canopy,
                    &calendar_days,
                    &gdd,
                    season.gdd_method,
                    temperatures,
                )?;
                SeasonCropCycle::GrowingDegreeDays {
                    calendar_days,
                    gdd,
                    gdd_method: season.gdd_method,
                }
            }
        };

        Ok(SeasonCrop { cycle, parameters })
    }
}

impl SeasonCrop {
    /// Validates the seasonal crop and calculates invariant derived values.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when a numeric parameter is non-finite or violates a
    /// model relationship required during preparation.
    pub fn prepare(self) -> Result<PreparedCrop, Error> {
        validate_season_crop_config(&self)?;
        let Self { cycle, parameters } = self;
        let CropParameters {
            kind,
            planting_method,
            depletion_adjustment,
            temperature,
            water_stress,
            fertility_stress,
            salinity_stress,
            rooting,
            assimilate_transfer,
            evapotranspiration,
            canopy,
            development,
            productivity,
            premature_end_day,
            perennial_period,
        } = parameters;
        let prepared_cycle = match cycle {
            SeasonCropCycle::CalendarDays { calendar_days } => {
                PreparedCropCycle::CalendarDays { calendar_days }
            }
            SeasonCropCycle::GrowingDegreeDays {
                calendar_days,
                gdd,
                gdd_method,
            } => PreparedCropCycle::GrowingDegreeDays {
                calendar_days,
                gdd,
                gdd_method,
            },
        };
        let calibrated = fertility_stress.canopy_growth_shape <= 24.9
            || fertility_stress.maximum_canopy_shape <= 24.9
            || fertility_stress.water_productivity_shape <= 24.9
            || fertility_stress.canopy_decline_shape <= 24.9;
        let (extraction_top, extraction_bottom) = derive_root_extraction(
            rooting.extraction_top_quarter,
            rooting.extraction_bottom_quarter,
        );
        let population_per_square_metre = f64::from(canopy.planting_density) / 10_000.0;
        let initial_seedling_cover =
            population_per_square_metre * (canopy.seedling_size / 10_000.0);
        let initial_regrowth_cover =
            population_per_square_metre * (canopy.regrowth_plant_size / 10_000.0);

        Ok(PreparedCrop {
            cycle: prepared_cycle,
            parameters: CropParameters {
                kind,
                planting_method,
                depletion_adjustment,
                temperature,
                water_stress,
                fertility_stress: PreparedCropFertilityStress {
                    calibration_stress: fertility_stress.calibration_stress,
                    canopy_growth_shape: fertility_stress.canopy_growth_shape,
                    maximum_canopy_shape: fertility_stress.maximum_canopy_shape,
                    water_productivity_shape: fertility_stress.water_productivity_shape,
                    canopy_decline_shape: fertility_stress.canopy_decline_shape,
                    calibrated,
                },
                salinity_stress,
                rooting: PreparedCropRooting {
                    minimum_depth: rooting.minimum_depth,
                    maximum_depth: rooting.maximum_depth,
                    shape: rooting.shape,
                    extraction_top_quarter: rooting.extraction_top_quarter,
                    extraction_bottom_quarter: rooting.extraction_bottom_quarter,
                    extraction_top,
                    extraction_bottom,
                    first_year_minimum_depth: rooting.first_year_minimum_depth,
                    sown_in_first_year: rooting.sown_in_first_year,
                },
                assimilate_transfer,
                evapotranspiration,
                canopy: PreparedCropCanopy {
                    seedling_size: canopy.seedling_size,
                    regrowth_plant_size: canopy.regrowth_plant_size,
                    planting_density: canopy.planting_density,
                    initial_seedling_cover,
                    initial_regrowth_cover,
                    self_thinning_years: canopy.self_thinning_years,
                    self_thinning_shape: canopy.self_thinning_shape,
                    maximum_cover: canopy.maximum_cover,
                },
                development,
                productivity,
                premature_end_day,
                perennial_period,
            },
        })
    }
}

fn complete_gdd_calendar<T: TemperatureExtrema>(
    kind: &CropKind,
    temperature: &CropTemperature,
    canopy: &CropCanopy,
    supplied: &PartialDevelopmentCalendar,
    gdd: &DevelopmentCalendar,
    gdd_method: GrowingDegreeDayMethod,
    temperatures: &[T],
) -> Result<DevelopmentCalendar, Error> {
    let population_per_square_metre = f64::from(canopy.planting_density) / 10_000.0;
    let initial_seedling_cover = population_per_square_metre * (canopy.seedling_size / 10_000.0);
    let full_canopy_gdd = time_to_canopy_cover(
        0.98 * canopy.maximum_cover,
        initial_seedling_cover,
        canopy.maximum_cover,
        gdd.canopy_growth_coefficient,
        gdd.emergence,
    )
    .min(gdd.maturity);

    let emergence = supplied_or_derive(supplied.emergence, || {
        calendar_days_for_gdd(temperature, gdd_method, temperatures, gdd.emergence, 0)
    })?;
    let maximum_rooting = supplied_or_derive(supplied.maximum_rooting, || {
        calendar_days_for_gdd(
            temperature,
            gdd_method,
            temperatures,
            gdd.maximum_rooting,
            0,
        )
    })?;
    let senescence = supplied_or_derive(supplied.senescence, || {
        calendar_days_for_gdd(temperature, gdd_method, temperatures, gdd.senescence, 0)
    })?;
    let maturity = supplied_or_derive(supplied.maturity, || {
        calendar_days_for_gdd(temperature, gdd_method, temperatures, gdd.maturity, 0)
    })?;
    let flowering = if matches!(kind, CropKind::Grain | CropKind::Tuber) {
        supplied_or_derive(supplied.flowering, || {
            calendar_days_for_gdd(temperature, gdd_method, temperatures, gdd.flowering, 0)
        })?
    } else {
        supplied.flowering.unwrap_or(0)
    };
    let flowering_length = if *kind == CropKind::Grain {
        supplied_or_derive(supplied.flowering_length, || {
            let first_index = usize::try_from(flowering).map_err(|_| {
                Error::InvalidCropConfiguration("crop flowering day cannot be negative")
            })?;
            calendar_days_for_gdd(
                temperature,
                gdd_method,
                temperatures,
                gdd.flowering_length,
                first_index,
            )
        })?
    } else {
        0
    };
    let harvest_index = gdd
        .harvest_index
        .map(|target| {
            supplied_or_derive(supplied.harvest_index, || {
                let first_index = if matches!(kind, CropKind::Grain | CropKind::Tuber) {
                    usize::try_from(flowering).map_err(|_| {
                        Error::InvalidCropConfiguration("crop flowering day cannot be negative")
                    })?
                } else {
                    0
                };
                calendar_days_for_gdd(temperature, gdd_method, temperatures, target, first_index)
            })
        })
        .transpose()?
        .or(supplied.harvest_index);
    let canopy_growth_coefficient = supplied_or_derive(supplied.canopy_growth_coefficient, || {
        let days_to_full_canopy =
            calendar_days_for_gdd(temperature, gdd_method, temperatures, full_canopy_gdd, 0)?;
        if days_to_full_canopy <= 0 {
            return Err(Error::InvalidCropConfiguration(
                "GDD crop calendar cannot resolve its daily canopy-growth coefficient",
            ));
        }
        Ok(f64::from(full_canopy_gdd) / f64::from(days_to_full_canopy)
            * gdd.canopy_growth_coefficient)
    })?;
    let canopy_decline_coefficient =
        supplied_or_derive(supplied.canopy_decline_coefficient, || {
            calendar_decline_coefficient(
                temperature,
                gdd_method,
                temperatures,
                canopy.maximum_cover,
                senescence,
                gdd,
            )
        })?;

    Ok(DevelopmentCalendar {
        emergence,
        maximum_rooting,
        senescence,
        maturity,
        flowering,
        flowering_length,
        harvest_index,
        canopy_growth_coefficient,
        canopy_decline_coefficient,
    })
}

fn supplied_or_derive<T>(
    supplied: Option<T>,
    derive: impl FnOnce() -> Result<T, Error>,
) -> Result<T, Error> {
    supplied.map_or_else(derive, Ok)
}

fn calendar_days_for_gdd<T: TemperatureExtrema>(
    temperature: &CropTemperature,
    gdd_method: GrowingDegreeDayMethod,
    season_temperatures: &[T],
    target_gdd: i32,
    first_index: usize,
) -> Result<i32, Error> {
    if target_gdd <= 0 {
        return Ok(0);
    }
    let temperatures =
        season_temperatures
            .get(first_index..)
            .ok_or(Error::InvalidCropConfiguration(
                "season temperatures do not cover a crop-stage start day",
            ))?;
    let mut remaining = f64::from(target_gdd);
    let mut days = 0_i32;
    for daily in temperatures {
        remaining -= growing_degree_days(
            temperature.base,
            temperature.upper,
            daily.minimum(),
            daily.maximum(),
            gdd_method,
        );
        days += 1;
        if remaining <= 0.0 {
            return Ok(days);
        }
    }
    Err(Error::InvalidCropConfiguration(
        "season temperatures do not cover a complete GDD crop stage",
    ))
}

fn calendar_decline_coefficient<T: TemperatureExtrema>(
    temperature: &CropTemperature,
    gdd_method: GrowingDegreeDayMethod,
    temperatures: &[T],
    maximum_cover: f64,
    days_to_senescence: i32,
    gdd: &DevelopmentCalendar,
) -> Result<f64, Error> {
    let gdd_coefficient = gdd.canopy_decline_coefficient;
    let complete_decline_gdd =
        roundc_i32((maximum_cover + 2.29) / (gdd_coefficient * 3.33) * 21.0_f64.ln() + 0.5);
    let decline_gdd = if gdd.senescence + complete_decline_gdd <= gdd.maturity {
        complete_decline_gdd
    } else if gdd.senescence < gdd.maturity {
        gdd.maturity - gdd.senescence
    } else {
        5
    };
    let ending_cover = if gdd.senescence + complete_decline_gdd <= gdd.maturity {
        0.0
    } else {
        maximum_cover
            * (1.0
                - 0.05
                    * ((f64::from(decline_gdd) * gdd_coefficient * 3.33 / (maximum_cover + 2.29))
                        .exp()
                        - 1.0))
    };
    let first_index = usize::try_from(days_to_senescence)
        .map_err(|_| Error::InvalidCropConfiguration("crop senescence day cannot be negative"))?;
    let decline_days = calendar_days_for_gdd(
        temperature,
        gdd_method,
        temperatures,
        decline_gdd,
        first_index,
    )?;
    Ok(((maximum_cover + 2.29) / f64::from(decline_days)
        * (1.0 + (1.0 - ending_cover / maximum_cover) / 0.05).ln())
        / 3.33)
}

fn validate_crop_config(config: &Crop) -> Result<(), Error> {
    let parameters = &config.parameters;
    let finite = [
        parameters.temperature.base,
        parameters.temperature.upper,
        parameters.rooting.minimum_depth,
        parameters.rooting.maximum_depth,
        parameters.rooting.extraction_top_quarter,
        parameters.rooting.extraction_bottom_quarter,
        parameters.rooting.first_year_minimum_depth,
        parameters.canopy.seedling_size,
        parameters.canopy.regrowth_plant_size,
        parameters.canopy.maximum_cover,
    ];
    if finite.iter().any(|value| !value.is_finite())
        || config.cycle.calendar_days().is_some_and(|calendar| {
            !calendar.canopy_growth_coefficient.is_finite()
                || !calendar.canopy_decline_coefficient.is_finite()
        })
        || config
            .cycle
            .partial_calendar_days()
            .is_some_and(|calendar| {
                calendar
                    .canopy_growth_coefficient
                    .is_some_and(|value| !value.is_finite())
                    || calendar
                        .canopy_decline_coefficient
                        .is_some_and(|value| !value.is_finite())
            })
        || config.cycle.gdd().is_some_and(|calendar| {
            !calendar.canopy_growth_coefficient.is_finite()
                || !calendar.canopy_decline_coefficient.is_finite()
        })
    {
        return Err(Error::InvalidCropConfiguration(
            "crop parameters must be finite",
        ));
    }
    if parameters.temperature.base >= parameters.temperature.upper {
        return Err(Error::InvalidCropConfiguration(
            "crop base temperature must be below its upper temperature",
        ));
    }
    if parameters.rooting.minimum_depth <= 0.0
        || parameters.rooting.maximum_depth <= 0.0
        || parameters.rooting.minimum_depth > parameters.rooting.maximum_depth
    {
        return Err(Error::InvalidCropConfiguration(
            "crop rooting depths must be positive and ordered",
        ));
    }
    if parameters.canopy.seedling_size < 0.0
        || parameters.canopy.regrowth_plant_size < 0.0
        || parameters.canopy.planting_density <= 0
        || !(0.0..=1.0).contains(&parameters.canopy.maximum_cover)
    {
        return Err(Error::InvalidCropConfiguration(
            "crop canopy parameters are outside their supported ranges",
        ));
    }
    Ok(())
}

fn validate_season_crop_config(config: &SeasonCrop) -> Result<(), Error> {
    let parameters = &config.parameters;
    let finite = [
        parameters.temperature.base,
        parameters.temperature.upper,
        parameters.rooting.minimum_depth,
        parameters.rooting.maximum_depth,
        parameters.rooting.extraction_top_quarter,
        parameters.rooting.extraction_bottom_quarter,
        parameters.rooting.first_year_minimum_depth,
        parameters.canopy.seedling_size,
        parameters.canopy.regrowth_plant_size,
        parameters.canopy.maximum_cover,
        config.cycle.calendar_days().canopy_growth_coefficient,
        config.cycle.calendar_days().canopy_decline_coefficient,
    ];
    let gdd_is_invalid = match &config.cycle {
        SeasonCropCycle::CalendarDays { .. } => false,
        SeasonCropCycle::GrowingDegreeDays { gdd, .. } => {
            !gdd.canopy_growth_coefficient.is_finite()
                || !gdd.canopy_decline_coefficient.is_finite()
        }
    };
    if finite.iter().any(|value| !value.is_finite()) || gdd_is_invalid {
        return Err(Error::InvalidCropConfiguration(
            "crop parameters must be finite",
        ));
    }
    if parameters.temperature.base >= parameters.temperature.upper {
        return Err(Error::InvalidCropConfiguration(
            "crop base temperature must be below its upper temperature",
        ));
    }
    if parameters.rooting.minimum_depth <= 0.0
        || parameters.rooting.maximum_depth <= 0.0
        || parameters.rooting.minimum_depth > parameters.rooting.maximum_depth
    {
        return Err(Error::InvalidCropConfiguration(
            "crop rooting depths must be positive and ordered",
        ));
    }
    if parameters.canopy.seedling_size < 0.0
        || parameters.canopy.regrowth_plant_size < 0.0
        || parameters.canopy.planting_density <= 0
        || !(0.0..=1.0).contains(&parameters.canopy.maximum_cover)
    {
        return Err(Error::InvalidCropConfiguration(
            "crop canopy parameters are outside their supported ranges",
        ));
    }
    Ok(())
}

fn derive_root_extraction(top_quarter: f64, bottom_quarter: f64) -> (f64, f64) {
    if (top_quarter - bottom_quarter).abs() < 1.0e-12 {
        return (top_quarter, bottom_quarter);
    }
    let (larger, smaller) = if top_quarter < bottom_quarter {
        (bottom_quarter, top_quarter)
    } else {
        (top_quarter, bottom_quarter)
    };
    let x = 3.0 * smaller / (larger - smaller);
    let (derived_larger, derived_smaller) = if x < 0.5 {
        ((4.0 / 3.5) * larger, 0.0)
    } else {
        ((x + 3.5) * larger / (x + 3.0), (x - 0.5) * smaller / x)
    };
    if top_quarter > bottom_quarter {
        (derived_larger, derived_smaller)
    } else {
        (derived_smaller, derived_larger)
    }
}
