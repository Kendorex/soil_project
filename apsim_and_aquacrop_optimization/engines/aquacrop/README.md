> Workspace migration: this directory contains the calculation engine. The field model and Python preparation pipeline have moved to `../../models/aquacrop`; the field runner is in `field-runner`. See [workspace instructions](../../README.md).

# AquaCrop for Rust

This workspace contains a from-scratch Rust implementation of the FAO AquaCrop crop-growth model.
The numerical core reproduces the currently supported AquaCrop version, exposes an explicit daily
state machine, reads standard project directories through an optional feature, and is exercised
against the vendored official testcase.

Compatibility is pinned by the version of the reference implementation in `third-party/KUL-RSDA/AquaCrop`. The compatibility primitives are exposed by
`aquacrop-core` as `AQUACROP_COMPATIBLE_VERSION`, `AQUACROP_REFERENCE_TAG`, and
`AQUACROP_REFERENCE_COMMIT`.

## Workspace

- [`aquacrop-core`](crates/aquacrop-core) implements model types, compatibility primitives,
  individual processes, the daily simulation engine, aggregation, and AquaCrop file/project I/O
  behind its `file-io` feature.
- [`aquacrop`](crates/aquacrop) is the Rust-facing facade. It currently provides a stable crate
  boundary and re-exports `aquacrop-core`; higher-level builders, typed units, and iterator adapters
  remain future facade work.
- [`aquacrop-cli`](crates/aquacrop-cli) builds the `aquacrop` executable. It runs `.PRO` and `.PRM`
  projects from a standard AquaCrop directory and writes the requested result files to `OUTP`.
- [`aquacrop-conformance`](crates/aquacrop-conformance) is a private test crate containing the
  single current testcase and cross-crate numerical regression checks.

## Implemented model

The core includes AquaCrop calendar and rounding behavior, soil profiles, water and salt balances,
runoff, infiltration, drainage, capillary rise, evaporation, transpiration and root extraction,
crop and root development, temperature/water/fertility/salinity stress, biomass, harvest index,
yield, weeds, cuttings, perennial assimilate transfer, and the integrated daily process order.

Three execution paths are available:

- Construct `aquacrop_core::engine::Simulation` directly, call `step()` for each model day, and
  consume the final state with `finalize()`.
- Enable `aquacrop-core/file-io` and use the project readers and runners in
  `aquacrop_core::io`.
- Run the `aquacrop` executable against an AquaCrop directory.

The file-based runner supports the input and output paths exercised by the current official
testcase. Known compatibility gaps are listed in the [development status](docs/roadmap.md); the
verified testcase must not be interpreted as coverage of every possible AquaCrop project layout.

## Command-line use

Run projects in the current directory:

```sh
cargo run --release -p aquacrop-cli --
```

Run a different AquaCrop project directory:

```sh
cargo run --release -p aquacrop-cli -- --root /path/to/project
```

The executable reads `LIST/ListProjects.txt`. If that file is absent, it discovers `.PRO` and
`.PRM` files directly under `LIST` in filename order.

## Direct Rust API example

[`official_fixture.rs`](crates/aquacrop-core/examples/official_fixture.rs) runs the official
Ottawa fixture through the direct `aquacrop-core` API. It reads configuration from YAML and daily
weather and atmospheric CO2 observations from CSV instead of AquaCrop project and climate files,
then prepares and runs a `SimulationConfig` and prints a seasonal report:

```sh
cargo run -p aquacrop-core --example official_fixture --features serde
```

## Verification

The conformance suite copies input files from
[`third-party/KUL-RSDA/AquaCrop/testcase`](third-party/KUL-RSDA/AquaCrop/testcase) to a temporary
directory, runs the Rust simulation there, and compares the generated output set with `OUTP_REF`.
Timestamp headers are the only intentionally ignored output content.

Run the repository checks with:

```sh
cargo fmt --all --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
cargo doc --workspace --all-features --no-deps
```

See the [architecture](docs/architecture.md), [development status](docs/roadmap.md), and
[contribution guide](CONTRIBUTING.md) for current boundaries and workflow. The tracked
[compatibility reference](spec/aquacrop-porting-reference-v7.3/index.md) remains the guide for
scientific equations, units, and daily ordering; it is no longer a statement of implementation
progress.

## License

The Rust implementation is licensed under the BSD 3-Clause License. Vendored AquaCrop testcase
material retains its original KU Leuven copyright and license notice.
