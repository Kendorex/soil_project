# Portable management starting reference

`management_reference.json` retains four starting management policies, the
native management decision domain, economics and runtime settings. It includes
the exact source-model/weather hashes and the historical origin identifier.
It contains no archived simulated objective scores or candidate databases.

This replaces the runtime dependency on a completed `runs/apsim/...` directory.
Future outcomes must always be calculated afresh. The policies are starting
points, not validated future recommendations. Existing exported calibration
inputs and the configured 0.1 ha operational override are unchanged.
