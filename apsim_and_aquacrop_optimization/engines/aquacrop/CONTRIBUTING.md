# Contributing

Repository content must be written in English, including documentation, comments, commit messages,
identifiers, diagnostics, and test names.

## Before changing code

Read the [architecture](docs/architecture.md) for the compatibility contract, crate boundaries,
API conventions, and test ownership. Check the [development status](docs/roadmap.md) before adding
new functionality or describing existing coverage. For calculation work, follow the architecture's
compatibility sources and numerical contract before changing implementation or expectations.

## Change workflow

1. Keep the change within the ownership boundaries defined by the architecture.
2. Add focused unit or integration tests at the boundary specified there.
3. For numerical changes, verify the formula and process order before updating integrated
   expectations.
4. Do not mutate the vendored testcase during a test. Run project simulations from temporary
   copies and compare generated files with the reference outputs.
5. Do not add fixture checksums. Supported-version metadata and the fixture are updated manually in
   the same change.
6. Update documentation when public behavior, support status, or an architectural boundary changes.

## Local validation

Run the complete validation suite before submitting a change:

```sh
just verify
```

Use `just --list` for focused formatting, checking, linting, testing, documentation, build, and run
commands.
