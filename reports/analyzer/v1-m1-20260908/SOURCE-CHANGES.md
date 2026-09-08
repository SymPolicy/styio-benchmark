# Analyzer M1 source changes

Uncommitted. Includes the original M1 delivery, the first F1–F5 repair,
and the Codex R1–R4 repair of the second audit. Other tasks' dirty files
were left in place. No Styio compiler/runtime source was changed.

## Added (original M1 and this fix pass)

- `tools/measurement_core.py` — shared sampling, statistics, privacy, paired
  measurement; late failures now return collected samples and the failure stage
- `tools/styio_analyzer.py` — compare / verify CLI; verify now recomputes
  displayed statistics, derives coverage, and requires toolchain binding
  evidence before `comparable=true`
- `tests/test_styio_analyzer.py` — deterministic Analyzer tests, including
  F1–F5 and R1–R4 acceptance counterexamples
- `reports/analyzer/v1-m1-20260908/` — original delivery, both independent
  audits, and separately identified repair evidence

## Edited for M1 (pre-existing dirty files kept their other-task edits)

- `tools/standard_parity_gate.py` — import shared core; `_measure_cell` is a
  Styio/C++ adapter. This fix pass did not change that file.
- `README.md` — document the live Analyzer CLI
- `docs/IN_TREE_BENCHMARKS.md` — add the Analyzer entrypoint bullet
- `reports/README.md` — Analyzer report location
- `.gitignore` — allow `reports/analyzer/`
- `.github/workflows/standard-parity-contract.yml` — add deterministic Analyzer tests
- `docs/STYIO-ANALYZER-V1.md` — status and the implemented toolchain binding,
  partial-evidence and coverage rules
- `docs/STYIO-ANALYZER-IMPLEMENTATION-PLAN.md` — status line only

## Earlier repair (acceptance F1–F5)

- `tools/measurement_core.py` — retain completed pairs on retained/RSS
  failure; do not drop time samples or flip unrelated correctness
- `tools/styio_analyzer.py` — verify displayed medians/intervals/RSS/volume;
  derive coverage and collection_complete; resolve and pin native Clang.
  The earlier directory-based binding heuristic was rejected in the second
  audit and is removed by the Codex repair below.
- `tests/test_styio_analyzer.py` — F1–F4 counterexamples; F5 removed the
  empty `or True` assertion and the permanent migration-string gate
- `reports/analyzer/v1-m1-20260908/HANDOFF.md` — resubmission
- `reports/analyzer/v1-m1-20260908/aa/recompute-after-fix.json` and
  `ab/recompute-after-fix.json` — re-verify of unmodified historical reports
- `reports/analyzer/v1-m1-20260908/targeted-verification.txt` and
  `final-regression.txt` — new runs after the fixes

## Codex repair (acceptance recheck R1–R4)

- `tools/styio_analyzer.py` — observe the runtime input of an actual Styio
  native build once before measurement; remove directory-based inference.
  The observer stops before linking and records only a match and public
  native configuration. Preserve the absolute CXX invocation name, including
  `clang++` symlinks; compare driver mode, target and public build settings.
- `tools/styio_analyzer.py` — validate all available paired and unpaired
  evidence independently of sample sufficiency; recompute derived fields,
  reject contradictory partial statistics, and check source/input identity
  and capabilities against the same catalog selection.
- `tools/measurement_core.py` — save unpaired raw batch times; describe the
  completed retained-pair prefix on partial failure. Existing sampling,
  statistics and the parity adapter remain shared.
- `tests/test_styio_analyzer.py` — embedded-runtime/fallback behavior,
  actual C++ links through all four CXX resolution paths, partial-evidence
  corruption, catalog/coverage contradictions, and private build settings.
  Remove the remaining permanent source-string migration assertion.
- `README.md`, `docs/STYIO-ANALYZER-V1.md` — document the resulting behavior.
- This directory — update the handoff and source-change inventory; add
  `codex-targeted-verification.txt`, `codex-relocated-runtime.json`,
  `codex-aa/`, `codex-ab/`, and `codex-final-regression.txt`.
  See HANDOFF.md for completed results.

Original `aa/results.json`, `ab/results.json`, `ACCEPTANCE.md`, and
`ACCEPTANCE-RECHECK.md` were not rewritten. Existing revisions were built
in isolated worktrees to produce new real A/A and A/B evidence; those
builds are not compiler optimization changes.

## Not touched (other tasks)

Research workloads, research reports, `docs/RESEARCH-CONTAINER-SNAPSHOT-COPIES.md`,
parity-v2 contract/generators, `docs/STANDARD-PARITY.md`, `docs/PARITY-*.md`,
`docs/COVERAGE-MATRIX.md`, `native-cpp/standard_process_tree_rss.py`,
`tools/perf-route.sh`, `styio-probes/`, and all Styio compiler/runtime sources.
