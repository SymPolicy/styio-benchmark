# Styio standard parity (parity-v2)

This document describes the frozen, standards-derived comparison contract. The
machine-readable authority is
[`workloads/parity-v2/contract.json`](../workloads/parity-v2/contract.json);
the report gate is [`tools/standard_parity_gate.py`](../tools/standard_parity_gate.py).

The current catalog-check digest is
`f7e99de17a1366325bcf08223f861f40d4aa95423ebef5ed2622267b0dbc951c`.
Reference evidence produced before the compiler-phase C++ source was corrected
to the static binding-chain structure is retired (`retired_invalid_structure`)
and must not be merged into a final report.

## Workload identity

The catalog contains three independently implemented Computer Language
Benchmarks Game (CLBG) families—fannkuch-redux, spectral-norm, and n-body—and
eight LLVM TestSuite-style families covering scalar chains, call graphs,
control diamonds, recursive SCCs, collection mutation, dictionary updates,
dense matrix multiplication, and list allocation. Each family has separate
Styio and C++ source digests, a deterministic input digest, a canonical output
oracle, an algorithm identity, and a static work-unit count.

CLBG descriptions are used as the provenance for the first three families;
LLVM TestSuite/LNT structure supplies the compile, execute, output, code-size,
and sharding shape for the remaining families. SPEC CPU rules are measurement
methodology only. No SPEC program or input is copied into this repository.

## Scale labels

Every family and compiler-phase cell is present at all three scales:

- `smoke` is a bounded development check;
- `development` is a local tuning sweep; and
- `reference` is the official reference scale and the only scale eligible for
  the aggregate parity claim.

The runner never relabels a reduced input as an official result. A shard report
records its scale label and the merge gate keeps cell identities disjoint.

## Fair paired measurement

Both implementations use the same algorithm identity, input bytes, numeric
contract, operation order, static work units, and one thread. The C++ companion
is compiled as C++20 with `-O3 -DNDEBUG -fno-lto`; it uses no weak optimization,
debug, sanitizer, LTO, or avoidable synchronous stream mode. Styio is measured
through its normal native artifact path.

The three closed route boundaries are:

- `compile-and-run`: fresh optimized build followed by execution;
- `native-build`: fresh build only, with the artifact discarded; and
- `native-run`: execution of an artifact built outside the timed region.

Correctness is checked against the catalog output digest before timing. Three
warmups and eleven retained repetitions are required for reference evidence;
all samples are retained, paired order is alternated, and cells whose sample
CV exceeds 5% fail closed and are rerun as whole cells. Time is observer-free;
peak process-tree RSS is collected by an isolated replay rather than a sampler
inside the timed region.

The compiler-phase sweep also records generated token counts,
declaration counts, expression-node counts, print nodes, and a zero-runtime-loop
assertion for each scale. The gate independently parses both generated sources
and rejects a runtime loop or unequal static structure, even when the nominal
token target is unchanged.

## Compiler-owned native-build cache and attribution

`styio build` may reuse compiler-owned objects for the immutable runtime
translation units and for each generated user IR and wrapper. The cache is
opt-in by configuration (`STYIO_NATIVE_RUNTIME_CACHE_DIR`; set
`STYIO_NATIVE_CACHE=0` to disable) and is never report evidence. Each entry is
bound to the compiler path and version, target, ABI, complete build flags,
runtime/header closure, and generated source content. Objects and their
metadata are published with a temporary directory and atomic rename; malformed
or mutated entries trigger a cold compile, while cache failures fall back to
the original source-link route.

For diagnosis outside timed samples, set `STYIO_NATIVE_BUILD_PROFILE_OUT` to
capture privacy-safe native-build phase durations, or set
`STYIO_NATIVE_PROFILE_OUT` for generated-artifact runtime initialization and
execute phases. These profiles attribute work; they do not alter the paired
measurement boundary or parity thresholds.

## Commands

Validate the frozen catalog and C++ strength contract:

```bash
python3 tools/standard_parity_gate.py catalog-check \
  --contract workloads/parity-v2/contract.json
python3 tools/standard_parity_gate.py cpp-strength \
  --contract workloads/parity-v2/contract.json
```

Run one bounded smoke shard (use `--scale reference` only for the official
reference sweep):

```bash
python3 tools/standard_parity_gate.py run \
  --contract workloads/parity-v2/contract.json \
  --family clbg-fannkuch-redux --scale smoke \
  --styio-root ../styio-nightly --build-dir ../styio-nightly/build \
  --out-dir reports/standard-parity/shards/clbg-fannkuch-redux \
  --warmups 3 --repetitions 11
```

Merge complete, non-overlapping shards and apply strict reference gates:

```bash
python3 tools/standard_parity_gate.py merge \
  --contract workloads/parity-v2/contract.json \
  --reports-dir reports/standard-parity/shards \
  --out-dir reports/standard-parity/final
python3 tools/standard_parity_gate.py verify \
  --contract workloads/parity-v2/contract.json \
  --report reports/standard-parity/final/results.json \
  --require-all --max-cv-pct 5 --max-geomean-ratio 1.05 \
  --max-case-ratio 1.10 --privacy strict
```

The strict verifier requires every reference cell, no lower-is-better ratio
above 1.10, and an equal-weight geometric mean no greater than 1.05. It never
drops samples, picks the fastest run, relaxes thresholds, or changes the C++
baseline. Reports contain only public contract and statistic fields; machine
identity, absolute paths, commands, endpoints, credentials, and raw subprocess
text are rejected.

Strict parity is not complete for this delivery: the corrected compiler-phase
reference sweep has not been rerun, and the existing family/reference evidence
still contains unresolved ratio and CV failures. Do not describe smoke,
development, or retired phase reports as a parity claim.

## Capability exclusions

Bit-packed, byte-buffer, object-node, regular-expression, and arbitrary-
precision cases remain `capability-blocked` and outside the aggregate. Adding a
syntax, type-system, ABI, or weakened native implementation to make an excluded
case pass would invalidate this contract.
