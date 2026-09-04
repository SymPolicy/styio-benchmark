# Observable Language Performance Gate Plan

**Purpose:** Define future black-box correctness and performance evidence for Styio observable-language stages without owning their protocols or inventing thresholds before measurement.

**Last updated:** 2026-09-04

**Status:** Planned, not authorized, and not started.

## 1. Ownership and Activation

This repository owns observable-language benchmark workloads, runners, probes, report fields, controlled baselines, comparison rules, and privacy-safe summaries. It does not own snapshot, delta, query, lineage, runtime-event, identifier, evidence, or completeness semantics.

Each measurement slice activates only after the matching Styio stage publishes an accepted version and deterministic correctness fixtures:

| Benchmark slice | Producer gate | Earliest measurement scope |
|---|---|---|
| B1 — static snapshot | `styio-nightly:docs/plan/observable-static-snapshot/Plan.md` (PLAN-004) | snapshot construction and serialization |
| B2 — delta, lineage, and query | `styio-nightly:docs/plan/observable-delta-query-lineage/Plan.md` (PLAN-005) | invalidation, delta, reconstruction, query, and cache behavior |
| B3 — runtime correlation | `styio-nightly:docs/plan/observable-runtime-correlation/Plan.md` (PLAN-006) | disabled overhead, enabled event production, scheduler impact, buffering, aggregation, and loss |

An unapproved plan or the attachment `Styio-Observable-Language-Long-Term-Evolution-2026-09-04.zip` cannot activate benchmark implementation. The attachment is reference material only.

## 2. Measurement Order

Every slice follows the same evidence order:

1. import or reproduce the accepted producer fixtures without changing their semantics,
2. add a deterministic contract test proving the runner measures the intended behavior,
3. define stable workload identity, scale parameters, metric units, and directionality,
4. verify privacy-safe report output and compatibility with historical reports,
5. collect a controlled local baseline with raw samples and environment eligibility,
6. collect comparable candidate runs,
7. adopt a regression threshold only after multiple eligible runs demonstrate a stable noise floor and meaningful effect size.

Correctness failure invalidates the performance sample. A fast result for incomplete, lossy, stale, or semantically different output is not a performance improvement.

## 3. B1 — Static Snapshot Evidence

### Workloads

Cover small, medium, and large qualified compilations across representative topology shapes:

1. sparse and dense node/edge sets,
2. repeated resource/state shapes that exercise canonicalization,
3. evidence-rich and explicitly incomplete snapshots,
4. equivalent inputs rooted in different machine locations,
5. observability disabled as the ordinary-compilation control.

### Metrics

1. ordinary compilation latency and peak memory with observability disabled,
2. incremental snapshot-construction latency when enabled,
3. canonical serialization latency,
4. peak and retained memory attributable to snapshot production,
5. serialized artifact bytes,
6. node, edge, fact, evidence, and source-anchor counts used to normalize cost.

### Correctness gate

The benchmark must verify deterministic bytes or the producer-defined canonical equivalence, stable logical identity across absolute-location changes, explicit completeness, and absence of forbidden private fields before recording timing.

## 4. B2 — Delta, Lineage, Query, and Cache Evidence

### Workloads

1. no-op recompilation,
2. one local semantic change,
3. fan-out invalidation of increasing width,
4. deletion and replacement,
5. wrong-parent and stale-delta rejection,
6. bounded query shapes over small, medium, and large snapshots,
7. sustained updates that cross the configured cache/lineage retention boundary.

### Metrics

1. invalidation and delta-construction latency,
2. delta bytes relative to full snapshot bytes,
3. child reconstruction latency,
4. bounded query latency by query shape and result size,
5. cache hit/miss counts and retained cache memory,
6. lineage depth retained and eviction work,
7. full-snapshot fallback count where the public contract permits it.

### Correctness gate

For every timed accepted case, applying the delta to the declared parent must yield the accepted child and query results must equal the corresponding snapshot facts. Rejected stale or wrong-parent cases must leave the last valid state unchanged.

## 5. B3 — Runtime and Scheduler Correlation Evidence

### Workloads

1. observability disabled,
2. static snapshot support enabled with runtime events disabled,
3. summary, asynchronous, and detailed/trace modes if the published contract defines them,
4. scheduler fan-out, contention, wait chains, and causal chains across small, medium, and large scales,
5. sustained event pressure that exercises bounded buffers, sampling, aggregation, and declared loss behavior.

### Metrics

1. disabled-mode compile/runtime overhead,
2. static-only overhead,
3. scheduler throughput and tail latency,
4. task dispatch, wake, and wait-path latency where the producer exposes stable measurement points,
5. peak queue depth and retained buffer memory,
6. emitted, aggregated, sampled, dropped, and delivered event counts,
7. event bytes and production/serialization latency,
8. consumer-independent correlation success for explicit snapshot/site/instance identifiers.

### Correctness gate

Event ordering, causal/wait relationships, identifiers, and loss/sampling accounting must match accepted producer fixtures. The runner must not reconstruct missing events or infer joins from timestamps, names, source positions, or runtime values.

## 6. Baseline Eligibility

A run may become a comparison baseline only when:

1. the correctness gate passes before measurement,
2. the Styio contract version, benchmark revision, workload identity, scale, build profile, and runner options are recorded,
3. the existing environment checker reports the controlled host as eligible or every warning is explicitly documented,
4. warm-up and independent repeat policy are satisfied,
5. raw samples, median, confidence interval, coefficient of variation, and comparison metadata are retained,
6. the report contains no host identity, absolute paths, environment values, source content, secrets, runtime values, or service endpoints,
7. baseline and candidate inputs are contract-compatible.

Moving a baseline to another machine class or changing workload semantics requires a new baseline series; it is not an in-place continuation.

## 7. Threshold Adoption

No numeric regression threshold is approved by this plan.

A threshold proposal requires multiple eligible baseline and candidate runs, a documented noise floor, direction-aware practical impact, confidence evidence, and separate review for each metric family and scale. A single host run, a single median, or statistical significance without practical impact is insufficient.

Until a threshold is approved, reports classify observed changes using existing comparison semantics and remain advisory. Shared CI validates schemas, fixtures, deterministic statistics, and tool behavior only; it must not run wall-clock performance gates.

## 8. Planned Artifacts

When a slice is separately authorized, it may add only the artifacts needed for that slice:

1. versioned fixture adapters or black-box probes,
2. workload definitions with stable IDs and scale parameters,
3. additive report-schema fields,
4. deterministic contract tests and privacy assertions,
5. controlled-local runner routes,
6. baseline eligibility and comparison documentation.

Stored performance reports are curated only after eligibility review. Temporary measurements and machine-specific outputs remain untracked.

## 9. Non-Goals

This plan does not authorize:

1. changing Styio protocol semantics or producer implementation,
2. copying compiler-private code into benchmark probes,
3. backend telemetry, remote collection, cloud storage, or uploading runtime data,
4. raw source or runtime-value capture,
5. shared-CI wall-clock benchmarking,
6. arbitrary checksum layers, duplicate runners, or a second comparison framework,
7. a threshold chosen from one machine or one observation.

## 10. Completion Criteria

Each authorized benchmark slice is complete only when its correctness fixture, controlled runner, report contract, privacy gate, baseline eligibility check, and documentation all pass together. B1, B2, and B3 remain independently reviewable; completing one does not imply that another has started.
