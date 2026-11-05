//! Canonical variable registry: names, aliases, units, and grids.
//!
//! Entries are derived from the reference `aquacrop_fortran/bmi_aquacrop.f90` source at commit
//! `bdfdb7fac59c098c4795bab32733285aecdfc2c2`.

/// Whether a canonical variable is a BMI input, output, or both.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    /// Settable only (appears in the reference's `input_items`).
    Input,
    /// Gettable only (appears in the reference's `output_items`).
    Output,
    /// Both settable and gettable (`soil__water_content_in_layers` is the only such name).
    Both,
}

/// Static metadata for one canonical variable, matching the reference's declared contract.
#[derive(Debug, Clone, Copy)]
pub struct VariableMeta {
    /// Exact canonical name, as declared in the reference `input_items`/`output_items`.
    pub name: &'static str,
    /// Units string returned by the reference `get_var_units`.
    pub units: &'static str,
    /// Grid id: 0 = scalar, 1 = per-soil-layer vector, 2 = per-compartment vector.
    pub grid: u8,
    /// Input, output, or both.
    pub role: Role,
}

/// Number of BMI input variables, matching the reference `input_item_count`.
pub const INPUT_ITEM_COUNT: usize = 16;
/// Number of BMI output variables. `31` matches the reference `output_item_count` as of the
/// registry's ledger snapshot; `+1` for `crop__gdd_cumulative_from_planting` (see the module
/// doc comment) brings this to `32`, one short of the reference's current `33` --
/// `crop__gdd_cumulative` is deliberately not yet added.
pub const OUTPUT_ITEM_COUNT: usize = 32;
/// Number of unique canonical names across inputs and outputs combined (`46` from the ledger
/// snapshot `+1` for `crop__gdd_cumulative_from_planting`).
pub const CANONICAL_NAME_COUNT: usize = 47;

/// Canonical input variable names, in the reference's declared `input_items` order.
pub const INPUT_NAMES: [&str; INPUT_ITEM_COUNT] = [
    "plant_fertility-stress",
    "air_precipitation",
    "air_temperature_minimum~day",
    "air_temperature_maximal~day",
    "air_evapotranspiration~reference",
    "management_irrigation_method",
    "management_irrigation_amount",
    "atmosphere_co2-concentration",
    "management_mulch-cover",
    "management_bund-height",
    "management_weed-cover",
    "bmi__clear_weather_overrides",
    "soil__water_content_in_layers",
    "groundwater__depth",
    "groundwater__ec",
    "soil_water__infiltration_amount",
];

/// Canonical output variable names, in the reference's declared `output_items` order.
pub const OUTPUT_NAMES: [&str; OUTPUT_ITEM_COUNT] = [
    "plant_cover~projective",
    "plant_biomass~above-ground",
    "plant_yield~standard",
    "soil_water_actual",
    "plant_stress_water",
    "plant_stress_temperature",
    "plant_stress_aeration",
    "plant_stress_salinity",
    "plant_root_depth",
    "air_transpiration",
    "air_evapotranspiration~plants",
    "plant_biomass_potential",
    "soil_water_actual_layer-1",
    "soil_water_actual_layer-2",
    "soil_water_actual_layer-3",
    "soil_water_actual_layer-4",
    "soil_water_actual_layer-5",
    "soil_water_actual_layer-6",
    "soil_water_actual_layer-7",
    "soil_water_actual_layer-8",
    "soil_water_actual_layer-9",
    "soil_water_actual_layer-10",
    "soil__water_content_in_layers",
    "soil__water_content_in_compartments",
    "soil_depth~compartment",
    "soil_water_drainage~deep",
    "water_runoff~surface",
    "soil_infiltration~rate",
    "air_evaporation",
    "air_transpiration~actual",
    "soil_water_capillary",
    "crop__gdd_cumulative_from_planting",
];

/// Number of alias entries in `resolve_var_alias`.
pub const ALIAS_COUNT: usize = 41;

/// Every `(alias, canonical)` pair from the reference's `resolve_var_alias`, in declaration order.
///
/// `management__surface_storage` resolves to `management_surface-storage`, which the reference
/// itself never wires to a getter, setter, or units case. Calling get/set with either name
/// fails against the reference too. This orphaned alias is preserved here for exact conformance,
/// not silently dropped.
pub const ALIASES: [(&str, &str); ALIAS_COUNT] = [
    ("crop__yield", "plant_yield~standard"),
    ("crop__biomass", "plant_biomass~above-ground"),
    ("crop__biomass_potential", "plant_biomass_potential"),
    ("crop__canopy_cover", "plant_cover~projective"),
    ("crop__rooting_depth", "plant_root_depth"),
    ("crop__transpiration", "air_transpiration"),
    ("crop__evapotranspiration", "air_evapotranspiration~plants"),
    ("crop__water_stress", "plant_stress_water"),
    ("crop__temperature_stress", "plant_stress_temperature"),
    ("crop__aeration_stress", "plant_stress_aeration"),
    ("crop__salinity_stress", "plant_stress_salinity"),
    ("soil__moisture", "soil_water_actual"),
    ("soil__moisture_layer_1", "soil_water_actual_layer-1"),
    ("soil__moisture_layer_2", "soil_water_actual_layer-2"),
    ("soil__moisture_layer_3", "soil_water_actual_layer-3"),
    ("soil__moisture_layer_4", "soil_water_actual_layer-4"),
    ("soil__moisture_layer_5", "soil_water_actual_layer-5"),
    ("soil__moisture_layer_6", "soil_water_actual_layer-6"),
    ("soil__moisture_layer_7", "soil_water_actual_layer-7"),
    ("soil__moisture_layer_8", "soil_water_actual_layer-8"),
    ("soil__moisture_layer_9", "soil_water_actual_layer-9"),
    ("soil__moisture_layer_10", "soil_water_actual_layer-10"),
    ("weather__rainfall_amount", "air_precipitation"),
    (
        "weather__air_temperature_max",
        "air_temperature_maximal~day",
    ),
    (
        "weather__air_temperature_min",
        "air_temperature_minimum~day",
    ),
    (
        "weather__reference_evapotranspiration",
        "air_evapotranspiration~reference",
    ),
    (
        "management__irrigation_amount",
        "management_irrigation_amount",
    ),
    (
        "management__irrigation_method",
        "management_irrigation_method",
    ),
    ("management__mulch_cover", "management_mulch-cover"),
    ("management__bund_height", "management_bund-height"),
    ("management__weed_cover", "management_weed-cover"),
    ("management__surface_storage", "management_surface-storage"),
    (
        "atmosphere__co2_concentration",
        "atmosphere_co2-concentration",
    ),
    ("crop__fertility_stress", "plant_fertility-stress"),
    ("soil__compartment_thickness", "soil_depth~compartment"),
    (
        "soil_water__deep_percolation_flux",
        "soil_water_drainage~deep",
    ),
    ("land_surface_water__runoff_flux", "water_runoff~surface"),
    ("soil_water__infiltration_flux", "soil_infiltration~rate"),
    ("soil__evaporation_flux", "air_evaporation"),
    (
        "crop__transpiration_flux_actual",
        "air_transpiration~actual",
    ),
    ("soil_water__capillary_rise_flux", "soil_water_capillary"),
];

/// Resolves an alias (or a canonical name, passed through unchanged) to its canonical name,
/// mirroring the reference's `resolve_var_alias` exactly (including the unmatched case, which
/// passes the input through unchanged rather than failing).
#[must_use]
pub fn resolve_alias(name: &str) -> &str {
    ALIASES
        .iter()
        .find_map(|(alias, canonical)| (*alias == name).then_some(*canonical))
        .unwrap_or(name)
}

/// Static metadata for every one of the 46 canonical names, in `input_items` then `output_items`
/// declaration order (duplicates removed, keeping the first occurrence).
pub const VARIABLES: [VariableMeta; CANONICAL_NAME_COUNT] = [
    VariableMeta {
        name: "plant_fertility-stress",
        units: "%",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "air_precipitation",
        units: "mm d-1",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "air_temperature_minimum~day",
        units: "degC",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "air_temperature_maximal~day",
        units: "degC",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "air_evapotranspiration~reference",
        units: "mm d-1",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "management_irrigation_method",
        units: "enumeration",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "management_irrigation_amount",
        units: "mm",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "atmosphere_co2-concentration",
        units: "ppm",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "management_mulch-cover",
        units: "%",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "management_bund-height",
        units: "m",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "management_weed-cover",
        units: "%",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "bmi__clear_weather_overrides",
        units: "-",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "soil__water_content_in_layers",
        units: "m3 m-3",
        grid: 1,
        role: Role::Both,
    },
    VariableMeta {
        name: "groundwater__depth",
        units: "m",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "groundwater__ec",
        units: "dS m-1",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "soil_water__infiltration_amount",
        units: "mm",
        grid: 0,
        role: Role::Input,
    },
    VariableMeta {
        name: "plant_cover~projective",
        units: "%",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "plant_biomass~above-ground",
        units: "t ha-1",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "plant_yield~standard",
        units: "t ha-1",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_actual",
        units: "mm",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "plant_stress_water",
        units: "days",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "plant_stress_temperature",
        units: "days",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "plant_stress_aeration",
        units: "days",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "plant_stress_salinity",
        units: "days",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "plant_root_depth",
        units: "m",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "air_transpiration",
        units: "mm",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "air_evapotranspiration~plants",
        units: "mm",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "plant_biomass_potential",
        units: "t ha-1",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_actual_layer-1",
        units: "m3 m-3",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_actual_layer-2",
        units: "m3 m-3",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_actual_layer-3",
        units: "m3 m-3",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_actual_layer-4",
        units: "m3 m-3",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_actual_layer-5",
        units: "m3 m-3",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_actual_layer-6",
        units: "m3 m-3",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_actual_layer-7",
        units: "m3 m-3",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_actual_layer-8",
        units: "m3 m-3",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_actual_layer-9",
        units: "m3 m-3",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_actual_layer-10",
        units: "m3 m-3",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil__water_content_in_compartments",
        units: "m3 m-3",
        grid: 2,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_depth~compartment",
        units: "m",
        grid: 2,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_drainage~deep",
        units: "mm",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "water_runoff~surface",
        units: "mm",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_infiltration~rate",
        units: "mm",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "air_evaporation",
        units: "mm",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "air_transpiration~actual",
        units: "mm",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "soil_water_capillary",
        units: "mm",
        grid: 0,
        role: Role::Output,
    },
    VariableMeta {
        name: "crop__gdd_cumulative_from_planting",
        units: "degC d",
        grid: 0,
        role: Role::Output,
    },
];

/// Looks up static metadata for a canonical name (aliases must be resolved first via
/// [`resolve_alias`]).
#[must_use]
pub fn metadata(canonical_name: &str) -> Option<&'static VariableMeta> {
    VARIABLES.iter().find(|meta| meta.name == canonical_name)
}
