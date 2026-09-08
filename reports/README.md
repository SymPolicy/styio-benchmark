# Benchmark Reports

`tools/perf-route.sh` and `tools/parity_gate.py` write privacy-safe evidence
under this directory.

约定：

- Each run uses an explicitly selected output directory.
- Generated reports are evidence, not parity decisions: `run` writes
  `results.json` with `verification: not-evaluated` and a public-only
  `summary.md`.
- `verify` reads `results.json` and emits a stable reason-code decision.
- Reports must not contain paths, host/user identity, environment values,
  commands, endpoints, secrets, or unsanitized subprocess text.

Promoted evidence should remain limited to `results.json` and the short
summary; raw logs and machine-specific metadata are intentionally not stored.

Curated `styio_core_bench` evidence lives under `reports/core/`; core comparison
is provided by `tools/core-benchmark-compare.py`.

Analyzer v1 comparison evidence lives under `reports/analyzer/`. Those files
are Styio-revision comparisons, not Styio/C++ parity claims. `results.json`
is the numeric source; `verify` only recomputes consistency.

The active parity-v2 catalog digest is
`f7e99de17a1366325bcf08223f861f40d4aa95423ebef5ed2622267b0dbc951c`. Phase
reports generated before the static C++ binding-chain correction are retired
with reason `retired_invalid_structure` and must not enter a merge. Strict
parity remains incomplete until corrected reference shards and all required
gates pass.
