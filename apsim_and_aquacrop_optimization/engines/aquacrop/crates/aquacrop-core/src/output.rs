//! Post-processing of completed daily simulation results.

use crate::Error;
use crate::calendar::{CalendarDate, days_in_month};
use crate::compat::{EQUIVALENT_WEIGHT, ZERO_THRESHOLD, roundc_i32};
use crate::crop::{Crop, SeasonCrop};
use crate::engine::{
    CompletedSimulation, DailyCompartmentResult, DailyResult, FinalResult, SimulationCheckpoint,
};
use crate::management::Management;
use crate::soil::{SoilCompartment, SoilProfile};

/// Retained daily and final results from one completed simulation.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct SimulationResult {
    /// Daily results in simulation order.
    pub daily: Vec<DailyResult>,
    /// Final scalar results after the configured period.
    pub final_result: FinalResult,
}

impl SimulationResult {
    /// Combines retained daily results with a completed simulation.
    #[must_use]
    pub fn from_completed(
        daily: Vec<DailyResult>,
        completed: CompletedSimulation,
    ) -> (Self, SimulationCheckpoint) {
        (
            Self {
                daily,
                final_result: completed.result,
            },
            completed.checkpoint,
        )
    }

    /// Derives the complete daily reporting values that accompany [`Self::daily`].
    ///
    /// `dry_matter` is the crop dry-matter content in percent. Passing `None`
    /// leaves fresh yield undefined.
    #[must_use]
    pub fn daily_reports(&self, dry_matter: Option<i8>) -> Vec<DailyReport> {
        daily_reports(&self.daily, dry_matter)
    }

    /// Derives the complete seasonal reporting values for this simulation.
    #[must_use]
    pub fn seasonal_report(&self, crop: &SeasonCrop, management: &Management) -> SeasonalReport {
        SeasonalReport::from_simulation(self, crop, management)
    }

    /// Evaluates this simulation against filesystem-independent field observations.
    ///
    /// `sample_depth` is the observed soil depth in metres.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when a matched day number cannot be converted to a calendar date.
    pub fn evaluate(
        &self,
        sample_depth: f64,
        observations: &[FieldObservation],
    ) -> Result<EvaluationResult, Error> {
        evaluate_simulation(self, sample_depth, observations)
    }
}

/// Reporting period used to aggregate completed daily results.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum OutputAggregation {
    /// Emit one result for every completed day.
    Daily,
    /// Emit results for days 1–10, 11–20, and 21 through month end.
    TenDaily,
    /// Emit one result at the end of each calendar month.
    Monthly,
}

/// Flux totals and end state for one reporting period.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct AggregatedResult {
    /// First calendar date included in the period.
    pub start: CalendarDate,
    /// Last calendar date included in the period.
    pub end: CalendarDate,
    /// Rainfall accumulated over the period, in millimetres.
    pub rain: f64,
    /// Reference evapotranspiration accumulated over the period, in millimetres.
    pub reference_et: f64,
    /// Growing degree-days accumulated over the period, in degree Celsius-days.
    pub growing_degree_days: f64,
    /// Irrigation accumulated over the period, in millimetres.
    pub irrigation: f64,
    /// Surface water infiltrated over the period, in millimetres.
    pub infiltration: f64,
    /// Surface runoff accumulated over the period, in millimetres.
    pub runoff: f64,
    /// Bottom drainage accumulated over the period, in millimetres.
    pub drainage: f64,
    /// Capillary-rise water accumulated over the period, in millimetres.
    pub capillary_rise: f64,
    /// Actual soil evaporation accumulated over the period, in millimetres.
    pub evaporation: f64,
    /// Potential soil evaporation accumulated over the period, in millimetres.
    pub potential_evaporation: f64,
    /// Actual transpiration accumulated over the period, in millimetres.
    pub transpiration: f64,
    /// Crop-only transpiration accumulated over the period, in millimetres.
    pub crop_transpiration: f64,
    /// Potential transpiration accumulated over the period, in millimetres.
    pub potential_transpiration: f64,
    /// Salt entering with infiltrated water over the period, in grams per square metre.
    pub infiltrated_salt: f64,
    /// Salt leaving in bottom drainage over the period, in grams per square metre.
    pub drained_salt: f64,
    /// Salt entering by capillary rise over the period, in grams per square metre.
    pub capillary_salt: f64,
    /// Biomass produced over the period, in tonnes per hectare.
    pub biomass: f64,
    /// Unrestricted biomass produced over the period, in tonnes per hectare.
    pub unrestricted_biomass: f64,
    /// Assimilate biomass mobilized over the period, in tonnes per hectare.
    pub mobilized_assimilates: f64,
    /// Net change in stored assimilate biomass over the period, in tonnes per hectare.
    pub stored_assimilates: f64,
    /// Soil-profile water at period end, in millimetres.
    pub profile_water: f64,
    /// Soil-profile salt at period end, in grams per square metre.
    pub profile_salt: f64,
    /// Green canopy cover at period end as a soil-cover fraction.
    pub canopy_cover: f64,
    /// Rooting depth at period end, in metres.
    pub rooting_depth: f64,
    /// Harvest index at period end, in percent, when yield formation has begun.
    pub harvest_index: Option<f64>,
    /// Cumulative dry yield at period end, in tonnes per hectare.
    pub dry_yield: f64,
}

/// Stateful post-processor reproducing AquaCrop's daily, ten-day, and monthly boundaries.
#[derive(Debug)]
pub struct ResultAggregator {
    aggregation: OutputAggregation,
    pending: Option<AggregatedResult>,
    previous_biomass: f64,
    previous_unrestricted_biomass: f64,
}

impl ResultAggregator {
    /// Creates an empty aggregator for `aggregation`.
    #[must_use]
    pub const fn new(aggregation: OutputAggregation) -> Self {
        Self {
            aggregation,
            pending: None,
            previous_biomass: 0.0,
            previous_unrestricted_biomass: 0.0,
        }
    }

    /// Adds one completed daily result and emits a period at its reference boundary.
    ///
    /// All numeric fields retain the units documented on [`DailyResult`] and
    /// [`AggregatedResult`].
    ///
    /// # Errors
    ///
    /// Returns [`Error`] if the daily result contains an invalid absolute day number.
    pub fn push(&mut self, daily: &DailyResult) -> Result<Option<AggregatedResult>, Error> {
        let date = CalendarDate::from_day_number(daily.day_number)?;
        let daily_biomass = daily.biomass - self.previous_biomass;
        let daily_unrestricted_biomass =
            daily.unrestricted_biomass - self.previous_unrestricted_biomass;
        self.previous_biomass = daily.biomass;
        self.previous_unrestricted_biomass = daily.unrestricted_biomass;

        if let Some(period) = &mut self.pending {
            period.add(daily, date, daily_biomass, daily_unrestricted_biomass);
        } else {
            self.pending = Some(AggregatedResult::from_daily(
                daily,
                date,
                daily_biomass,
                daily_unrestricted_biomass,
            ));
        }

        if self.is_boundary(date)? {
            Ok(self.pending.take())
        } else {
            Ok(None)
        }
    }

    /// Emits a final incomplete ten-day or monthly period, if one remains.
    #[must_use]
    pub fn finish(&mut self) -> Option<AggregatedResult> {
        self.pending.take()
    }

    fn is_boundary(&self, date: CalendarDate) -> Result<bool, Error> {
        Ok(match self.aggregation {
            OutputAggregation::Daily => true,
            OutputAggregation::TenDaily => {
                matches!(date.day(), 10 | 20)
                    || date.day() == days_in_month(date.year(), date.month())?
            }
            OutputAggregation::Monthly => date.day() == days_in_month(date.year(), date.month())?,
        })
    }
}

impl AggregatedResult {
    fn from_daily(
        daily: &DailyResult,
        date: CalendarDate,
        biomass: f64,
        unrestricted_biomass: f64,
    ) -> Self {
        Self {
            start: date,
            end: date,
            rain: daily.rain,
            reference_et: daily.reference_et,
            growing_degree_days: daily.growing_degree_days,
            irrigation: daily.irrigation,
            infiltration: daily.infiltration,
            runoff: daily.runoff,
            drainage: daily.drainage,
            capillary_rise: daily.capillary_rise,
            evaporation: daily.evaporation,
            potential_evaporation: daily.potential_evaporation,
            transpiration: daily.transpiration,
            crop_transpiration: daily.crop_transpiration,
            potential_transpiration: daily.potential_transpiration,
            infiltrated_salt: daily.infiltrated_salt,
            drained_salt: daily.drained_salt,
            capillary_salt: daily.capillary_salt,
            biomass,
            unrestricted_biomass,
            mobilized_assimilates: daily.mobilized_assimilates,
            stored_assimilates: daily.stored_assimilates - daily.mobilized_assimilates,
            profile_water: daily.profile_water,
            profile_salt: daily.profile_salt,
            canopy_cover: daily.canopy_cover,
            rooting_depth: daily.rooting_depth,
            harvest_index: daily.harvest_index,
            dry_yield: daily.dry_yield,
        }
    }

    fn add(
        &mut self,
        daily: &DailyResult,
        date: CalendarDate,
        biomass: f64,
        unrestricted_biomass: f64,
    ) {
        self.end = date;
        self.rain += daily.rain;
        self.reference_et += daily.reference_et;
        self.growing_degree_days += daily.growing_degree_days;
        self.irrigation += daily.irrigation;
        self.infiltration += daily.infiltration;
        self.runoff += daily.runoff;
        self.drainage += daily.drainage;
        self.capillary_rise += daily.capillary_rise;
        self.evaporation += daily.evaporation;
        self.potential_evaporation += daily.potential_evaporation;
        self.transpiration += daily.transpiration;
        self.crop_transpiration += daily.crop_transpiration;
        self.potential_transpiration += daily.potential_transpiration;
        self.infiltrated_salt += daily.infiltrated_salt;
        self.drained_salt += daily.drained_salt;
        self.capillary_salt += daily.capillary_salt;
        self.biomass += biomass;
        self.unrestricted_biomass += unrestricted_biomass;
        self.mobilized_assimilates += daily.mobilized_assimilates;
        self.stored_assimilates += daily.stored_assimilates - daily.mobilized_assimilates;
        self.profile_water = daily.profile_water;
        self.profile_salt = daily.profile_salt;
        self.canopy_cover = daily.canopy_cover;
        self.rooting_depth = daily.rooting_depth;
        self.harvest_index = daily.harvest_index;
        self.dry_yield = daily.dry_yield;
    }
}

/// Prepared soil-profile geometry used to interpret compartment-level results.
#[derive(Debug)]
pub struct SoilProfileGeometry {
    /// Reference soil compartments at field capacity.
    pub compartments: Vec<SoilCompartment>,
    /// Total depth represented by `compartments`, in metres.
    pub profile_depth: f64,
    /// Crop maximum rooting depth, in metres.
    pub maximum_rooting_depth: f64,
    /// Depth of each compartment centre below the surface, in metres.
    pub compartment_nodes: Vec<f64>,
}

/// Prepares the soil geometry needed to interpret compartment-level daily results.
///
/// # Errors
///
/// Returns [`Error`] when the soil profile cannot be prepared.
pub fn soil_profile_geometry(crop: &Crop, soil: SoilProfile) -> Result<SoilProfileGeometry, Error> {
    let soil = soil.prepare()?;
    let compartments = soil
        .reference_compartments_at_field_capacity_for_depth(crop.parameters.rooting.maximum_depth);
    let profile_depth = compartments
        .iter()
        .map(|compartment| compartment.thickness)
        .sum();
    let mut compartment_nodes = Vec::with_capacity(compartments.len());
    let mut depth = 0.0;
    for compartment in &compartments {
        compartment_nodes.push(depth + compartment.thickness / 2.0);
        depth += compartment.thickness;
    }
    Ok(SoilProfileGeometry {
        compartments,
        profile_depth,
        maximum_rooting_depth: crop.parameters.rooting.maximum_depth,
        compartment_nodes,
    })
}

/// Values derived from one [`DailyResult`] for complete daily reporting.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct DailyReport {
    /// Absolute AquaCrop day number shared with the source [`DailyResult`].
    pub day_number: i32,
    /// Actual-to-potential evaporation ratio, in percent.
    pub evaporation_ratio: i32,
    /// Actual-to-potential transpiration ratio, in percent.
    pub transpiration_ratio: i32,
    /// Potential evaporation plus potential transpiration, in millimetres.
    pub potential_evapotranspiration: f64,
    /// Actual evaporation plus actual transpiration, in millimetres.
    pub evapotranspiration: f64,
    /// Actual-to-potential evapotranspiration ratio, in percent.
    pub evapotranspiration_ratio: i32,
    /// Leaf-expansion stress, in percent, when defined.
    pub leaf_stress: Option<i32>,
    /// Stomatal stress, in percent, when defined.
    pub stomatal_stress: Option<i32>,
    /// Early-senescence stress, in percent, when defined.
    pub senescence_stress: Option<i32>,
    /// Root-zone salinity stress, in percent, when defined.
    pub salinity_stress: Option<i32>,
    /// Weed stress, in percent, when crop canopy exists.
    pub weed_stress: Option<i32>,
    /// Temperature stress, in percent.
    pub temperature_stress: i32,
    /// Transpiration crop coefficient, when defined.
    pub crop_coefficient: Option<f64>,
    /// Green canopy attributable to the crop, in percent.
    pub crop_canopy_percent: f64,
    /// Total green canopy cover, in percent.
    pub canopy_percent: f64,
    /// Fresh yield, in tonnes per hectare, when crop dry matter is known.
    pub fresh_yield: Option<f64>,
    /// Actual biomass relative to unrestricted biomass, in percent.
    pub relative_biomass: Option<i32>,
    /// Yield water productivity, in kilograms per cubic metre.
    pub yield_water_productivity: f64,
    /// Salt in the active root-zone water, in tonnes per hectare.
    pub root_salt: Option<f64>,
    /// Salt entering through infiltration, in tonnes per hectare.
    pub infiltrated_salt: f64,
    /// Salt leaving through drainage, in tonnes per hectare.
    pub drained_salt: f64,
    /// Salt entering through capillary rise, in tonnes per hectare.
    pub capillary_salt: f64,
    /// Salt stored in the profile, in tonnes per hectare.
    pub profile_salt: f64,
    /// Root-zone salinity stress, in percent, with zero outside the root zone.
    pub root_salinity_stress: i32,
    /// Volumetric water content of each compartment, in percent.
    pub compartment_water_percent: Vec<f64>,
    /// Mean of minimum and maximum daily air temperature, in degrees Celsius.
    pub average_temperature: f64,
}

/// Derives complete reporting values for each daily result.
///
/// `dry_matter` is the crop dry-matter content in percent. Passing `None`
/// leaves fresh yield undefined.
#[must_use]
pub fn daily_reports(daily_results: &[DailyResult], dry_matter: Option<i8>) -> Vec<DailyReport> {
    let mut cumulative_transpiration = 0.0;
    daily_results
        .iter()
        .map(|daily| {
            cumulative_transpiration += daily.transpiration;
            DailyReport::new(daily, dry_matter, cumulative_transpiration)
        })
        .collect()
}

impl DailyReport {
    fn new(daily: &DailyResult, dry_matter: Option<i8>, cumulative_transpiration: f64) -> Self {
        let evaporation_ratio = ratio_or_hundred(daily.evaporation, daily.potential_evaporation);
        let transpiration_ratio =
            ratio_or_hundred(daily.transpiration, daily.potential_transpiration);
        let potential_evapotranspiration =
            daily.potential_evaporation + daily.potential_transpiration;
        let evapotranspiration = daily.evaporation + daily.transpiration;
        let evapotranspiration_ratio =
            ratio_or_hundred(evapotranspiration, potential_evapotranspiration);
        let salinity_stress = daily
            .root_zone_salinity
            .as_ref()
            .map(|value| roundc_i32(100.0 * (1.0 - value.stress_coefficient)));
        let weed_stress =
            (daily.crop_canopy_cover > ZERO_THRESHOLD).then(|| roundc_i32(daily.weed_cover));
        let temperature_stress = roundc_i32(100.0 * (1.0 - daily.temperature_stress_coefficient));
        let crop_coefficient = (daily.reference_et > 0.0
            && daily.potential_transpiration > 0.0
            && temperature_stress < 100)
            .then(|| {
                daily.potential_transpiration
                    / (daily.reference_et * daily.temperature_stress_coefficient)
            });
        let relative_biomass = (daily.biomass > 0.0 && daily.unrestricted_biomass > 0.0)
            .then(|| roundc_i32(100.0 * daily.biomass / daily.unrestricted_biomass).min(100));
        let yield_water_productivity = if daily.dry_yield > 0.0
            && (cumulative_transpiration > 0.0 || daily.cumulative_crop_evaporation > 0.0)
        {
            daily.dry_yield * 1000.0
                / ((cumulative_transpiration + daily.cumulative_crop_evaporation) * 10.0)
        } else {
            0.0
        };
        let root_salt = daily
            .root_zone_water
            .as_ref()
            .zip(daily.root_zone_salinity.as_ref())
            .map(|(water, salinity)| {
                water.saturation * salinity.extract_conductivity * EQUIVALENT_WEIGHT / 100.0
            });
        Self {
            day_number: daily.day_number,
            evaporation_ratio,
            transpiration_ratio,
            potential_evapotranspiration,
            evapotranspiration,
            evapotranspiration_ratio,
            leaf_stress: daily.leaf_expansion_stress.map(roundc_i32),
            stomatal_stress: daily.stomatal_stress.map(roundc_i32),
            senescence_stress: daily.senescence_stress.map(roundc_i32),
            salinity_stress,
            weed_stress,
            temperature_stress,
            crop_coefficient,
            crop_canopy_percent: daily.crop_canopy_cover * 100.0,
            canopy_percent: daily.canopy_cover * 100.0,
            fresh_yield: dry_matter.map(|value| fresh_yield(daily.dry_yield, value)),
            relative_biomass,
            yield_water_productivity,
            root_salt,
            infiltrated_salt: daily.infiltrated_salt / 100.0,
            drained_salt: daily.drained_salt / 100.0,
            capillary_salt: daily.capillary_salt / 100.0,
            profile_salt: daily.profile_salt / 100.0,
            root_salinity_stress: daily.root_zone_salinity.as_ref().map_or(0, |value| {
                roundc_i32(100.0 * (1.0 - value.stress_coefficient))
            }),
            compartment_water_percent: daily
                .compartments
                .iter()
                .map(|compartment| compartment.theta * 100.0)
                .collect(),
            average_temperature: f64::midpoint(
                daily.minimum_temperature,
                daily.maximum_temperature,
            ),
        }
    }
}

fn ratio_or_hundred(actual: f64, potential: f64) -> i32 {
    if potential > 0.0 {
        roundc_i32(100.0 * actual / potential)
    } else {
        100
    }
}

/// Complete values derived for one simulated season.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct SeasonalReport {
    /// Total rainfall, in millimetres.
    pub rain: f64,
    /// Total reference evapotranspiration, in millimetres.
    pub reference_et: f64,
    /// Total growing degree-days, in degree Celsius days.
    pub growing_degree_days: f64,
    /// Atmospheric carbon-dioxide concentration, in parts per million.
    pub carbon_dioxide: f64,
    /// Total irrigation, in millimetres.
    pub irrigation: f64,
    /// Total infiltration, in millimetres.
    pub infiltration: f64,
    /// Total runoff, in millimetres.
    pub runoff: f64,
    /// Total bottom drainage, in millimetres.
    pub drainage: f64,
    /// Total capillary rise, in millimetres.
    pub capillary_rise: f64,
    /// Total actual evaporation, in millimetres.
    pub evaporation: f64,
    /// Actual-to-potential evaporation ratio, in percent.
    pub evaporation_ratio: Option<i32>,
    /// Total actual transpiration, in millimetres.
    pub transpiration: f64,
    /// Total transpiration attributable to the crop, in millimetres.
    pub crop_transpiration: f64,
    /// Actual-to-potential transpiration ratio, in percent.
    pub transpiration_ratio: Option<i32>,
    /// Salt entering through infiltration, in tonnes per hectare.
    pub infiltrated_salt: f64,
    /// Salt leaving through drainage, in tonnes per hectare.
    pub drained_salt: f64,
    /// Salt entering through capillary rise, in tonnes per hectare.
    pub capillary_salt: f64,
    /// Salt stored in the profile at season end, in tonnes per hectare.
    pub profile_salt: f64,
    /// Days included in seasonal stress averaging.
    pub stress_days: i32,
    /// Mean salinity stress over active-canopy days, in percent.
    pub salinity_stress: i32,
    /// Configured soil-fertility stress, in percent.
    pub fertility_stress: i32,
    /// Mean weed stress over active-canopy days, in percent.
    pub weed_stress: i32,
    /// Mean temperature stress over active-canopy days, in percent.
    pub temperature_stress: i32,
    /// Mean leaf-expansion stress over active-canopy days, in percent.
    pub leaf_expansion_stress: i32,
    /// Mean stomatal stress over active-canopy days, in percent.
    pub stomatal_stress: i32,
    /// Final dry biomass, in tonnes per hectare.
    pub biomass: f64,
    /// Actual biomass relative to unrestricted biomass, in percent.
    pub relative_biomass: Option<i32>,
    /// Final dry-yield-to-biomass ratio, in percent.
    pub harvest_index: Option<f64>,
    /// Final dry yield, in tonnes per hectare.
    pub dry_yield: f64,
    /// Final fresh yield, in tonnes per hectare, when dry matter is known.
    pub fresh_yield: Option<f64>,
    /// Yield water productivity, in kilograms per cubic metre.
    pub water_productivity: f64,
    /// Total assimilates mobilized into above-ground biomass, in tonnes per hectare.
    pub mobilized_assimilates: f64,
    /// Assimilates retained for a linked season, in tonnes per hectare.
    pub stored_assimilates: f64,
}

impl SeasonalReport {
    /// Derives the complete seasonal result from a completed simulation and its inputs.
    #[must_use]
    pub fn from_simulation(
        result: &SimulationResult,
        crop: &SeasonCrop,
        management: &Management,
    ) -> Self {
        Self::from_simulation_inputs(result, crop.parameters.development.dry_matter, management)
    }

    pub(crate) fn from_simulation_inputs(
        result: &SimulationResult,
        dry_matter: Option<i8>,
        management: &Management,
    ) -> Self {
        let sum = SeasonalSums::from_daily(&result.daily);
        let biomass = result.final_result.biomass;
        let unrestricted_biomass = result.final_result.unrestricted_biomass;
        let dry_yield = result.final_result.dry_yield;
        let relative_biomass = (biomass > 0.0 && unrestricted_biomass > 0.0)
            .then(|| roundc_i32(100.0 * biomass / unrestricted_biomass).min(100));
        let harvest_index = (biomass > f64::EPSILON).then(|| 100.0 * dry_yield / biomass);
        let water_productivity =
            if dry_yield > 0.0 && (sum.transpiration > 0.0 || sum.crop_evaporation > 0.0) {
                dry_yield * 1000.0 / ((sum.transpiration + sum.crop_evaporation) * 10.0)
            } else {
                0.0
            };
        Self {
            rain: sum.rain,
            reference_et: sum.reference_et,
            growing_degree_days: sum.growing_degree_days,
            carbon_dioxide: sum.carbon_dioxide,
            irrigation: sum.irrigation,
            infiltration: sum.infiltration,
            runoff: sum.runoff,
            drainage: sum.drainage,
            capillary_rise: sum.capillary_rise,
            evaporation: sum.evaporation,
            evaporation_ratio: ratio(sum.evaporation, sum.potential_evaporation),
            transpiration: sum.transpiration,
            crop_transpiration: sum.crop_transpiration,
            transpiration_ratio: ratio(sum.transpiration, sum.potential_transpiration),
            infiltrated_salt: sum.infiltrated_salt / 100.0,
            drained_salt: sum.drained_salt / 100.0,
            capillary_salt: sum.capillary_salt / 100.0,
            profile_salt: sum.profile_salt / 100.0,
            stress_days: sum.stress_days,
            salinity_stress: roundc_i32(sum.salinity_stress),
            fertility_stress: management.fertility_stress,
            weed_stress: roundc_i32(sum.weed_stress),
            temperature_stress: roundc_i32(sum.temperature_stress),
            leaf_expansion_stress: roundc_i32(sum.leaf_expansion_stress),
            stomatal_stress: roundc_i32(sum.stomatal_stress),
            biomass,
            relative_biomass,
            harvest_index,
            dry_yield,
            fresh_yield: dry_matter.map(|value| fresh_yield(dry_yield, value)),
            water_productivity,
            mobilized_assimilates: sum.mobilized_assimilates,
            stored_assimilates: result.final_result.stored_assimilates,
        }
    }
}

struct SeasonalSums {
    rain: f64,
    reference_et: f64,
    growing_degree_days: f64,
    carbon_dioxide: f64,
    irrigation: f64,
    infiltration: f64,
    runoff: f64,
    drainage: f64,
    capillary_rise: f64,
    evaporation: f64,
    potential_evaporation: f64,
    transpiration: f64,
    crop_transpiration: f64,
    potential_transpiration: f64,
    infiltrated_salt: f64,
    drained_salt: f64,
    capillary_salt: f64,
    profile_salt: f64,
    crop_evaporation: f64,
    mobilized_assimilates: f64,
    stress_days: i32,
    salinity_stress: f64,
    weed_stress: f64,
    temperature_stress: f64,
    leaf_expansion_stress: f64,
    stomatal_stress: f64,
}

impl SeasonalSums {
    fn from_daily(daily: &[DailyResult]) -> Self {
        let mut sum = Self {
            rain: 0.0,
            reference_et: 0.0,
            growing_degree_days: 0.0,
            carbon_dioxide: daily.first().map_or(0.0, |value| value.carbon_dioxide),
            irrigation: 0.0,
            infiltration: 0.0,
            runoff: 0.0,
            drainage: 0.0,
            capillary_rise: 0.0,
            evaporation: 0.0,
            potential_evaporation: 0.0,
            transpiration: 0.0,
            crop_transpiration: 0.0,
            potential_transpiration: 0.0,
            infiltrated_salt: 0.0,
            drained_salt: 0.0,
            capillary_salt: 0.0,
            profile_salt: daily.last().map_or(0.0, |value| value.profile_salt),
            crop_evaporation: daily
                .last()
                .map_or(0.0, |value| value.cumulative_crop_evaporation),
            mobilized_assimilates: 0.0,
            stress_days: 0,
            salinity_stress: 0.0,
            weed_stress: 0.0,
            temperature_stress: 0.0,
            leaf_expansion_stress: 0.0,
            stomatal_stress: 0.0,
        };
        for value in daily {
            sum.rain += value.rain;
            sum.reference_et += value.reference_et;
            sum.growing_degree_days += value.growing_degree_days;
            sum.irrigation += value.irrigation;
            sum.infiltration += value.infiltration;
            sum.runoff += value.runoff;
            sum.drainage += value.drainage;
            sum.capillary_rise += value.capillary_rise;
            sum.evaporation += value.evaporation;
            sum.potential_evaporation += value.potential_evaporation;
            sum.transpiration += value.transpiration;
            sum.crop_transpiration += value.crop_transpiration;
            sum.potential_transpiration += value.potential_transpiration;
            sum.infiltrated_salt += value.infiltrated_salt;
            sum.drained_salt += value.drained_salt;
            sum.capillary_salt += value.capillary_salt;
            sum.mobilized_assimilates += value.mobilized_assimilates;
            if value.canopy_cover > 0.0 {
                sum.stress_days += 1;
                let count = f64::from(sum.stress_days);
                let salinity = value
                    .root_zone_salinity
                    .as_ref()
                    .map_or(0.0, |stress| 100.0 * (1.0 - stress.stress_coefficient));
                sum.salinity_stress += (salinity - sum.salinity_stress) / count;
                sum.weed_stress += (value.weed_cover - sum.weed_stress) / count;
                sum.temperature_stress += (100.0 * (1.0 - value.temperature_stress_coefficient)
                    - sum.temperature_stress)
                    / count;
                if let Some(stress) = value.leaf_expansion_stress {
                    sum.leaf_expansion_stress += (stress - sum.leaf_expansion_stress) / count;
                }
                if let Some(stress) = value.stomatal_stress {
                    sum.stomatal_stress += (stress - sum.stomatal_stress) / count;
                }
            }
        }
        sum
    }
}

fn ratio(actual: f64, potential: f64) -> Option<i32> {
    (potential > 0.0).then(|| roundc_i32(100.0 * actual / potential))
}

/// One set of field observations used to evaluate a simulated day.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct FieldObservation {
    /// Absolute AquaCrop day number of the observation.
    pub day_number: i32,
    /// Observed canopy cover and deviation, in percent.
    pub canopy: Option<ObservedValue>,
    /// Observed biomass and deviation, in tonnes per hectare.
    pub biomass: Option<ObservedValue>,
    /// Observed soil-water storage and deviation, in millimetres.
    pub soil_water: Option<ObservedValue>,
}

/// One observed value and its standard deviation.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct ObservedValue {
    /// Observed value in the quantity's documented unit.
    pub value: f64,
    /// Standard deviation in the same unit as `value`.
    pub deviation: f64,
}

/// Matched observed and simulated values for one date.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct EvaluationPair {
    /// Observed value in the evaluated quantity's documented unit.
    pub observed: f64,
    /// Standard deviation of the observed value.
    pub deviation: f64,
    /// Simulated value in the same unit as `observed`.
    pub simulated: f64,
    /// Calendar date shared by the observed and simulated values.
    pub date: CalendarDate,
}

/// Statistical comparison of observed and simulated values.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct Statistics {
    /// Arithmetic mean of observed values.
    pub observed_average: f64,
    /// Arithmetic mean of simulated values.
    pub simulated_average: f64,
    /// Pearson correlation coefficient.
    pub correlation: f64,
    /// Root mean square error in the evaluated quantity's unit.
    pub root_mean_square_error: f64,
    /// Root mean square error normalized by the observed mean, in percent.
    pub normalized_root_mean_square_error: f64,
    /// Nash-Sutcliffe model efficiency coefficient.
    pub efficiency: f64,
    /// Willmott index of agreement.
    pub agreement: f64,
}

/// Matched field observations for the quantities evaluated by AquaCrop.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct EvaluationResult {
    /// Canopy-cover pairs, in percent.
    pub canopy: Vec<EvaluationPair>,
    /// Biomass pairs, in tonnes per hectare.
    pub biomass: Vec<EvaluationPair>,
    /// Soil-water-storage pairs, in millimetres.
    pub soil_water: Vec<EvaluationPair>,
}

/// Matches field observations to the corresponding completed daily results.
///
/// `sample_depth` is the observed soil depth in metres.
///
/// # Errors
///
/// Returns [`Error`] when a matched day number cannot be converted to a calendar date.
pub fn evaluate_simulation(
    result: &SimulationResult,
    sample_depth: f64,
    observations: &[FieldObservation],
) -> Result<EvaluationResult, Error> {
    let mut canopy = Vec::new();
    let mut biomass = Vec::new();
    let mut soil_water = Vec::new();
    for observation in observations {
        let Some(daily) = result
            .daily
            .iter()
            .find(|daily| daily.day_number == observation.day_number)
        else {
            continue;
        };
        let date = CalendarDate::from_day_number(daily.day_number)?;
        if let Some(observed) = &observation.canopy {
            canopy.push(EvaluationPair {
                observed: observed.value,
                deviation: observed.deviation,
                simulated: quantized(100.0 * daily.crop_canopy_cover, 1)?,
                date,
            });
        }
        if let Some(observed) = &observation.biomass {
            biomass.push(EvaluationPair {
                observed: observed.value,
                deviation: observed.deviation,
                simulated: quantized(daily.biomass, 3)?,
                date,
            });
        }
        if let Some(observed) = &observation.soil_water {
            soil_water.push(EvaluationPair {
                observed: observed.value,
                deviation: observed.deviation,
                simulated: quantized(water_to_depth(&daily.compartments, sample_depth), 1)?,
                date,
            });
        }
    }
    Ok(EvaluationResult {
        canopy,
        biomass,
        soil_water,
    })
}

/// Calculates AquaCrop evaluation statistics for matched observations.
#[must_use]
pub fn statistics(pairs: &[EvaluationPair]) -> Statistics {
    let count = u32::try_from(pairs.len()).map_or(0.0, f64::from);
    let observed_average = pairs.iter().map(|pair| pair.observed).sum::<f64>() / count;
    let simulated_average = pairs.iter().map(|pair| pair.simulated).sum::<f64>() / count;
    let covariance = pairs
        .iter()
        .map(|pair| (pair.observed - observed_average) * (pair.simulated - simulated_average))
        .sum::<f64>();
    let observed_variance = pairs
        .iter()
        .map(|pair| (pair.observed - observed_average).powi(2))
        .sum::<f64>();
    let simulated_variance = pairs
        .iter()
        .map(|pair| (pair.simulated - simulated_average).powi(2))
        .sum::<f64>();
    let squared_error = pairs
        .iter()
        .map(|pair| (pair.observed - pair.simulated).powi(2))
        .sum::<f64>();
    let agreement_denominator = pairs
        .iter()
        .map(|pair| {
            ((pair.simulated - observed_average).abs() + (pair.observed - observed_average).abs())
                .powi(2)
        })
        .sum::<f64>();
    let root_mean_square_error = (squared_error / count).sqrt();
    Statistics {
        observed_average,
        simulated_average,
        correlation: covariance / (observed_variance * simulated_variance).sqrt(),
        root_mean_square_error,
        normalized_root_mean_square_error: 100.0 * root_mean_square_error / observed_average,
        efficiency: 1.0 - squared_error / observed_variance,
        agreement: 1.0 - squared_error / agreement_denominator,
    }
}

fn water_to_depth(compartments: &[DailyCompartmentResult], depth: f64) -> f64 {
    let mut remaining = depth;
    compartments
        .iter()
        .map(|compartment| {
            let included = remaining.clamp(0.0, compartment.thickness);
            remaining -= included;
            1000.0 * compartment.theta * included
        })
        .sum()
}

fn quantized(value: f64, precision: u32) -> Result<f64, Error> {
    let rounded = rounded_output_value(value, precision, f64::round_ties_even);
    format!("{rounded:.precision$}", precision = precision as usize)
        .parse()
        .map_err(|_| Error::InvalidSimulationConfiguration("formatted evaluation value is invalid"))
}

pub(crate) fn rounded_output_value(value: f64, precision: u32, round: fn(f64) -> f64) -> f64 {
    let factor = 10.0_f64.powf(f64::from(precision));
    let scaled = value * factor;
    let lower = scaled.floor();
    let snapped = if (scaled - lower - 0.5).abs() <= 0.001 {
        lower + 0.5
    } else {
        scaled
    };
    round(snapped) / factor
}

/// Converts dry yield to fresh yield using dry-matter content in percent.
#[must_use]
pub fn fresh_yield(dry_yield: f64, dry_matter: i8) -> f64 {
    dry_yield / (f64::from(dry_matter) / 100.0)
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64;

    use super::*;

    #[test]
    fn ten_daily_periods_end_on_tenth_twentieth_and_month_end() -> Result<(), Error> {
        let mut aggregator = ResultAggregator::new(OutputAggregation::TenDaily);
        let mut emitted = Vec::new();
        let first = CalendarDate::new(2014, 1, 8)?.day_number();
        for offset in 0..24 {
            let daily = daily_result(first + offset, f64::from(offset + 1));
            if let Some(period) = aggregator.push(&daily)? {
                emitted.push(period);
            }
        }

        assert_eq!(emitted.len(), 3);
        assert_eq!(emitted[0].start.day(), 8);
        assert_eq!(emitted[0].end.day(), 10);
        assert_eq_f64!(emitted[0].rain, 3.0_f64);
        assert_eq!(emitted[1].start.day(), 11);
        assert_eq!(emitted[1].end.day(), 20);
        assert_eq_f64!(emitted[1].rain, 10.0_f64);
        assert_eq!(emitted[2].start.day(), 21);
        assert_eq!(emitted[2].end.day(), 31);
        assert_eq_f64!(emitted[2].rain, 11.0_f64);
        assert!(aggregator.finish().is_none());
        Ok(())
    }

    #[test]
    fn monthly_period_uses_aquacrop_leap_year_month_end() -> Result<(), Error> {
        let mut aggregator = ResultAggregator::new(OutputAggregation::Monthly);
        let first = CalendarDate::new(2100, 2, 27)?.day_number();
        assert!(aggregator.push(&daily_result(first, 1.0))?.is_none());
        assert!(aggregator.push(&daily_result(first + 1, 2.0))?.is_none());
        let period = aggregator
            .push(&daily_result(first + 2, 3.0))?
            .expect("29 February closes February in the reference calendar");

        assert_eq!(period.end.day(), 29);
        assert_eq_f64!(period.rain, 3.0_f64);
        assert_eq_f64!(period.biomass, 3.0_f64);
        Ok(())
    }

    #[test]
    fn finish_emits_incomplete_month_without_changing_its_end_state() -> Result<(), Error> {
        let mut aggregator = ResultAggregator::new(OutputAggregation::Monthly);
        let date = CalendarDate::new(2014, 5, 21)?;
        let daily = daily_result(date.day_number(), 4.0);
        assert!(aggregator.push(&daily)?.is_none());
        let period = aggregator
            .finish()
            .expect("a completed daily result leaves a partial reporting period");

        assert_eq!(period.start.day(), 21);
        assert_eq!(period.end.day(), 21);
        assert_eq_f64!(period.profile_water, 4.0_f64);
        assert_eq_f64!(period.stored_assimilates, (-4.0_f64));
        Ok(())
    }

    #[test]
    fn fresh_yield_converts_dry_matter_fraction() {
        assert_eq_f64!(fresh_yield(2.0, 40), 5.0_f64);
    }

    fn daily_result(day_number: i32, value: f64) -> DailyResult {
        DailyResult {
            day_number,
            day_after_planting: Some(1),
            germinated: true,
            stage: crate::engine::CropStage::Vegetative,
            delayed_days: 0,
            growing_degree_days: value,
            reference_et: value,
            cumulative_growing_degree_days: value,
            rooting_depth: value,
            canopy_cover: value,
            crop_canopy_cover: value,
            weed_cover: 0.0,
            profile_water: value,
            surface_water: value,
            rain: 1.0,
            irrigation: value,
            infiltration: value,
            runoff: value,
            drainage: value,
            capillary_rise: value,
            horizontal_water: value,
            potential_evaporation: value,
            evaporation: value,
            potential_transpiration: value,
            transpiration: value,
            stomatal_stress: Some(0.0),
            crop_transpiration: value,
            cumulative_crop_evaporation: value,
            infiltrated_salt: value,
            drained_salt: value,
            capillary_salt: value,
            profile_salt: value,
            root_zone_water: None,
            maximum_root_zone_water: None,
            compartments: Vec::new(),
            root_zone_salinity: None,
            anaerobic_stress_coefficient: value,
            root_zone_anaerobic_days: 0,
            temperature_stress_coefficient: value,
            leaf_expansion_stress: Some(value),
            senescence_stress: Some(value),
            water_productivity: value,
            biomass: value,
            unrestricted_biomass: value,
            harvest_index: Some(value),
            dry_yield: value,
            mobilized_assimilates: value,
            stored_assimilates: 0.0,
            cutting: None,
            water_balance_error: value,
            salt_balance_error: value,
            minimum_temperature: value,
            maximum_temperature: value,
            carbon_dioxide: value,
            groundwater: None,
        }
    }
}
