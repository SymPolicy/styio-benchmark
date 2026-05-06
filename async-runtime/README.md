# Async Runtime Benchmark Framework

This directory contains the cross-runtime async benchmark route for the Styio task scheduler.

The route compares the same two workloads across:

- `styio_task_scheduler`: the repository runtime target.
- `cpp_stackless_coroutine`: a C++20 stackless coroutine scheduler built with coroutine frames and `co_await`.
- `goroutine`: Go goroutines with `GOMAXPROCS` pinned to the worker count.
- `tokio_multi_thread`: Rust Tokio's multi-thread runtime.

## Workloads

1. `sleep`: four `160ms` blocked tasks. This proves whether a runtime overlaps blocked work. A speedup near the worker count means the implementation is truly scheduled rather than eager.
2. `noop`: one hundred thousand trivial tasks. This exposes submit, wake, wait, and release overhead while reducing single-run timer noise.

## Framework Shape

The async route follows the black-box structure used for multi-language parity testing:

- `pytest` owns the lightweight test contract in `test_async_runtime_blackbox.py`.
- `run-async-bench.py` owns process startup, runtime selection, repeated samples, and report generation.
- Each implementation is treated as an external runtime selected by `--runtime`; the pytest smoke path requires only `styio` and `cpp` so it does not need Go/Rust toolchain bootstrap.
- The report contract is JSON/CSV/Markdown, so deeper tooling can consume it without depending on GoogleTest, `go test`, or `cargo test` as the unified entrypoint.

Testcontainers, Docker Compose, k6, and ghz are reserved for future service-shaped async benchmarks. This route is a local scheduler benchmark, so subprocess black-box execution is the right boundary today.

## Run

Smoke contract:

```bash
STYIO_ROOT=/path/to/styio \
python3 -m pytest async-runtime/test_async_runtime_blackbox.py
```

Focused subprocess smoke without pytest:

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

Full baseline comparison:

```bash
async-runtime/run-async-bench.py \
  --styio-root /path/to/styio \
  --case baseline \
  --bootstrap-toolchains \
  --repeats 5 \
  --out-dir async-runtime/reports/<run-id>
```

The script uses `build/async-runtime-release` under the selected Styio checkout by default and configures it as a CMake `Release` build when needed. It refuses to use a non-Release Styio build directory for runtime comparisons because the C++ baseline is built with `-O3`. Styio builds and the generated C++ stackless coroutine baseline prefer Clang, which is the standard compiler for Styio.

`--bootstrap-toolchains` installs missing Go and Rust toolchains under the benchmark build directory; that directory is a local build artifact and is not tracked.

Presets:

- `--case smoke`: small `styio`/`cpp` contract route suitable for pytest.
- `--case baseline`: default comparable report across the selected runtimes.
- `--case stress`: larger fanout route for scheduler regression investigation.
- `--case custom`: use explicit `--tasks`, `--sleep-ms`, `--noop-tasks`, `--workers`, and `--repeats` values.

The report directory contains:

- `metadata.json`
- `results.json`
- `benchmarks.csv`
- `summary.md`

`summary.md` includes normalized `Sleep perf` and `Noop perf` columns. Each workload is normalized independently: the best runtime is `1.00x`, and the others show their relative score against that best result. It also includes a C++ stackless coroutine parity section for Styio. The default route runs each runtime five times and reports medians; `results.json` keeps the successful samples for debugging noisy microbenchmarks.

The generated temporary C++/Go/Rust sources and binaries live under `<styio-root>/build/async-runtime-release/async-runtime-work/<run-id>`, not inside the report.
