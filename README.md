# styio-benchmark

Black-box benchmark and performance evidence routes for Styio.

This repository intentionally keeps benchmark orchestration outside the Styio
compiler source tree. Runners treat Styio as an external checkout selected by
`--styio-root` or `STYIO_ROOT`; benchmark code here should not include Styio
private C++ headers or link against internal CMake targets directly.

## Layout

- `async-runtime/`: cross-runtime async scheduler comparison for Styio, C++20
  stackless coroutine, Go goroutine, and Rust Tokio.
- `native-cpp/`: Styio vs hand-written native C++ black-box comparison routes.
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

Run the native C++ standard comparison routes:

```bash
native-cpp/run-native-cpp-bench.py \
  --styio-root /path/to/styio \
  --routes all \
  --line-count 100000 \
  --line-bytes 48 \
  --repeats 5 \
  --out-dir reports/native-cpp-stdin-echo
```

`full-cli` measures Styio CLI wall-clock cost against a hand-written C++
binary. `cached-jit` is reported as `unsupported` until Styio exposes a real
reusable compiled/JIT artifact execution entrypoint. `runtime-only` uses Styio's
exported C runtime ABI from the selected Styio build to isolate helper overhead
without storing the benchmark in the Styio source tree.

## Boundary

Styio may still provide compiled benchmark probes such as
`styio_task_scheduler_perf_test` and `styio_soak_test`. This repository owns the
performance workloads, runners, report contract, baselines, native C++
harnesses, and cross-runtime harness. Treat Styio as a tested checkout selected
with `--styio-root`; do not add new benchmark runners or stored performance
reports to the Styio source tree. That split keeps the benchmark route portable
while avoiding private source-level coupling.
