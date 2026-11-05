# Repository Instructions

## Required reading

- Read [`docs/architecture.md`](docs/architecture.md) before changing code. It is the canonical
  source for compatibility, crate ownership, API, filesystem, output, and test boundaries.
- Follow [`CONTRIBUTING.md`](CONTRIBUTING.md) for the change workflow and validation requirements.
- Check [`docs/roadmap.md`](docs/roadmap.md) before describing implementation status or adding a
  new product surface.

## Agent-specific rules

- Write repository code, documentation, comments, identifiers, diagnostics, and test names in
  English.
- The code of the reference implementation and the corresponding test data are pinned in the repository in `third-party/KUL-RSDA/AquaCrop`.
- The reference implementation is updated via `just pull-kul-rsda-aquacrop`.
- When working with the code, prioritize the repository specifications first, then the source code in `third-party/KUL-RSDA/AquaCrop`.
- All references to the remote test case should use `third-party/KUL-RSDA/AquaCrop/testcase`.
