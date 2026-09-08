# Styio benchmark routes

This repository owns Styio benchmark orchestration, frozen workloads, reports,
and regression artifacts. A tested Styio checkout is always supplied explicitly
with `--styio-root` or `STYIO_ROOT`; benchmark code does not implicitly discover
or modify the compiler source tree.

## Canonical entrypoints

- `tools/standard_parity_gate.py` validates, runs, merges, and verifies the
  frozen Styio/C++ parity catalog.
- `tools/styio_analyzer.py` compares two Styio builds on that same catalog
  without changing the Styio/C++ parity contract.
- `tools/perf-route.sh` is a thin one-shard wrapper around that same gate and
  defines no independent timing or scoring logic.
- `styio-probes/` contains the C++ sources for compiler-owned benchmark target
  names such as `styio_soak_test` and `styio_task_scheduler_perf_test`.
- `async-runtime/` owns the separate Styio/C++/Go/Rust scheduler comparison.
- `tools/parser-shadow-suite-gate.sh` owns parser shadow checks.
- `tools/soak-minimize.sh` minimizes soak failures.

The complete module and pipeline coverage is listed in
[`COVERAGE-MATRIX.md`](COVERAGE-MATRIX.md). The authoritative parity procedure,
statistics, thresholds, and controlled-run policy are in
[`STANDARD-PARITY.md`](STANDARD-PARITY.md).

## Styio/C++ parity shard

Run a bounded development shard:

```bash
tools/perf-route.sh \
  --family clbg-fannkuch-redux \
  --scale smoke \
  --styio-root /path/to/styio \
  --build-dir /path/to/styio/build \
  --out-dir reports/standard-parity/shards/clbg-fannkuch-redux \
  --verify
```

The catalog exposes only three routes:

- `compile-and-run`: a fresh optimized build followed by execution;
- `native-build`: a fresh optimized native build; and
- `native-run`: execution of an artifact built outside the timed region.

Every workload shard contains all three routes. Route aliases or fallback
boundaries are rejected. Official reference evidence additionally requires
`--scale reference --run-class controlled`, all eleven workload families, the
compiler-phase shard, a disjoint merge, and strict verification.

## Compiler probes

The Styio build registers benchmark probe targets only when it is configured
with an explicit benchmark repository:

```bash
cmake -S /path/to/styio -B /path/to/styio/build \
  -DSTYIO_BENCHMARK_ROOT=/path/to/styio-benchmark \
  -DSTYIO_REQUIRE_EXTERNAL_BENCHMARK=ON
```

The phase sweep uses one isolated `styio_soak_test` probe per retained sample
and a matching Clang `-ftime-trace` compilation. Tokenize, parse, semantic
analysis, lowering, and LLVM emission records are attribution diagnostics; they
never enter the `native-run` performance score.

## Report boundary

Reports may contain stable workload IDs, source/input/output digests, public
toolchain versions, raw numeric samples, aggregate statistics, capability
coverage, and stable reason codes. The serializer rejects machine and user
identity, absolute paths, commands, environment values, endpoints, secrets,
and raw child-process output.
