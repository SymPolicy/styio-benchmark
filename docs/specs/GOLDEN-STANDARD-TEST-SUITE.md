# Golden Standard Test Suite

**Purpose:** Define the styio-benchmark test level that makes benchmark harness changes submittable.

`test / smoke` byte-compiles the benchmark runners, tools, and capability test
sources.

`test / golden-standard` installs the test extra and invokes the repository
golden gate (`scripts/benchmark-golden-gate.py`). The gate:

- byte-compiles every Python source in `async-runtime/`, `native-cpp/`, `tools/`, `tests/`, and `scripts/`;
- validates historical report fixtures for readability;
- validates the deterministic capability fixtures under `tests/fixtures/benchmark-capabilities/` against the additive statistical schema;
- asserts production dependencies remain empty;
- inspects the workflow to prove no wall-clock benchmark job runs on shared CI;
- runs the fast capability test modules (`async-runtime/test_async_runtime_statistics.py`, `native-cpp/test_native_cpp_bench.py`, `tools/test_benchmark_tools.py`, `tests/test_benchmark_capability_contract.py`);
- smokes both new tool CLIs (`tools/benchmark-env-check.py`, `tools/benchmark-compare.py`);
- runs the async runtime black-box pytest contract, which must skip explicitly rather than silently pass when no Styio checkout is available.

No job on shared CI executes a real wall-clock benchmark. Real measurement
belongs on controlled local machines, with `tools/benchmark-env-check.py` used
as a preflight check before local runs.

## Local Gate Profile

`styio-benchmark-async-blackbox-profile` is the repository-owned adaptation for benchmark harness integrity. It is maintained in this repository through the async runtime black-box contract, pytest execution, and explicit skip behavior when an external Styio checkout is unavailable. The organization-level audit only verifies that this local profile is present and covered by `test / golden-standard`.

Required local markers: repo-owned adaptation, async runtime black-box, pytest, explicit skip, capability fixtures, additive statistics schema, empty dependency list.

## Industry Gate Group

`benchmark / measurement-integrity` is the role-specific gate group for benchmark harness changes. It keeps black-box validation, pytest execution, byte-compile checks, explicit skip behavior, and performance baseline evidence grouped under `test / golden-standard`.

Required evidence markers: black-box, pytest, byte-compile, explicit skip, performance baseline, additive statistics schema, tool smoke, no shared-CI wall-clock benchmark.

## Submit Readiness

A styio-benchmark version is submittable only when `test / smoke` and `test / golden-standard` both pass.
