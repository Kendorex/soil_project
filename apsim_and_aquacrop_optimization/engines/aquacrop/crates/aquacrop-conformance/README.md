# aquacrop-conformance

Private cross-crate and official-testcase checks for the supported AquaCrop version.

The crate covers compatibility primitives, input parsers, individual process boundaries, daily
water and salt conservation, linked project runs, official daily values, aggregation behavior, and
the generated output set. It is not published.

## Testcase policy

`third-party/KUL-RSDA/AquaCrop/testcase/` is the single current fixture. It is replaced when the
subtree is updated to another supported AquaCrop version; versioned fixture directories, content
checksums, and generated metadata cross-checks are intentionally not used.

Tests must treat the fixture as immutable. Project and CLI conformance tests copy `DATA`, `LIST`,
`OBS`, `PARAM`, and `SIMUL` into a temporary directory, create a fresh `OUTP`, execute there, and
compare the generated filenames and contents with `OUTP_REF`. Status files are compared exactly;
for timestamped reports only the first creation line is excluded.
