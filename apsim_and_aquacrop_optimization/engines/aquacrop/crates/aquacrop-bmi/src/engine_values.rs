// `get_value`/`set_value` and related variable dispatch for `BmiModel`.
//
// Included (not a submodule) from `engine.rs` purely to keep the lifecycle/override bookkeeping
// and the per-variable dispatch table in separate, independently readable files while sharing
// private access to `RunState`/`Phase`.

impl BmiModel {
    /// Units string for `name`, exactly as declared by the reference (including known reference
    /// units-string defects).
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::UnknownVariable`] for an unresolvable name.
    pub fn var_units(name: &str) -> Result<&'static str, BmiError> {
        Ok(Self::variable(name)?.units)
    }

    /// Grid id for `name` (0 = scalar, 1 = per-soil-layer vector, 2 = per-compartment vector).
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::UnknownVariable`] for an unresolvable name.
    pub fn var_grid(name: &str) -> Result<u8, BmiError> {
        Ok(Self::variable(name)?.grid)
    }

    /// Data type returned by the BMI Python interface.
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::UnknownVariable`] for an unresolvable name.
    pub fn var_type(name: &str) -> Result<&'static str, BmiError> {
        Self::variable(name)?;
        Ok("float64")
    }

    /// Item size in bytes, always `8` (one `f64`), matching the reference.
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::UnknownVariable`] for an unresolvable name.
    pub fn var_itemsize(name: &str) -> Result<usize, BmiError> {
        Self::variable(name)?;
        Ok(8)
    }

    /// Total size in bytes: [`Self::grid_size`] times 8.
    ///
    /// # Errors
    ///
    /// See [`Self::grid_size`].
    pub fn var_nbytes(&self, name: &str) -> Result<usize, BmiError> {
        Ok(self.grid_size(name)? * 8)
    }

    /// Grid location, always `"node"`, matching the reference.
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::UnknownVariable`] for an unresolvable name.
    pub fn var_location(name: &str) -> Result<&'static str, BmiError> {
        Self::variable(name)?;
        Ok("node")
    }

    /// Thickness-weighted average volumetric water content for soil layer `layer_index`
    /// (zero-based), matching the reference's `GetSoilLayerTheta` (`global.f90:16252-16287`):
    /// compartments are grouped by [`aquacrop_core::soil::SoilCompartment::layer_index`] and
    /// averaged weighted by thickness; a layer with no matching compartments (for example because
    /// the crop's rooting depth does not reach it) reads `0.0`, matching the reference's own
    /// no-overlap fallback.
    ///
    /// Reads through [`aquacrop_core::engine::Engine::compartments`] (live state), not the cached
    /// `last_result` snapshot. A `set_value` write to the live compartments must
    /// be visible to an immediately-following `get_value`, without requiring an intervening
    /// `update()`, matching the Fortran reference's own immediate-visibility behavior.
    fn layer_theta(state: &RunState, layer_index: usize) -> f64 {
        let (weighted, total_thickness) = state
            .simulation
            .compartments()
            .iter()
            .filter(|compartment| compartment.layer_index == layer_index)
            .fold((0.0, 0.0), |(weighted, total), compartment| {
                (
                    weighted + compartment.theta * compartment.thickness,
                    total + compartment.thickness,
                )
            });
        if total_thickness > 0.0 {
            weighted / total_thickness
        } else {
            0.0
        }
    }

    /// Scalar per-layer output for `soil_water_actual_layer-N` (one-based `layer_number`),
    /// returning the reference's exact `-999.0` sentinel when the layer does not exist in the
    /// configured soil profile (`bmi_aquacrop.f90:810-869`).
    fn layer_theta_or_sentinel(state: &RunState, layer_number: usize) -> f64 {
        let layer_index = layer_number - 1;
        if layer_index >= state.snapshot.soil_layer_thicknesses.len() {
            return -999.0;
        }
        Self::layer_theta(state, layer_index)
    }

    fn layer_number_suffix(canonical: &str) -> Option<usize> {
        canonical
            .strip_prefix("soil_water_actual_layer-")
            .and_then(|suffix| suffix.parse().ok())
    }

    /// Reads `name`'s current value(s) into `dest`, which must have exactly [`Self::grid_size`]
    /// elements (`1` for every scalar variable).
    ///
    /// Before the first [`Self::update`] call, *flux and cumulative* outputs that come from a
    /// completed [`DailyResult`] (infiltration, drainage, biomass, and similar) read `0.0` (there is
    /// no completed day yet); this is a deliberate, documented wrapper convention, not a reference
    /// behavior being claimed. *State* outputs backed by the live compartment grid
    /// (`soil__water_content_in_compartments`, `soil__water_content_in_layers`,
    /// `soil_water_actual_layer-N`, `soil_depth~compartment`) are the exception: they read the
    /// actual initial condition from `initialize()` onward, and reflect a `set_value` write
    /// immediately, without requiring an `update()` first.
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::UnknownVariable`] for an unresolvable name,
    /// [`BmiError::UnsupportedOperation`] for a set-only (write-only) variable, matching the
    /// reference's own get/set asymmetry, [`BmiError::WrongShape`] if `dest.len()` does not match
    /// [`Self::grid_size`], and the lifecycle errors from [`Self::running`].
    pub fn get_value(&self, name: &str, dest: &mut [f64]) -> Result<(), BmiError> {
        let canonical = registry::resolve_alias(name).to_owned();
        let expected = self.grid_size(name)?;
        if dest.len() != expected {
            return Err(BmiError::WrongShape {
                name: canonical,
                expected,
                actual: dest.len(),
            });
        }
        let state = self.running()?;
        let last = state.last_result.as_ref();

        if canonical == "soil__water_content_in_layers" {
            for (index, slot) in dest.iter_mut().enumerate() {
                *slot = Self::layer_theta(state, index);
            }
            return Ok(());
        }
        if canonical == "soil__water_content_in_compartments" {
            for (slot, compartment) in dest.iter_mut().zip(state.simulation.compartments()) {
                *slot = compartment.theta;
            }
            return Ok(());
        }
        if canonical == "soil_depth~compartment" {
            for (slot, compartment) in dest.iter_mut().zip(state.simulation.compartments()) {
                *slot = compartment.thickness;
            }
            return Ok(());
        }
        if let Some(layer_number) = Self::layer_number_suffix(&canonical) {
            dest[0] = Self::layer_theta_or_sentinel(state, layer_number);
            return Ok(());
        }

        let value = match canonical.as_str() {
            "plant_fertility-stress" => f64::from(state.fertility_stress),
            "air_precipitation" => last.map_or(0.0, |daily| daily.rain),
            "air_temperature_minimum~day" => last.map_or(0.0, |daily| daily.minimum_temperature),
            "air_temperature_maximal~day" => last.map_or(0.0, |daily| daily.maximum_temperature),
            "air_evapotranspiration~reference" => last.map_or(0.0, |daily| daily.reference_et),
            "management_irrigation_method" => state.irrigation_method.unwrap_or(0.0),
            "management_irrigation_amount" => last.map_or(0.0, |daily| daily.irrigation),
            "atmosphere_co2-concentration" => last.map_or(0.0, |daily| daily.carbon_dioxide),
            "management_mulch-cover" => f64::from(state.mulch_cover),
            "management_bund-height" => state.bund_height,
            "management_weed-cover" => last.map_or(0.0, |daily| daily.weed_cover),
            "plant_cover~projective" => last.map_or(0.0, |daily| daily.canopy_cover * 100.0),
            "plant_biomass~above-ground" => last.map_or(0.0, |daily| daily.biomass),
            "plant_yield~standard" => last.map_or(0.0, |daily| daily.dry_yield),
            "soil_water_actual" => last
                .and_then(|daily| daily.root_zone_water.as_ref())
                .map_or(0.0, |water| water.actual),
            "plant_stress_water" => state.stress.water_mean,
            "plant_stress_temperature" => state.stress.temperature_mean,
            "plant_stress_aeration" => {
                last.map_or(0.0, |daily| f64::from(daily.root_zone_anaerobic_days))
            }
            "plant_stress_salinity" => state.stress.salinity_mean,
            "plant_root_depth" => last.map_or(0.0, |daily| daily.rooting_depth),
            "air_transpiration" => state.cumulative_water_use.transpiration,
            "air_evapotranspiration~plants" => state.cumulative_water_use.evapotranspiration,
            "plant_biomass_potential" => last.map_or(0.0, |daily| daily.unrestricted_biomass),
            "soil_water_drainage~deep" => last.map_or(0.0, |daily| daily.drainage),
            "water_runoff~surface" => last.map_or(0.0, |daily| daily.runoff),
            "soil_infiltration~rate" => last.map_or(0.0, |daily| daily.infiltration),
            "air_evaporation" => last.map_or(0.0, |daily| daily.evaporation),
            "air_transpiration~actual" => last.map_or(0.0, |daily| daily.transpiration),
            "soil_water_capillary" => last.map_or(0.0, |daily| daily.capillary_rise),
            "crop__gdd_cumulative_from_planting" => {
                last.map_or(0.0, |daily| daily.cumulative_growing_degree_days)
            }
            _ => {
                return Err(BmiError::UnsupportedOperation {
                    name: canonical,
                    operation: "get",
                });
            }
        };
        dest[0] = value;
        Ok(())
    }

    /// Writes `src` into `name`, which must have exactly [`Self::grid_size`] elements.
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::UnknownVariable`], [`BmiError::UnsupportedOperation`] for a get-only
    /// (read-only) variable, [`BmiError::WrongShape`], or the lifecycle errors from
    /// [`Self::running_mut`].
    pub fn set_value(&mut self, name: &str, src: &[f64]) -> Result<(), BmiError> {
        let canonical = registry::resolve_alias(name).to_owned();
        let expected = self.grid_size(name)?;
        if src.len() != expected {
            return Err(BmiError::WrongShape {
                name: canonical,
                expected,
                actual: src.len(),
            });
        }
        let state = self.running_mut()?;

        if canonical == "soil__water_content_in_layers" {
            Self::scatter_layer_theta(state, src);
            return Ok(());
        }

        let value = src[0];
        match canonical.as_str() {
            "plant_fertility-stress" => {
                state.fertility_stress = aquacrop_core::compat::roundc_i32(value.clamp(0.0, 100.0));
            }
            "air_precipitation" => state.weather_overrides.rain = Some(value.max(0.0)),
            "air_temperature_minimum~day" => state.weather_overrides.minimum_temperature = Some(value),
            "air_temperature_maximal~day" => state.weather_overrides.maximum_temperature = Some(value),
            "air_evapotranspiration~reference" => {
                state.weather_overrides.reference_et = Some(value.max(0.0));
            }
            "management_irrigation_method" => {
                state.irrigation_method = Some(value.clamp(0.0, 4.0).round());
            }
            "management_irrigation_amount" => {
                if let Some(climate) = state.simulation.next_day_climate_mut() {
                    let conductivity = climate
                        .external_irrigation
                        .as_ref()
                        .map_or(0.0, |irrigation| irrigation.conductivity);
                    climate.external_irrigation = Some(aquacrop_core::irrigation::DailyIrrigation {
                        depth: value.max(0.0),
                        conductivity,
                    });
                }
            }
            "atmosphere_co2-concentration" => state.co2_override = Some(value.max(280.0)),
            "management_mulch-cover" => {
                state.mulch_cover = aquacrop_core::compat::roundc_i32(value.clamp(0.0, 100.0));
            }
            "management_bund-height" => state.bund_height = value.max(0.0),
            "management_weed-cover" => {
                // Tracked for get-value symmetry only; see the "management overlay" note on
                // RunState. There is no live per-day weed_cover slot to write into the way there
                // is for climate, so this cannot be echoed back through DailyResult.weed_cover
                // (which reflects aquacrop-core's own baked-in configuration, unaffected by this
                // write). `get_value` for this name reads `DailyResult::weed_cover`,
                // not this write, since that is what actually drove the simulation.
                let _ = value;
            }
            "bmi__clear_weather_overrides" => {
                if value != 0.0 {
                    state.weather_overrides = WeatherOverrides::default();
                    state.infiltration_override = None;
                }
            }
            "groundwater__depth" => state.groundwater_depth = Some(value),
            "groundwater__ec" => state.groundwater_ec = Some(value),
            "soil_water__infiltration_amount" => {
                state.infiltration_override = Some(value.max(0.0));
            }
            _ => {
                return Err(BmiError::UnsupportedOperation {
                    name: canonical,
                    operation: "set",
                });
            }
        }
        Ok(())
    }

    /// Scatter-writes `src` into live compartments. A compartment spanning two layers receives
    /// the value of the later layer. The deepest layer extends to the compartment-grid depth.
    fn scatter_layer_theta(state: &mut RunState, src: &[f64]) {
        let mut thicknesses = state.snapshot.soil_layer_thicknesses.clone();
        let declared_depth: f64 = thicknesses.iter().sum();
        let compartment_depth: f64 = state
            .simulation
            .compartments_mut()
            .iter()
            .map(|compartment| compartment.thickness)
            .sum();
        if compartment_depth > declared_depth
            && let Some(last) = thicknesses.last_mut()
        {
            *last += compartment_depth - declared_depth;
        }
        let mut layer_top = 0.0_f64;
        for (layer_index, (&layer_thickness, &target)) in
            thicknesses.iter().zip(src.iter()).enumerate()
        {
            let layer_bottom = layer_top + layer_thickness;
            let mut compartment_top = 0.0_f64;
            for compartment in state.simulation.compartments_mut() {
                let compartment_bottom = compartment_top + compartment.thickness;
                if Self::ranges_meaningfully_overlap(
                    compartment_top,
                    compartment_bottom,
                    layer_top,
                    layer_bottom,
                ) {
                    compartment.theta = target;
                }
                compartment_top = compartment_bottom;
                if compartment_top >= layer_bottom - Self::OVERLAP_EPSILON {
                    break;
                }
            }
            let _ = layer_index;
            layer_top = layer_bottom;
        }
    }

    /// `EPSILON` above every zero-overlap comparison in [`Self::ranges_meaningfully_overlap`] and
    /// its call site's loop-termination check: far above any realistic float noise at these
    /// magnitudes (~1e-15, confirmed by direct sweep -- see that function's docs) and far below
    /// the smallest physically meaningful overlap (a layer/compartment thickness, always >=
    /// centimeters), matching this codebase's other zero-guard thresholds (e.g. Fortran's own
    /// `ac_zero_threshold = 0.000001_dp`).
    const OVERLAP_EPSILON: f64 = 0.000_001;

    /// Whether compartment range `[compartment_top, compartment_bottom)` and layer range
    /// `[layer_top, layer_bottom)` overlap by more than float noise.
    ///
    /// `layer_top`/`compartment_top` (and their matching bottoms) are two independently
    /// floating-point-accumulated running sums (layer boundaries from `soil_layer_thicknesses`,
    /// compartment boundaries from each compartment's own `thickness`) that are frequently
    /// *meant* to coincide exactly at a shared boundary -- e.g. the point where the compartment
    /// grid's total depth is stretched to exactly match a layer's extended depth in
    /// `scatter_layer_theta`. Because they come from different summation sequences, floating-point
    /// rounding can leave them disagreeing by ~1e-16 at a boundary that should be exact. A bare
    /// `overlap > 0.0` check (this function's predecessor) registers that as a spurious positive
    /// overlap and writes a compartment an extra, unintended time -- confirmed to actually happen,
    /// not just a theoretical risk, by a systematic sweep over realistic layer/compartment
    /// thickness combinations: a 20-layer/12-compartment grid matching this crate's
    /// own GOSHA fixture produced a layer boundary computed as `1.50000000000000022204` against an
    /// independently-computed compartment boundary of exactly `1.5`, a spurious `2.22e-16`
    /// "overlap" under the old check.
    fn ranges_meaningfully_overlap(
        compartment_top: f64,
        compartment_bottom: f64,
        layer_top: f64,
        layer_bottom: f64,
    ) -> bool {
        let overlap = (compartment_bottom.min(layer_bottom) - compartment_top.max(layer_top)).max(0.0);
        overlap > Self::OVERLAP_EPSILON
    }

    /// Reads `name`'s value(s) at the given zero-based `indices` into `dest` (`dest.len()` must
    /// equal `indices.len()`).
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::IndexOutOfRange`] for any index at or beyond [`Self::grid_size`], plus
    /// the errors from [`Self::get_value`] for the underlying full-variable read.
    pub fn get_value_at_indices(
        &self,
        name: &str,
        dest: &mut [f64],
        indices: &[usize],
    ) -> Result<(), BmiError> {
        let size = self.grid_size(name)?;
        let mut full = vec![0.0; size];
        self.get_value(name, &mut full)?;
        for (slot, &index) in dest.iter_mut().zip(indices) {
            *slot = *full.get(index).ok_or_else(|| BmiError::IndexOutOfRange {
                name: registry::resolve_alias(name).to_owned(),
                index,
                size,
            })?;
        }
        Ok(())
    }

    /// Writes `src` into `name`'s value(s) at the given zero-based `indices`.
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::IndexOutOfRange`] for any index at or beyond [`Self::grid_size`], plus
    /// the errors from [`Self::get_value`]/[`Self::set_value`] for the underlying full-variable
    /// read/write (a full read-modify-write, since `aquacrop-core` has no partial-vector setter).
    pub fn set_value_at_indices(
        &mut self,
        name: &str,
        indices: &[usize],
        src: &[f64],
    ) -> Result<(), BmiError> {
        let size = self.grid_size(name)?;
        let mut full = vec![0.0; size];
        self.get_value(name, &mut full)?;
        for (&index, &value) in indices.iter().zip(src) {
            let slot = full.get_mut(index).ok_or_else(|| BmiError::IndexOutOfRange {
                name: registry::resolve_alias(name).to_owned(),
                index,
                size,
            })?;
            *slot = value;
        }
        self.set_value(name, &full)
    }
}

#[cfg(test)]
mod overlap_epsilon_tests {
    use super::*;

    /// A genuinely, substantially overlapping pair must always register as overlapping,
    /// regardless of the epsilon guard -- proving the fix can never mask a real overlap. Every
    /// real overlap in this codebase's soil grids is at minimum a fraction of a compartment or
    /// layer thickness (always >= centimeters), vastly larger than `OVERLAP_EPSILON` (1e-6).
    #[test]
    fn a_real_overlap_of_several_centimeters_registers() {
        assert!(BmiModel::ranges_meaningfully_overlap(0.10, 0.25, 0.10, 0.20));
        assert!(BmiModel::ranges_meaningfully_overlap(0.10, 0.25, 0.20, 0.30));
        assert!(BmiModel::ranges_meaningfully_overlap(0.0, 1.20, 0.0, 2.0));
    }

    /// A pair with no overlap at all (a clean gap between them) must never register, regardless
    /// of the epsilon guard.
    #[test]
    fn disjoint_ranges_never_register() {
        assert!(!BmiModel::ranges_meaningfully_overlap(0.0, 0.10, 0.10, 0.20));
        assert!(!BmiModel::ranges_meaningfully_overlap(0.20, 0.30, 0.0, 0.10));
    }

    /// The actual regression: two boundaries that are *mathematically* meant to coincide exactly
    /// (a compartment's bottom and the next range's top at the same true depth) but were computed
    /// via two independent floating-point summations that disagree by ~1e-16 -- exactly the
    /// values observed for a 20-layer/12-compartment GOSHA-style grid (a layer boundary accumulated to
    /// `1.50000000000000022204` against an independently-accumulated compartment boundary of
    /// exactly `1.5`). The old bare `overlap > 0.0` check registered this as a spurious overlap;
    /// `ranges_meaningfully_overlap` must not.
    #[test]
    fn a_float_noise_disagreement_at_a_true_boundary_does_not_register() {
        let compartment_top = 1.5_f64;
        let compartment_bottom = 1.75_f64;
        let layer_top = 1.400_000_000_000_000_1_f64;
        let layer_bottom = 1.500_000_000_000_000_2_f64;

        // Sanity check: confirm this reproduces genuine float disagreement, not a coincidence of
        // hand-picked equal values -- if this assertion ever fails, the hardcoded constants above
        // no longer reproduce the drift and need updating from a fresh sweep, not deleting.
        assert!(
            compartment_top < layer_bottom,
            "expected compartment_top to sit fractionally below layer_bottom, reproducing the \
             exact drift direction that causes the spurious overlap"
        );
        let raw_overlap = (compartment_bottom.min(layer_bottom) - compartment_top.max(layer_top)).max(0.0);
        assert!(
            raw_overlap > 0.0 && raw_overlap < 1e-9,
            "expected a tiny but strictly positive raw overlap (float noise), got {raw_overlap:.2e} \
             -- this test's premise depends on it"
        );

        assert!(
            !BmiModel::ranges_meaningfully_overlap(
                compartment_top,
                compartment_bottom,
                layer_top,
                layer_bottom
            ),
            "a {raw_overlap:.2e} disagreement between two boundaries meant to coincide exactly \
             must not register as a real overlap"
        );
    }

    /// A genuine overlap that happens to be very small (a compartment barely straddling a layer
    /// boundary by a fraction of a millimeter, not by float noise) must still register -- the
    /// epsilon guards against *drift*, not against small-but-real geometry.
    #[test]
    fn a_small_but_genuine_overlap_still_registers() {
        // 1mm of real overlap -- three orders of magnitude above OVERLAP_EPSILON (1e-6 = 1
        // micrometer), nowhere near float noise (~1e-16) at these magnitudes.
        assert!(BmiModel::ranges_meaningfully_overlap(0.599, 0.700, 0.0, 0.600));
    }
}
