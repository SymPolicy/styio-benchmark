# Native C++ Benchmark Route

This directory contains the native C++ comparison route for Styio.

The route compares the same workload across three execution routes:

- `full-cli`: the Styio executable reads the source, parses, lowers, performs
  LLVM/JIT work, and executes — compared against a C++20 program compiled with
  `-O3 -DNDEBUG` from the same source shape.
- `cached-jit`: reserved for a future reusable compiled/JIT artifact execution
  path. It is reported as `unsupported` until that CLI/runtime contract exists,
  so reports stay explicit instead of inventing numbers.
- `runtime-only`: compares native C++ against a C++ harness that calls Styio
  runtime helpers directly. It isolates helper overhead from frontend/JIT cost,
  but is not a substitute for generated Styio code execution.

## Workloads

1. `stdin_echo`: a stdin line iterator that echoes each line to stdout. It
   exercises per-line read/write overhead, buffer handling, and iteration
   cost, which is the shape that matters for the generator-style workloads
   this benchmark suite targets.

## Scales

Input size presets control the generated line count; an explicit
`--line-count` always overrides the preset:

- `--scale small`: 10,000 lines.
- `--scale medium`: 100,000 lines (default).
- `--scale large`: 1,000,000 lines.

Line width defaults to 48 bytes (`--line-bytes`). The runner validates each
implementation against the same generated input before timing begins.

## Run

Focused subprocess smoke:

```bash
native-cpp/run-native-cpp-bench.py \
  --styio-root /path/to/styio \
  --case stdin_echo \
  --routes full-cli \
  --scale small \
  --out-dir native-cpp/reports/<run-id>
```

Full comparison route (default `--routes all`):

```bash
native-cpp/run-native-cpp-bench.py \
  --styio-root /path/to/styio \
  --scale medium \
  --repeats 10 \
  --out-dir native-cpp/reports/<run-id>
```

The script resolves the Styio executable from the CMake build directory
(`build/default/bin/styio` by default, `--build-dir` / `--styio-exe` to
override) and refuses to run if it is missing. The C++ compiler defaults to
`CXX`, then `clang++`, then `c++` (`--cxx` to pin it). The C++ baseline is
built with `-O3 -DNDEBUG`; the Styio frontend/JIT route must be compared
against a Release build for fair numbers.

## Report contract

The report directory contains `results.json`, `benchmarks.csv`, and
`summary.md`; run metadata remains embedded in `results.json`.

Every successful record gains an additive `statistics` object with an
`elapsed_s` and a `throughput_mib_s` payload: `sample_count`, `median`, a
deterministic bootstrap 95% confidence interval (fixed local seed, 10000
median resamples), the coefficient of variation, and a quality marker
(`ok` / `insufficient_sample`). Each non-native record also gets a
`comparison` against the same-case, same-route `native_cpp` samples using a
two-sided Mann-Whitney U test (average ranks, tie correction, continuity
correction, alpha 0.05) with `significant` plus a direction-aware result; the
native record itself is marked as the baseline.
`unsupported` records carry `not_comparable` instead of fabricated numbers.

CSV columns are additive: legacy fields keep their names, and the new
`elapsed_*`, `throughput_*`, and `significance_*` columns are appended.
`summary.md` keeps the per-route table and appends `95% CI`, `CV`,
`Sig`, and `Direction` columns.

## Measurement boundary

Wall-clock benchmarks are only meaningful on controlled local machines. Run
`tools/benchmark-env-check.py` before local runs, compare stored baselines
with `tools/benchmark-compare.py`, and treat shared CI as contract-only: CI
byte-compiles, validates fixtures, and runs fast deterministic tests, but
never executes this runner.

Generated programs, native binaries, inputs, and validation outputs live
under the report directory (`programs/`, `bin/`, `inputs/`, `outputs/`) and
are deleted after validation unless `--keep-outputs` is passed.
