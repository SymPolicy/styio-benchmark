# Styio Benchmark

**Black-box benchmark and performance evidence routes for Styio.**
Part of the [Styio](https://styio.io) ecosystem.

[![License](https://img.shields.io/github/license/SymPolicy/styio-benchmark?style=flat-square)](LICENSE)

---

This repository keeps benchmark orchestration outside the Styio compiler source
tree. Runners treat Styio as an external checkout selected by `--styio-root` or
`STYIO_ROOT`.

## Layout

| Directory | Purpose |
|---|---|
| `async-runtime/` | Cross-runtime async scheduler comparison (Styio, C++20, Go, Rust Tokio) |
| `native-cpp/` | Styio vs hand-written native C++ black-box comparison routes |
| `styio-probes/` | Migrated C++ probe sources compiled by a matching Styio checkout |
| `tools/` | Shell and Python route helpers |
| `tests/` | Fast capability contract tests and deterministic report fixtures |
| `reports/` | Curated historical benchmark reports |
| `regressions/` | Minimized regression artifact area |
| `docs/` | Benchmark coverage, regression templates, migration notes |

## Quick Start

Run the async runtime smoke contract:

```bash
STYIO_ROOT=/path/to/styio \
python3 -m pytest async-runtime/test_async_runtime_blackbox.py
```

Run a focused subprocess smoke without pytest:

```bash
async-runtime/run-async-bench.py \
  --styio-root /path/to/styio \
  --case smoke \
  --runtime styio --runtime cpp \
  --require-runtime styio --require-runtime cpp \
  --out-dir async-runtime/reports/<run-id>
```

Run the baseline comparison:

```bash
async-runtime/run-async-bench.py \
  --styio-root /path/to/styio \
  --case baseline \
  --bootstrap-toolchains \
  --scale medium \
  --repeats 10 \
  --out-dir async-runtime/reports/<run-id>
```

Run native C++ comparison routes:

```bash
native-cpp/run-native-cpp-bench.py \
  --styio-root /path/to/styio \
  --routes all \
  --scale medium \
  --repeats 10 \
  --out-dir reports/native-cpp-stdin-echo
```

Check whether this machine is suitable for controlled local wall-clock
benchmarks (read-only, advisory):

```bash
tools/benchmark-env-check.py --format text
```

Compare a stored baseline report against a new head report:

```bash
tools/benchmark-compare.py \
  --baseline reports/<baseline-run>/results.json \
  --candidate reports/<head-run>/results.json \
  --label head \
  --out-md reports/<baseline-run>/comparison.md
```

## Measurement Discipline

Wall-clock benchmarks are only meaningful on controlled local machines. Shared
CI never executes a real benchmark job; CI only runs contract tests, the golden
gate, byte-compilation, and deterministic fixtures.

- Both runners (`async-runtime/run-async-bench.py` and
  `native-cpp/run-native-cpp-bench.py`) report per-core-metric medians with a
  deterministic bootstrap 95% confidence interval, coefficient of variation,
  and two-sided Mann-Whitney U significance against the matching C++ baseline.
- Baseline runs default to ten repeats; `small`, `medium`, and `large` scale
  presets are available in both runners, and explicit sizing and repeat flags
  always override presets. The async smoke case keeps its existing sizes and a
  one-repeat default for fast contract checks.
- `tools/benchmark-env-check.py` reports whether a machine is suitable for
  controlled local benchmarking (CPU availability, affinity, background load,
  frequency governor, turbo/boost, power source). It is read-only and never
  exposes host identity, paths, environment values, or secrets.
- `tools/benchmark-compare.py` compares a stored baseline report with a
  candidate report, reports direction-aware median change with Mann-Whitney U
  evidence, and classifies each metric as regression, improvement,
  inconclusive, insufficient-sample, or incompatible-input.
  `--fail-on-regression` makes it usable as a local gate.

Report JSON fields are additive: every existing field remains, and new
`statistics` and `comparison` objects carry the uncertainty and significance
data. See `docs/IN_TREE_BENCHMARKS.md` and `docs/COVERAGE-MATRIX.md` for the
coverage matrix, and `docs/specs/GOLDEN-STANDARD-TEST-SUITE.md` for the
contract-only CI route.

## Boundary

Styio provides compiled benchmark target names such as
`styio_task_scheduler_perf_test` and `styio_soak_test`, but their migrated
source files live under `styio-probes/` in this repository. This repository
owns the performance workloads, runners, report contract, baselines, native
C++ harnesses, cross-runtime harness, and probe source inventory. Do not add
new benchmark runners or stored performance reports to the Styio source tree.

## License

Apache-2.0. See [LICENSE](LICENSE).
