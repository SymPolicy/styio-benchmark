# Styio standard parity methodology

This document defines the auditable Styio/C++ performance comparison. The
machine-readable workload authority is
[`workloads/parity-v2/contract.json`](../workloads/parity-v2/contract.json),
and the report implementation is
[`tools/standard_parity_gate.py`](../tools/standard_parity_gate.py).

The current catalog-check digest is
`ffda1fd9dadb6493c3a722302b7abd823a402ce956f4ccbad6b45c54bdda1e6c`.
Report schema v3 is intentionally incompatible with earlier evidence: a v2
report cannot be merged or used for a current parity claim.

## What is standard—and what is not

The measurement procedure adopts established practices from:

- [Google Benchmark](https://google.github.io/benchmark/user_guide.html):
  minimum measured duration, warm-up, repetitions, and interleaved execution;
- [SPEC CPU 2017 run rules](https://www.spec.org/cpu2017/docs/runrules.html):
  frozen workloads, documented run conditions, reproducibility, validation,
  and fail-closed reporting;
- [LLVM test-suite/LNT](https://llvm.org/docs/TestSuiteGuide.html): separate
  compile, execute, output-validation, and result-reporting dimensions; and
- [Computer Language Benchmarks Game measurement rules](https://benchmarksgame-team.pages.debian.net/benchmarksgame/how-programs-are-measured.html):
  repeated measurements on an otherwise idle system and explicit uncertainty.

This is a standards-derived project suite, not a SPEC submission, SPEC score,
CLBG implementation set, or execution of the official LLVM test-suite. No
official suite program or input is copied into this repository. All eleven
programs are independent project microkernels. The retained `clbg-*` and
`llvm-*` identifiers are historical style/lineage labels only; the catalog
records that limitation explicitly and gives each kernel an algorithm ID that
describes the code it actually executes.

## Frozen workload contract

The catalog contains eleven independently implemented Styio/C++ families and
five compiler-phase diagnostics. Every workload freezes:

- separate Styio and C++ source digests;
- the same deterministic input bytes and static work-unit count;
- an independent canonical output oracle;
- algorithm identity, one-thread policy, and route boundary; and
- `smoke`, `development`, and `reference` scales without relabelling reduced
  inputs as reference evidence.

C++ uses C++20 with `-O3 -DNDEBUG -fno-lto`. The strength audit rejects weak
optimization, LTO, threaded baselines, and avoidable synchronous stream modes.
Correctness is validated before any measurement is retained.

## Measurement protocol

Each workload route follows one procedure:

1. Build both implementations and validate exact output digests outside the
   timed region.
2. Calibrate one *equal* batch count for Styio and C++. The faster side must
   reach a 0.5 second median retained interval; calibration targets 0.75 seconds
   and is capped at 20,000 operations.
3. Execute three warm-up batches and eleven retained paired batches. Pair order
   is balanced and reproducibly randomized to avoid always giving either side
   the first or second position.
4. Retain every sample. There is no fastest-run selection, outlier deletion,
   or retry-until-pass policy.
5. Measure time without an RSS observer. Measure peak process-tree RSS in a
   separate equivalent one-operation replay so memory sampling cannot perturb
   timing.
6. Report per-operation normalized samples *and* raw batch totals. The verifier
   recomputes ratios and checks the normalization rather than trusting summary
   fields.

The three boundaries remain separate:

- `compile-and-run`: each operation performs a fresh optimized build and then
  executes the resulting artifact;
- `native-build`: each operation performs a fresh optimized build only; and
- `native-run`: each operation executes an artifact built outside timing.

## Statistics and score policy

For each cell, the gate computes paired Styio/C++ log-ratios, their geometric
mean, CV for each implementation, and a paired 95% percentile-bootstrap
confidence interval. Route-level confidence intervals use a hierarchical
bootstrap: workload cells are equal-weight, then retained pairs are resampled
within each selected cell. Every interval uses 10,000 deterministic resamples.

The gate never produces one mixed score across unrelated measurements:

- scales are separated;
- `compile-and-run`, `native-build`, and `native-run` are separated;
- `native-run` is the primary native-performance claim;
- compiler-phase cells are diagnostic attribution only; and
- capability-blocked cases are disclosed but excluded from speed scores.

Strict reference evidence must satisfy all of these conditions:

- all 38 required reference records exist and pass exact correctness;
- each workload cell has 3 warm-ups, 11 retained samples, a valid 0.5-second
  batch floor, balanced interleaving, and CV no greater than 5%;
- every time ratio is at most 1.10;
- every route's time geometric mean **and the upper bound of its 95% confidence
  interval** are at most 1.05;
- every peak-RSS ratio is at most 1.15; and
- every route's peak-RSS geometric mean and upper confidence bound are at most
  1.10.

The five current capability exclusions—bit-packed, byte-buffer, object-node,
regular-expression, and arbitrary-precision—remain visible in every report.
They cannot silently improve the aggregate by being omitted.

## Controlled reference runs

Development is the default run class. A strict reference claim additionally
requires `--run-class controlled`, which is an explicit operator attestation
that the run used a dedicated idle system, fixed compiler/build configuration,
stable power mode, and no concurrent benchmark jobs. Reports disclose only the
generic control class; host names, user names, absolute paths, commands,
endpoints, credentials, and raw subprocess text are rejected recursively.

Shared hosted CI runners execute the catalog, baseline-strength, privacy, and
statistical self-tests only. They do not create official timing evidence.

## Commands

Validate the contract and the C++ baseline:

```bash
python3 tools/standard_parity_gate.py catalog-check \
  --contract workloads/parity-v2/contract.json
python3 tools/standard_parity_gate.py cpp-strength \
  --contract workloads/parity-v2/contract.json
```

Run one development shard:

```bash
python3 tools/standard_parity_gate.py run \
  --contract workloads/parity-v2/contract.json \
  --family clbg-fannkuch-redux --scale smoke \
  --styio-root ../styio-nightly --build-dir ../styio-nightly/build/perf-parity \
  --out-dir reports/standard-parity/shards/clbg-fannkuch-redux
```

For official evidence, run every family and the compiler-phase shard at the
reference scale under controlled conditions, adding `--run-class controlled`
to each command. Then merge the disjoint shards and apply the strict gate:

```bash
python3 tools/standard_parity_gate.py merge \
  --contract workloads/parity-v2/contract.json \
  --reports-dir reports/standard-parity/shards \
  --out-dir reports/standard-parity/final

python3 tools/standard_parity_gate.py verify \
  --contract workloads/parity-v2/contract.json \
  --report reports/standard-parity/final/results.json \
  --require-all --privacy strict
```

An official claim is valid only when the final verifier returns `decision:
pass`. A smoke/development result, an uncontrolled reference run, a legacy
schema, or a report with missing capabilities is descriptive evidence only.
