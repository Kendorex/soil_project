# aquacrop-cli

Compatibility-oriented command-line interface for the Rust AquaCrop implementation. The package
builds an executable named `aquacrop` and always enables `aquacrop-core/file-io`.

With no arguments, the executable runs projects in the current directory:

```sh
aquacrop
```

Use `--root` (or `-C`) without changing the process working directory:

```sh
aquacrop --root /path/to/project
```

The project root uses the standard `LIST`, `DATA`, `PARAM`, `SIMUL`, `OBS`, and `OUTP`
directories. Projects are read from `LIST/ListProjects.txt`; if that file is absent, `.PRO` and
`.PRM` files directly under `LIST` are discovered in filename order.

The executable writes seasonal output for each project and the daily, harvest, and evaluation
reports selected by the supported configuration files, followed by `ListProjectsLoaded.OUT` and
`AllDone.OUT`. Result reports use buffered direct file output.

Executable integration tests copy the current testcase inputs to a temporary directory, run the
binary there, and compare the generated file set and report bodies with `OUTP_REF`. Compatibility
is established for that implemented path; see the workspace development status for remaining file
and output combinations.

`aquacrop --help` reports the AquaCrop model version supplied by `aquacrop-core`.
