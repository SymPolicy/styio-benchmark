# M1 publication boundary and migration record

Status: publication authorized on 2026-09-09. Required retirement of the replaced implementation is part of the project migration rule.

The publication starts from task-branch revision `b987e89`. M1 was
implemented and validated on the working-tree parity measurement contract,
which has not yet been committed. Publishing only the three new Analyzer
Python files would omit their required catalog and gate behavior.

The runnable publication includes the existing parity-v2 measurement
foundation (catalog metadata, batching/statistics and gate tests), its already
performed parity-v1 entrypoint cleanup and documentation/probe migration,
then the M1 Analyzer, shared core, deterministic CI and delivery evidence.
These prerequisite edits predate the Codex R1–R4 repair; this publication
preserves their current content rather than reimplementing them.

Excluded: research corpus, research reports and dossier, research-only README
and gitignore additions, compiler/runtime source, isolated builds and raw CLI
logs. The local research work remains intact.

Validation applies to the current runnable source/test state: final 72 passed,
1 conditional parser-scaling skip; real A/A 3 and A/B 9 cells verify and
arithmetic checks passed. No new source edits are proposed here.

Included implementation/evidence files (repository-relative; this publication record is included as well):

```text
A	.github/workflows/standard-parity-contract.yml
M	.gitignore
M	README.md
M	docs/COVERAGE-MATRIX.md
M	docs/IN_TREE_BENCHMARKS.md
M	docs/PARITY-MEASUREMENT.md
M	docs/PARITY-WORKLOADS.md
M	docs/STANDARD-PARITY.md
A	docs/STYIO-ANALYZER-DESIGN.md
A	docs/STYIO-ANALYZER-IMPLEMENTATION-PLAN.md
A	docs/STYIO-ANALYZER-V1.md
A	docs/prompts/styio-analyzer-v1.cursor.md
M	native-cpp/standard_process_tree_rss.py
M	reports/README.md
A	reports/analyzer/v1-m1-20260908/ACCEPTANCE-RECHECK.md
A	reports/analyzer/v1-m1-20260908/ACCEPTANCE.md
A	reports/analyzer/v1-m1-20260908/HANDOFF.md
A	reports/analyzer/v1-m1-20260908/SOURCE-CHANGES.md
A	reports/analyzer/v1-m1-20260908/aa/recompute-after-fix.json
A	reports/analyzer/v1-m1-20260908/aa/recompute.json
A	reports/analyzer/v1-m1-20260908/aa/results.json
A	reports/analyzer/v1-m1-20260908/aa/summary.md
A	reports/analyzer/v1-m1-20260908/ab/recompute-after-fix.json
A	reports/analyzer/v1-m1-20260908/ab/recompute.json
A	reports/analyzer/v1-m1-20260908/ab/results.json
A	reports/analyzer/v1-m1-20260908/ab/summary.md
A	reports/analyzer/v1-m1-20260908/codex-aa/arithmetic-check.json
A	reports/analyzer/v1-m1-20260908/codex-aa/recompute.json
A	reports/analyzer/v1-m1-20260908/codex-aa/results.json
A	reports/analyzer/v1-m1-20260908/codex-aa/summary.md
A	reports/analyzer/v1-m1-20260908/codex-ab/arithmetic-check.json
A	reports/analyzer/v1-m1-20260908/codex-ab/recompute.json
A	reports/analyzer/v1-m1-20260908/codex-ab/results.json
A	reports/analyzer/v1-m1-20260908/codex-ab/summary.md
A	reports/analyzer/v1-m1-20260908/codex-final-regression.txt
A	reports/analyzer/v1-m1-20260908/codex-relocated-runtime.json
A	reports/analyzer/v1-m1-20260908/codex-targeted-verification.txt
A	reports/analyzer/v1-m1-20260908/final-regression.txt
A	reports/analyzer/v1-m1-20260908/targeted-verification.txt
M	styio-probes/styio_soak_test.cpp
D	tests/test_parity_catalog.py
D	tests/test_parity_gate.py
D	tests/test_report_privacy.py
M	tests/test_standard_parity_catalog.py
M	tests/test_standard_parity_gate.py
A	tests/test_styio_analyzer.py
A	tools/measurement_core.py
D	tools/parity_gate.py
M	tools/perf-route.sh
M	tools/standard_parity_gate.py
A	tools/styio_analyzer.py
D	workloads/parity-v1/contract.json
D	workloads/parity-v1/diagnostics/lex_unterminated_block_comment.styio
D	workloads/parity-v1/diagnostics/parse_unexpected_token.styio
D	workloads/parity-v1/diagnostics/runtime_read_missing_file.styio
D	workloads/parity-v1/diagnostics/type_final_then_flex_i64.styio
D	workloads/parity-v1/generators.py
D	workloads/parity-v1/sources/collection_reduce.cpp
D	workloads/parity-v1/sources/collection_reduce.styio
D	workloads/parity-v1/sources/scalar_compute.cpp
D	workloads/parity-v1/sources/scalar_compute.styio
D	workloads/parity-v1/sources/stream_io.cpp
D	workloads/parity-v1/sources/stream_io.styio
M	workloads/parity-v2/contract.json
```

One-time migration check: the retired runner, its tests and the parity-v1 corpus are absent; active entrypoints and documentation reference parity-v2. Historical measurement and acceptance records remain as evidence, not executable compatibility paths. The inspection is not a permanent test gate.
