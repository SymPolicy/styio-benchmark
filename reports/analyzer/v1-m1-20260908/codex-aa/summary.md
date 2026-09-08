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

Baseline version 0.0.1 revision ec6ba022519fd960c4457e0c890df2dd65366256 dirty False artifact 9480f2523c23658518a684dd3b1de5d6db94b0e56f95bf9188834626fdefc596
Candidate version 0.0.1 revision ec6ba022519fd960c4457e0c890df2dd65366256 dirty False artifact 9480f2523c23658518a684dd3b1de5d6db94b0e56f95bf9188834626fdefc596
External clang 21.0.0 selection=path_lookup comparable=True
Runtime binding baseline=confirmed candidate=confirmed

## Cells

### llvm-scalar-chain/smoke/compile-and-run
- route: compile-and-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=3.5563292920123786 candidate=3.682774499990046 ratio=1.0643036610739733 cv_b=5.3353005261998785 cv_c=15.977869456601915
- time interval 0.9930346082310623 .. 1.160743170737142 status=inconclusive (noise_cv)
- rss ratio=1.0053974272470396 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=389824 candidate=389824 delta=0 ratio=1.0 status=direct_observation

### llvm-scalar-chain/smoke/native-build
- route: native-build
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=3.4871049579815008 candidate=3.6611145000206307 ratio=1.0710259220552225 cv_b=10.085186316877474 cv_c=10.718798955446143
- time interval 0.9825019719696776 .. 1.1624311104832816 status=inconclusive (noise_cv)
- rss ratio=1.002993482564821 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=389824 candidate=389824 delta=0 ratio=1.0 status=direct_observation

### llvm-scalar-chain/smoke/native-run
- route: native-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=0.003757912939162955 candidate=0.003218621274786659 ratio=0.9368258452797279 cv_b=27.38131441784723 cv_c=34.49708213724845
- time interval 0.8394455359092661 .. 1.0608644934421723 status=inconclusive (noise_cv)
- rss ratio=1.0 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=389824 candidate=389824 delta=0 ratio=1.0 status=direct_observation

