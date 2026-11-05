//! `PyO3` binding exposing [`aquacrop_bmi::BmiModel`] as a BMI 2.0 Python class.
//!
//! This crate is a thin shim: every method here does argument/numpy-array conversion and then
//! delegates directly to `aquacrop_bmi::BmiModel`, which is pure Rust and independently tested
//! (see `crates/aquacrop-bmi/tests/`). No simulation logic, registry data, or override bookkeeping
//! lives here.
//!
//! `#[pymethods]` requires every exposed method to take `&self`/`&mut self` even when the Rust
//! body doesn't need it (Python instance methods always do), and `PyO3`'s extractor types
//! (`PyReadonlyArray1`, `PyReadwriteArray1`) are conventionally taken by value, not by reference.
//! both trip generic Clippy lints that don't know about these framework conventions.
#![allow(clippy::unused_self, clippy::needless_pass_by_value)]

use aquacrop_bmi::BmiModel;
use aquacrop_bmi::error::BmiError;
use numpy::{PyArray1, PyArrayMethods, PyReadonlyArray1, PyReadwriteArray1};
use pyo3::exceptions::{PyIndexError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;

fn to_python_error(error: BmiError) -> PyErr {
    match error {
        BmiError::UnknownVariable(_)
        | BmiError::WrongShape { .. }
        | BmiError::IndexOutOfRange { .. } => PyValueError::new_err(error.to_string()),
        BmiError::UnsupportedOperation { .. } => {
            PyErr::new::<pyo3::exceptions::PyNotImplementedError, _>(error.to_string())
        }
        BmiError::NotInitialized
        | BmiError::AlreadyInitialized
        | BmiError::AlreadyFinalized
        | BmiError::Simulation(_)
        | BmiError::InvalidConfiguration(_) => PyRuntimeError::new_err(error.to_string()),
    }
}

/// BMI 2.0 AquaCrop model, backed by the Rust `aquacrop-core` engine.
///
/// `initialize` reads a JSON scenario from a file path, as required by BMI 2.0.
/// Use [`Self::initialize_from_json`] or [`Self::initialize_from_dict`] for in-memory scenarios.
// `pymt` uses `__module__` to find package metadata.
#[pyclass(name = "AquaCropBmi", module = "aquacrop_bmi_rs")]
struct PyAquaCropBmi {
    model: BmiModel,
}

#[pymethods]
impl PyAquaCropBmi {
    #[new]
    fn new() -> Self {
        Self {
            model: BmiModel::new(),
        }
    }

    fn get_component_name(&self) -> &'static str {
        BmiModel::component_name()
    }

    /// Initializes the model from a JSON scenario file.
    fn initialize(&mut self, path: &str) -> PyResult<()> {
        let content = std::fs::read_to_string(path).map_err(|error| {
            PyRuntimeError::new_err(format!("failed to read config file '{path}': {error}"))
        })?;
        self.model.initialize(&content).map_err(to_python_error)
    }

    /// Initializes the model from an in-memory JSON scenario.
    fn initialize_from_json(&mut self, json_string: &str) -> PyResult<()> {
        self.model.initialize(json_string).map_err(to_python_error)
    }

    /// Initializes the model from a Python dictionary.
    fn initialize_from_dict(&mut self, py: Python<'_>, config: &Bound<'_, PyAny>) -> PyResult<()> {
        let json_module = py.import("json")?;
        let json_string: String = json_module.call_method1("dumps", (config,))?.extract()?;
        self.initialize_from_json(&json_string)
    }

    fn update(&mut self) -> PyResult<()> {
        self.model.update().map_err(to_python_error)
    }

    fn update_until(&mut self, time: f64) -> PyResult<()> {
        self.model.update_until(time).map_err(to_python_error)
    }

    fn finalize(&mut self) -> PyResult<()> {
        self.model.finalize().map_err(to_python_error)
    }

    fn get_input_item_count(&self) -> usize {
        BmiModel::input_var_names().len()
    }

    fn get_output_item_count(&self) -> usize {
        BmiModel::output_var_names().len()
    }

    fn get_input_var_names(&self) -> Vec<&'static str> {
        BmiModel::input_var_names().to_vec()
    }

    fn get_output_var_names(&self) -> Vec<&'static str> {
        BmiModel::output_var_names().to_vec()
    }

    fn get_start_time(&self) -> f64 {
        BmiModel::start_time()
    }

    fn get_end_time(&self) -> PyResult<f64> {
        self.model.end_time().map_err(to_python_error)
    }

    fn get_current_time(&self) -> PyResult<f64> {
        self.model.current_time().map_err(to_python_error)
    }

    fn get_time_step(&self) -> f64 {
        BmiModel::time_step()
    }

    fn get_time_units(&self) -> &'static str {
        BmiModel::time_units()
    }

    fn get_var_grid(&self, name: &str) -> PyResult<u8> {
        BmiModel::var_grid(name).map_err(to_python_error)
    }

    fn get_var_type(&self, name: &str) -> PyResult<&'static str> {
        BmiModel::var_type(name).map_err(to_python_error)
    }

    fn get_var_units(&self, name: &str) -> PyResult<&'static str> {
        BmiModel::var_units(name).map_err(to_python_error)
    }

    fn get_var_itemsize(&self, name: &str) -> PyResult<usize> {
        BmiModel::var_itemsize(name).map_err(to_python_error)
    }

    fn get_var_nbytes(&self, name: &str) -> PyResult<usize> {
        self.model.var_nbytes(name).map_err(to_python_error)
    }

    fn get_var_location(&self, name: &str) -> PyResult<&'static str> {
        BmiModel::var_location(name).map_err(to_python_error)
    }

    fn get_grid_type(&self, grid: u8) -> &'static str {
        match grid {
            0 => "scalar",
            _ => "uniform_rectilinear",
        }
    }

    fn get_grid_rank(&self, grid: u8) -> u8 {
        u8::from(grid != 0)
    }

    /// Not implemented, matching the reference (`bmi_aquacrop.f90:1375-1407`, which never
    /// associates a real pointer either. This is documented non-conformance parity, not a
    /// missing feature to silently fake.
    fn get_value_ptr(&self, name: &str) -> PyResult<()> {
        Err(PyErr::new::<pyo3::exceptions::PyNotImplementedError, _>(
            format!(
                "get_value_ptr is unsupported for {name}, matching the reference (no true pointer access anywhere in bmi_aquacrop.f90)"
            ),
        ))
    }

    fn get_value<'py>(
        &self,
        py: Python<'py>,
        name: &str,
        dest: PyReadwriteArray1<'py, f64>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        let mut owned = vec![0.0; dest.len()?];
        self.model
            .get_value(name, &mut owned)
            .map_err(to_python_error)?;
        let mut dest = dest;
        dest.as_slice_mut()?.copy_from_slice(&owned);
        Ok(PyArray1::from_slice(py, &owned))
    }

    fn set_value(&mut self, name: &str, src: PyReadonlyArray1<'_, f64>) -> PyResult<()> {
        self.model
            .set_value(name, src.as_slice()?)
            .map_err(to_python_error)
    }

    fn get_value_at_indices<'py>(
        &self,
        py: Python<'py>,
        name: &str,
        dest: PyReadwriteArray1<'py, f64>,
        indices: PyReadonlyArray1<'py, i64>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        let indices = to_usize_indices(indices.as_slice()?)?;
        let mut owned = vec![0.0; dest.len()?];
        self.model
            .get_value_at_indices(name, &mut owned, &indices)
            .map_err(to_python_error)?;
        let mut dest = dest;
        dest.as_slice_mut()?.copy_from_slice(&owned);
        Ok(PyArray1::from_slice(py, &owned))
    }

    fn set_value_at_indices(
        &mut self,
        name: &str,
        indices: PyReadonlyArray1<'_, i64>,
        src: PyReadonlyArray1<'_, f64>,
    ) -> PyResult<()> {
        let indices = to_usize_indices(indices.as_slice()?)?;
        self.model
            .set_value_at_indices(name, &indices, src.as_slice()?)
            .map_err(to_python_error)
    }

    fn get_grid_size(&self, grid: u8) -> PyResult<usize> {
        self.model
            .grid_size_for_grid_id(grid)
            .map_err(to_python_error)
    }

    /// Matches the reference (`bmi_aquacrop.f90:1166-1182`): defined only for grids 1 and 2.
    ///
    /// Takes `shape` as `int32`, not `int64`: `bmi-tester` (and the BMI convention it tests
    /// against) constructs this buffer as `np.empty(ndim, dtype=np.int32)`, and `PyO3`/`numpy`
    /// extraction requires an exact dtype match -- an `int64` parameter rejects that array outright
    /// (surfacing as the unhelpful `TypeError: 'ndarray' object is not an instance of 'ndarray'`).
    /// Returns the *same* array object that was passed in (mutated in place), not a fresh copy:
    /// the BMI convention this mirrors (see the reference's own `bmi_aquacrop.py`,
    /// `shape[:] = [...]; return shape`) is an in-place fill where `result is shape` holds for
    /// callers that check it.
    fn get_grid_shape<'py>(
        &self,
        grid: u8,
        shape: Bound<'py, PyArray1<i32>>,
    ) -> PyResult<Bound<'py, PyArray1<i32>>> {
        match grid {
            1 | 2 => {
                let size = self
                    .model
                    .grid_size_for_grid_id(grid)
                    .map_err(to_python_error)?;
                shape.readwrite().as_slice_mut()?[0] = i32::try_from(size)
                    .map_err(|_| PyValueError::new_err("grid size does not fit in i32"))?;
                Ok(shape)
            }
            _ => Err(grid_only_defined_for("get_grid_shape", "1 and 2")),
        }
    }

    /// Matches the reference (`bmi_aquacrop.f90:1186-1202`): hardcoded `1.0` for grids 1/2, not a
    /// physically meaningful spacing value. The provided array is updated in place.
    fn get_grid_spacing<'py>(
        &self,
        grid: u8,
        spacing: Bound<'py, PyArray1<f64>>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        match grid {
            1 | 2 => {
                spacing.readwrite().as_slice_mut()?[0] = 1.0;
                Ok(spacing)
            }
            _ => Err(grid_only_defined_for("get_grid_spacing", "1 and 2")),
        }
    }

    /// Matches the reference (`bmi_aquacrop.f90:1206-1222`): hardcoded `0.0` for grids 1/2. Returns
    /// the same array object passed in, mutated in place — see [`Self::get_grid_shape`]'s doc
    /// comment.
    fn get_grid_origin<'py>(
        &self,
        grid: u8,
        origin: Bound<'py, PyArray1<f64>>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        match grid {
            1 | 2 => {
                origin.readwrite().as_slice_mut()?[0] = 0.0;
                Ok(origin)
            }
            _ => Err(grid_only_defined_for("get_grid_origin", "1 and 2")),
        }
    }

    /// Matches the reference (`bmi_aquacrop.f90:1226-1276`): only grid 0 is handled, hardcoded
    /// `0.0` with `TODO` comments referencing project longitude/latitude/altitude in the
    /// reference. It is not physically implemented there either.
    fn get_grid_x<'py>(
        &self,
        grid: u8,
        x: Bound<'py, PyArray1<f64>>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        Self::grid_zero_coordinate(grid, x, "get_grid_x")
    }

    fn get_grid_y<'py>(
        &self,
        grid: u8,
        y: Bound<'py, PyArray1<f64>>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        Self::grid_zero_coordinate(grid, y, "get_grid_y")
    }

    fn get_grid_z<'py>(
        &self,
        grid: u8,
        z: Bound<'py, PyArray1<f64>>,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        Self::grid_zero_coordinate(grid, z, "get_grid_z")
    }

    /// Matches the reference (`bmi_aquacrop.f90:1280-1293`): only grid 0 is handled (`1`).
    fn get_grid_node_count(&self, grid: u8) -> PyResult<usize> {
        match grid {
            0 => Ok(1),
            _ => Err(grid_only_defined_for("get_grid_node_count", "0")),
        }
    }

    /// Unconditionally unsupported, matching the reference (`bmi_aquacrop.f90:1297-1369`:
    /// "Not applicable for scalar grid" for every grid, including 1 and 2).
    fn get_grid_edge_count(&self, _grid: u8) -> PyResult<usize> {
        Err(not_applicable("get_grid_edge_count"))
    }

    fn get_grid_face_count(&self, _grid: u8) -> PyResult<usize> {
        Err(not_applicable("get_grid_face_count"))
    }

    fn get_grid_edge_nodes(
        &self,
        _grid: u8,
        _edge_nodes: PyReadwriteArray1<'_, i64>,
    ) -> PyResult<()> {
        Err(not_applicable("get_grid_edge_nodes"))
    }

    fn get_grid_face_edges(
        &self,
        _grid: u8,
        _face_edges: PyReadwriteArray1<'_, i64>,
    ) -> PyResult<()> {
        Err(not_applicable("get_grid_face_edges"))
    }

    fn get_grid_face_nodes(
        &self,
        _grid: u8,
        _face_nodes: PyReadwriteArray1<'_, i64>,
    ) -> PyResult<()> {
        Err(not_applicable("get_grid_face_nodes"))
    }

    fn get_grid_nodes_per_face(
        &self,
        _grid: u8,
        _nodes_per_face: PyReadwriteArray1<'_, i64>,
    ) -> PyResult<()> {
        Err(not_applicable("get_grid_nodes_per_face"))
    }
}

impl PyAquaCropBmi {
    fn grid_zero_coordinate<'py>(
        grid: u8,
        coordinate: Bound<'py, PyArray1<f64>>,
        method: &'static str,
    ) -> PyResult<Bound<'py, PyArray1<f64>>> {
        match grid {
            0 => {
                coordinate.readwrite().as_slice_mut()?[0] = 0.0;
                Ok(coordinate)
            }
            _ => Err(grid_only_defined_for(method, "0")),
        }
    }
}

fn grid_only_defined_for(method: &str, grids: &str) -> PyErr {
    PyErr::new::<pyo3::exceptions::PyNotImplementedError, _>(format!(
        "{method} is only defined for grid(s) {grids}, matching the reference"
    ))
}

fn not_applicable(method: &str) -> PyErr {
    PyErr::new::<pyo3::exceptions::PyNotImplementedError, _>(format!(
        "{method} is not applicable for any grid in this model, matching the reference \
         (unconditionally unsupported in bmi_aquacrop.f90)"
    ))
}

fn to_usize_indices(raw: &[i64]) -> PyResult<Vec<usize>> {
    raw.iter()
        .map(|&value| {
            usize::try_from(value)
                .map_err(|_| PyIndexError::new_err(format!("negative index {value} is invalid")))
        })
        .collect()
}

/// `PyO3` module entry point. The Python-facing package (`aquacrop_bmi_rs`) re-exports
/// `AquaCropBmi` from this native extension module.
#[pymodule]
fn _aquacrop_bmi_rs(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PyAquaCropBmi>()?;
    Ok(())
}
