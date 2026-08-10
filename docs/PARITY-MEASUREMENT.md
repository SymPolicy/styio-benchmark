# Parity measurement contract

`tools/parity_gate.py` is the sole evidence runner for the frozen
`workloads/parity-v1/contract.json` catalog. The catalog, not a command-line
alias or an ad-hoc workload, determines what can be measured.

## Route boundaries

| Route | Timed region | Setup and correctness |
| --- | --- | --- |
| `compile-and-run` | Fresh source compilation plus execution | A clean process and output are used for every sample; output is validated before sampling. |
| `native-build` | Fresh source-to-native-artifact build | Artifacts are disposable per sample; a separate artifact is validated before sampling. |
| `native-run` | Execution only | Both artifacts are built and validated before warmups and measured runs. |

Styio uses the existing `styio build <source> -o <artifact>` contract. Both
compile-and-run sides perform that fresh optimized native build inside every
timed sample and then execute the resulting artifact. C++ is
compiled with the CMake-recorded Clang compiler, C++20, `-O3`, and `-fno-lto`.
The runner requires a Release CMake cache and records only public compiler
versions and a coarse target class.

## Sampling and statistics

The default budget is three warmups and eleven measured pairs. A measured pair
contains one Styio and one C++ sample; pair order alternates `AB`, `BA`, `AB`,
and so on. Warmups are discarded, measured samples are never discarded, and a
missing or failed sample remains an incomplete cell rather than being replaced
with a faster rerun.

For short cells the runner first performs an untimed calibration. If one
operation is below the 500 ms minimum-time floor, both implementations execute
the same bounded batch count (derived from the faster side) and each retained
elapsed value is divided by that count; the count is recorded in `batch_count`
and no sample is discarded. With the default eleven repetitions, a noisy whole
cell may be remeasured at most twice with larger equal batches. The first
complete attempt whose two time CVs are at most 5% is selected in execution
order; every rejected attempt's raw normalized samples and stable reason remain
under `stability_attempts`. Shared phase evidence retries the entire tier, never
one phase ID in isolation. Every invocation receives the declared timeout,
including diagnostics and phase probes; timeout evidence is the stable
`timed_out` reason.

For each implementation and cell the report retains every raw normalized execution time,
process-tree peak RSS sample, median, minimum, maximum, and sample coefficient
of variation. Throughput is derived from the validated input byte count. The
paired log ratio for a lower-is-better metric is `log(Styio / C++)`; throughput
uses the reciprocal ratio. Equal-weight geometric means are reported globally,
by route, and by workload family.

Peak RSS is collected by the native-platform helper in
`native-cpp/process_tree_rss.py`. A separate sampler tracks the process tree
while the parent blocks in one wait, so polling cadence cannot quantize elapsed
time. RSS state is isolated per invocation (procfs sampling on Linux and a
per-sample native resource delta elsewhere); cumulative child maxima are never
added to a later sample. The helper keeps child output private and returns only
numeric elapsed-time and RSS values to the runner.

Compiler phase cells are sourced from one isolated `styio_soak_test` probe pass
per retained sample and tier. The pass emits tokenize, parse,
semantic-analysis, lowering, and LLVM-emission boundaries together, so phase
cells do not duplicate full compilation. The equivalent generated C++ source is
compiled once per sample with Clang `-ftime-trace`; frontend, optimizer, and
codegen events are mapped to the same five buckets. Reports retain public phase
provenance and corrected language-specific source digests.

`focus_budgets` records deterministic non-overlapping log-ratio savings derived
from route fixed-cost shares and measured phase gaps. Their allocation sum is
the required closure to the 1.05 target. Focused verification fails closed if
the budget is missing or its sum is inconsistent.

## Evidence versus decisions

`run` always writes `results.json` with `status: evidence` and
`verification: not-evaluated`; it never claims parity. `verify` consumes that
report and applies independent checks for catalog digest, required cells,
correctness digests, balanced pairing, sample retention, finite statistics,
noise, strict privacy, and optional thresholds.

Verification modes are:

- `smoke`: completeness and correctness for a selected small run; no parity
  threshold is implied.
- `baseline`: complete evidence before optimization; an initial performance
  gap is expected and is not treated as a parity pass.
- `focused-budget`: compare a focused report with a supplied baseline and
  enforce explicit phase/growth budgets.
- `final`: enforce the default 1.05 equal-weight geometric-mean and 1.10
  per-cell lower-is-better ratio budgets (or caller-supplied limits).

Use `--privacy strict` for all promoted evidence. The recursive serializer
rejects path, host/user/machine, environment, command, endpoint, secret, and
unsanitized subprocess fields before JSON or Markdown is written. Stable reason
codes are retained; compiler diagnostics are reduced to their public code and
exit status, never raw output.

## Reproducible entrypoints

```bash
python3 tools/parity_gate.py catalog-check \
  --contract workloads/parity-v1/contract.json

python3 tools/parity_gate.py run \
  --contract workloads/parity-v1/contract.json \
  --styio-root styio-nightly \
  --build-dir styio-nightly/build/perf-parity-catalog \
  --out-dir reports/perf-parity/smoke \
  --sizes small --warmups 1 --repetitions 3

python3 tools/parity_gate.py verify \
  --contract workloads/parity-v1/contract.json \
  --report reports/perf-parity/smoke/results.json \
  --mode smoke --privacy strict
```

`tools/perf-route.sh` delegates to the same commands and accepts only the
catalog's three route IDs. It does not preserve a second benchmark model.
