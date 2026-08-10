# parity-v1 Workload Catalog

`workloads/parity-v1/contract.json` is the only required-cell manifest for
Styio/C++ compiler and generated-program parity.  It is versioned with the
benchmark repository and contains no machine identity, local path, network
address, credential, or measurement result.

## Fixed workload families

| Family | Algorithm | Logical sizes | Input generator |
| --- | --- | ---: | --- |
| `scalar-compute` | modular accumulator | `10000`, `250000`, `5000000` iterations | `generators.py:scalar_compute_input` |
| `collection-reduce` | weighted modular sum | `256`, `4096`, `65536` elements | `generators.py:collection_reduce_input` |
| `stream-I/O` | byte-preserving line stream | `65536`, `4194304`, `33554432` bytes | `generators.py:stream_io_input` |

Each family has a Styio source and an equivalent C++ source under
`workloads/parity-v1/sources/`.  A cell is the Cartesian product of one fixed
size and one supported route:

| Route | Timed boundary | Artifact rule |
| --- | --- | --- |
| `compile-and-run` | source read → validated stdout | no reusable artifact |
| `native-build` | source read → native artifact ready | discard the per-sample artifact |
| `native-run` | prebuilt artifact → validated stdout | build before timing |

The route list is intentionally closed.  A consumer must reject any route
not named in the manifest instead of silently mapping it to another boundary.

## Cell contract

Every workload cell declares:

- a stable `id`, `tier`, `size`, `algorithm_id`, and `work_units`;
- `source_digest` for both `styio` and `cpp` sources;
- the generated `input_digest`;
- `expected_output_digest` and the independently calculated
  `reference_output_digest`;
- `focus_owner`, `route`, and a relative `route_boundary`.

All digests are lowercase SHA-256 values over canonical UTF-8 or byte input.
The two output fields must agree in the frozen catalog, but they are retained
separately so a runner can prove that an implementation matched an oracle
rather than another implementation's output.

`generators.py` is deterministic and language-neutral.  Scalar input varies
the iteration count, collection input varies the generated list, and stream
input varies the exact byte count.  The reference functions execute the
algorithms independently and return canonical newline-terminated output.
Consequently, a program that prints a precomputed answer or removes the work
cannot satisfy all size cells and their input/output digests.

## Compiler phase sweep

`compiler_phase_sweep` freezes five in-process compiler stages:

1. `tokenize`
2. `parse`
3. `semantic-analysis`
4. `lowering`
5. `llvm-emission`

Each stage has generated binding-chain sources near `1000`, `16000`, and
`128000` tokens (the targets are recorded as work units, not timings). Styio
and C++ have language-specific source digests because their equivalent source
bytes differ. `ParityPhaseSweepReport` reads the selected tier from the
manifest, regenerates the Styio source, verifies the source and independent
output digests, and emits all five declared boundaries from one isolated
in-process pass. The parity runner shares that probe record across the five
phase cells and pairs it with one Clang `-ftime-trace` compile per sample.
The phase probe does not add a public syntax or route.

## Diagnostic corpus

The manifest includes representative, syntax-preserving failure fixtures:

- `STYIO_LEX_UNTERMINATED_BLOCK_COMMENT`
- `STYIO_PARSE_UNEXPECTED_TOKEN`
- `STYIO_TYPE_ERROR`
- `STYIO_RUNTIME_FILE_OPEN_READ`

Each diagnostic records a relative source, source/input digest, expected coarse
exit code, exact diagnostic code, focus owner, and compile-and-run diagnostic
boundary.  Consumers must compare the exact code; broad phase prefixes are not
an acceptable substitute.

## Validation

The catalog tests independently regenerate every input, compiler-phase source,
and reference output.  They reject missing or duplicate IDs, unsupported
routes, non-increasing sizes, digest drift, absolute paths, network-address-like
values, and stale diagnostic codes.  The C++ probe provides the focused smoke
for the small compiler-phase tier:

```text
STYIO_PARITY_SWEEP_TIER=small styio_soak_test --gtest_filter=StyioSoakSingleThread.ParityPhaseSweepReport
```
