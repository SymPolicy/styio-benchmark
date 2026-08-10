# styio-benchmark

Black-box benchmark and performance evidence routes for Styio.

This repository intentionally keeps benchmark orchestration outside the Styio
compiler source tree. Runners treat Styio as an external checkout selected by
`--styio-root` or `STYIO_ROOT`. The only source-level coupling allowed here is
`styio-probes/`, which stores migrated C++ probe sources that are compiled by a
matching Styio checkout; other benchmark routes should stay black-box.

## Layout

- `async-runtime/`: cross-runtime async scheduler comparison for Styio, C++20
  stackless coroutine, Go goroutine, and Rust Tokio.
- `native-cpp/`: Styio vs hand-written native C++ black-box comparison routes.
- `styio-probes/`: migrated C++ probe sources for Styio-owned benchmark target
  names.
- `workloads/core/`: deterministic core Styio workloads and their JSON runner.
- `tools/`: shell and Python route helpers migrated from the Styio in-tree
  benchmark surface.
- `reports/`: curated historical benchmark reports.
- `regressions/`: minimized regression artifact area.
- `docs/`: benchmark coverage, regression templates, and migration notes.

## Quick Start

Run the async runtime smoke contract against a Styio checkout:

```bash
STYIO_ROOT=/path/to/styio \
python3 -m pytest async-runtime/test_async_runtime_blackbox.py
```

Run a focused subprocess smoke without pytest:

```bash
async-runtime/run-async-bench.py \
  --styio-root /path/to/styio \
  --case smoke \
  --runtime styio \
  --runtime cpp \
  --require-runtime styio \
  --require-runtime cpp \
  --out-dir async-runtime/reports/<run-id>
```

Run the baseline comparison:

```bash
async-runtime/run-async-bench.py \
  --styio-root /path/to/styio \
  --case baseline \
  --bootstrap-toolchains \
  --repeats 5 \
  --out-dir async-runtime/reports/<run-id>
```

Run the canonical Styio/C++ parity evidence route:

```bash
python3 tools/parity_gate.py run \
  --contract workloads/parity-v1/contract.json \
  --styio-root /path/to/styio \
  --build-dir /path/to/styio/build/perf-parity \
  --out-dir reports/perf-parity/run \
  --sizes small --warmups 3 --repetitions 11
```

The runner measures only the three frozen catalog routes: fresh optimized
native build plus execution for compile-and-run, fresh native build, and
execution of artifacts built outside the timed region. Short cells use one
equal calibrated batch count for both implementations and retain normalized
samples (the faster side sets a 500 ms minimum-time floor). With eleven
repetitions, bounded whole-cell retries select the first complete attempt below
the fixed 5% CV gate and preserve every rejected attempt as privacy-safe audit
evidence; no sample is discarded. It validates both outputs against the
independent catalog digest before retaining paired samples. `run` writes
evidence only; parity thresholds are applied separately:

```bash
python3 tools/parity_gate.py verify \
  --contract workloads/parity-v1/contract.json \
  --report reports/perf-parity/run/results.json \
  --mode final --privacy strict --require-all
```

Reports contain stable workload, toolchain-version, sample, RSS, phase
provenance, calibration, focus-budget, and statistic fields only. Paths,
commands, host identity, environment values, URLs, and raw subprocess text are
rejected recursively before serialization. The phase sweep uses one isolated
probe pass and one Clang time trace per sample/tier, shared by all five phase
cells.

Run the benchmark-owned core corpus through a Styio compiler:

```bash
python3 workloads/core/run-core.py \
  --styio /path/to/styio \
  --iterations 3 \
  --output reports/core/local.json
```

To compile the C++ probes against a Styio checkout, configure that checkout with
an explicit `-DSTYIO_BENCHMARK_ROOT=/path/to/styio-benchmark`. Standalone Styio
builds do not discover this repository implicitly.

## Boundary

Styio still provides compiled benchmark target names such as
`styio_task_scheduler_perf_test` and `styio_soak_test`, but their migrated source
files live under `styio-probes/` in this repository. This repository owns the
performance workloads, runners, report contract, baselines, native C++ harnesses,
cross-runtime harness, and probe source inventory. Treat Styio as a tested
checkout selected with `--styio-root`; do not add new benchmark runners or stored
performance reports to the Styio source tree.
