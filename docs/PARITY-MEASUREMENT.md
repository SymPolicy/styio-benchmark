# Parity measurement

The active measurement contract is
[`STANDARD-PARITY.md`](STANDARD-PARITY.md). It is implemented solely by
[`tools/standard_parity_gate.py`](../tools/standard_parity_gate.py) against
[`workloads/parity-v2/contract.json`](../workloads/parity-v2/contract.json).

The standard route uses equal adaptive batches with a 0.5-second minimum,
three warm-ups, eleven retained reproducibly interleaved pairs, exact output
validation, isolated RSS replays, and paired hierarchical 95% bootstrap
intervals. Scores are separated by scale and route; compiler-phase records are
diagnostic only, and `native-run` is the primary native-performance claim.

Earlier parity report schemas and their aggregate model are not accepted by
the current merger or strict verifier.
