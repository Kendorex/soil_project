# Standalone AquaCrop inputs

This folder contains an independent JSON/native-file input model. The runtime
uses only AquaCrop. It does not open an external model or simulation database.

Run from the project root:

```powershell
python run.py aquacrop --objectives yield profit --run
```

Without `--run`, the command prepares inputs only. Search configuration and
explicit economics are in `optimization.json`; soil/initial water/crop profiles
are in `configs/fixed_field_*.json`. Initial water is field capacity and the
2024 bridge precedes the evaluated 2025-2029 horizon. Input provenance and
uncalibrated effective-crop limitations are recorded in `input_provenance.json`.

See [complete instructions](../../docs/aquacrop_standalone.md).
