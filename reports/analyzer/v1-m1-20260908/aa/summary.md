# Styio Analyzer v1 comparison

This summary is derived from results.json.  compare success means required
evidence was collected.  verify success means the report recomputes.
Neither result is a performance acceptance of the candidate.

Comparison kind: styio-revision
Catalog: parity-v2 digest ffda1fd9dadb6493c3a722302b7abd823a402ce956f4ccbad6b45c54bdda1e6c
Corpus: project microkernels; not an official suite.
Scale: smoke; official=False
Run class: development
Selected families: llvm-scalar-chain
Full catalog selected: False
Requested cells: 3
Observed cells: 3
Incomplete cells: 0
Blocked capabilities (not scored): arbitrary-precision, bit-packed, byte-buffer, object-node, regular-expression
Collection complete: True
Performance claim: not-evaluated

## Toolchains

Baseline version 0.0.1 revision ec6ba022519fd960c4457e0c890df2dd65366256 dirty True artifact ff4b24401b1de81ee818196005b31312d54d6a7990dbd0633979ee3b6a946586
Candidate version 0.0.1 revision ec6ba022519fd960c4457e0c890df2dd65366256 dirty True artifact ff4b24401b1de81ee818196005b31312d54d6a7990dbd0633979ee3b6a946586
External clang 21.0.0 comparable=True

## Cells

### llvm-scalar-chain/smoke/compile-and-run
- route: compile-and-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=3.851476832991466 candidate=4.062202166009229 ratio=1.0239915993836648 cv_b=12.193188052167987 cv_c=10.589686114688545
- time interval 0.9296464168778917 .. 1.1149395187209883 status=inconclusive (noise_cv)
- rss ratio=1.0080546497696763 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=389824 candidate=389824 delta=0 ratio=1.0 status=direct_observation

### llvm-scalar-chain/smoke/native-build
- route: native-build
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=4.088425915979315 candidate=4.387853000022005 ratio=1.028046153783585 cv_b=26.50357264221993 cv_c=13.816707578154197
- time interval 0.9004673849451736 .. 1.1775701267636738 status=inconclusive (noise_cv)
- rss ratio=1.008191250059558 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=389824 candidate=389824 delta=0 ratio=1.0 status=direct_observation

### llvm-scalar-chain/smoke/native-run
- route: native-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=0.0021241278677521986 candidate=0.002169835055054527 ratio=1.011312574424064 cv_b=20.676482143493015 cv_c=18.279115706741507
- time interval 0.9144462767340257 .. 1.1287281819223434 status=inconclusive (noise_cv)
- rss ratio=1.0 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=389824 candidate=389824 delta=0 ratio=1.0 status=direct_observation

