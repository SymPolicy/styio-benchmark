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

## Analyzer implementation documents

Styio Analyzer v1 compares two Styio builds on the existing parity-v2 corpus.
The CLI is `tools/styio_analyzer.py`. LNT history, broader corpora, and
optimization diagnostics remain later deliveries.

- [System design](docs/STYIO-ANALYZER-DESIGN.md)
- [v1 behavior specification](docs/STYIO-ANALYZER-V1.md)
- [Cursor implementation and independent acceptance plan](docs/STYIO-ANALYZER-IMPLEMENTATION-PLAN.md)
- [Cursor starting prompt](docs/prompts/styio-analyzer-v1.cursor.md)

```bash
python3 tools/styio_analyzer.py compare \
  --baseline-root /path/to/baseline \
  --baseline-build-dir /path/to/baseline/build \
  --candidate-root /path/to/candidate \
  --candidate-build-dir /path/to/candidate/build \
  --contract workloads/parity-v2/contract.json \
  --family llvm-scalar-chain \
  --scale smoke \
  --out-dir reports/analyzer/example

python3 tools/styio_analyzer.py verify \
  --contract workloads/parity-v2/contract.json \
  --report reports/analyzer/example/results.json
```

`compare` exits 0 when selected cells were executed and required evidence is
valid. A performance regression or no-difference result does not change that
exit code. `verify` exits 0 when the report is consistent and coverage is
disclosed; that is not performance acceptance of the candidate.

Before timing, Analyzer observes each compiler's actual runtime source selection
with a temporary CXX probe. It retains the CXX invocation name (including
`clang++` symlinks), records the driver/target and public build configuration,
and reports unconfirmed or different conditions as incomparable. Probe calls
are excluded from all performance samples.

Interrupted cells retain completed pairs and any unfinished pair's raw and
normalized values. `verify` checks these values even when fewer samples than
requested were collected. Pair-order metadata describes the completed prefix;
partial metrics do not publish aggregate statistics.

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
python3 tools/standard_parity_gate.py run \
  --contract workloads/parity-v2/contract.json \
  --family clbg-fannkuch-redux --scale smoke \
  --styio-root /path/to/styio \
  --build-dir /path/to/styio/build \
  --out-dir reports/standard-parity/shards/clbg-fannkuch-redux
```

The standard runner validates exact outputs, calibrates one equal-work batch
for both implementations to a 500 ms minimum retained interval, performs three
warm-ups and eleven retained reproducibly interleaved pairs, and keeps every
raw and normalized sample. Time is observer-free; process-tree RSS comes from
an isolated replay. Results carry paired 95% bootstrap intervals and are scored
separately by scale and by `compile-and-run`, `native-build`, and `native-run`.
Compiler-phase records remain diagnostic and never enter the primary
`native-run` claim. Full rules are in
[`docs/STANDARD-PARITY.md`](docs/STANDARD-PARITY.md).

After running all reference shards with the explicit controlled-run
attestation and merging them, apply the strict gate:

```bash
python3 tools/standard_parity_gate.py verify \
  --contract workloads/parity-v2/contract.json \
  --report reports/standard-parity/final/results.json \
  --privacy strict --require-all
```

Strict reference timing is intentionally not run on shared hosted CI machines;
CI validates the frozen catalog, C++ baseline strength, privacy rules,
statistics, aggregation, and fail-closed verifier.

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
