# Async Runtime Benchmark Report

- Run ID: `20260506T022543Z-async-runtime`
- Host: `linux-aarch64`
- Case: `baseline` (median performance comparison against C++ stackless coroutine, goroutine, and Tokio)
- Harness: `pytest-compatible black-box runner` over `subprocess`
- Runtimes: `styio, cpp, go, rust`
- Required runtimes: `none`
- Tasks: `4` sleep tasks x `160ms`, `100000` no-op tasks, `4` workers/procs
- Repeats: `5` per runtime, reported values are medians

| Language | Runtime | Status | Samples | Sleep seq ms | Sleep parallel ms | Speedup | Sleep perf | Noop total us | Noop us/task | Noop perf | Toolchain / reason |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| styio | styio_task_scheduler | ok | 5 | 652.000 | 165.000 | 3.952 | 0.98x | 56860.000 | 0.569 | 0.57x | repo runtime target (Release; clang++-18) |
| cpp | cpp_stackless_coroutine | ok | 5 | 649.000 | 161.000 | 4.031 | 1.00x | 56963.000 | 0.570 | 0.57x | Debian clang version 18.1.8 (18+b1) |
| go | goroutine | ok | 5 | 647.000 | 161.000 | 4.019 | 1.00x | 34107.000 | 0.341 | 0.95x | go version go1.26.2 linux/arm64 |
| rust | tokio_multi_thread | ok | 5 | 649.000 | 164.000 | 3.957 | 0.98x | 32552.000 | 0.326 | 1.00x | rustc 1.95.0 (59807616e 2026-04-14); cargo 1.95.0 (f2d3ce0bd 2026-03-21); tokio 1.52.2 |

## Interpretation

- `sleep` measures whether the runtime actually overlaps blocked tasks; speedup near the worker count indicates real scheduling instead of eager evaluation.
- `noop` measures submit/wait/release overhead for a large fanout of trivial tasks.
- `Samples` records successful repeats; all table metrics are median values to avoid single-run microbenchmark noise.
- `Sleep perf` and `Noop perf` normalize each workload independently; the best runtime is `1.00x`, and the others show their relative performance against that best result.
- The runner is intentionally pytest-compatible: each runtime is a subprocess black box, and pytest can assert the generated JSON/CSV contract without embedding language-specific unit-test frameworks.
- C++ uses C++20 stackless coroutine frames with `co_await` and a small scheduler built with Clang by default, Go uses goroutines with `GOMAXPROCS`, Rust uses Tokio's multi-thread runtime, and Styio uses the repository task scheduler target.
- `--bootstrap-toolchains` installs missing Go/Rust toolchains under the build directory; this keeps comparison runs reproducible on machines without system Go or Rust.

## C++ Stackless Parity

- Styio no-op vs C++ stackless coroutine: `1.00x` (`0.569` us/task vs `0.570` us/task).
- Styio sleep overlap vs C++ stackless coroutine: `0.98x` (`3.952` speedup vs `4.031`).
