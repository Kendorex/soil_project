# aquacrop

Rust-facing facade for the AquaCrop crop-growth model.

The crate currently re-exports the compatible low-level implementation as `aquacrop::core`:

```rust
use aquacrop::core;

let version = core::AQUACROP_COMPATIBLE_VERSION;
assert!(!version.is_empty());
```

This deliberately keeps a separate facade boundary while the numerical implementation stabilizes.
The planned higher-level builders, type-safe units, validation conveniences, and daily iterator
adapter are not implemented yet. Until they are, applications should use the types and
`Simulation` lifecycle available through `aquacrop::core`.

Enable the `file-io` feature to expose `aquacrop::core::io` and the supported AquaCrop readers,
project runners, and result writers.

Enable the `serde` feature to add `Serialize` and `Deserialize` implementations to the public
simulation input and output contracts re-exported through `aquacrop::core`.
