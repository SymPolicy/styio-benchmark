# Parity workload catalog

The active frozen catalog is
[`workloads/parity-v2/contract.json`](../workloads/parity-v2/contract.json).
It contains eleven independently implemented Styio/C++ workload families,
three closed route boundaries, three explicitly labelled scales, deterministic
input and output digests, and five compiler-phase diagnostics.

The `clbg-*` and `llvm-*` names are retained historical style labels. These are
project microkernels, not copies or ports of official suite programs.

Workload identity, measurement rules, scoring, capability exclusions, and
reproducible commands are documented in
[`STANDARD-PARITY.md`](STANDARD-PARITY.md). Earlier catalogs are not eligible
for a current parity claim.
