# Styio Analyzer v1 comparison

This summary is derived from results.json.  compare success means required
evidence was collected.  verify success means the report recomputes.
Neither result is a performance acceptance of the candidate.

Comparison kind: styio-revision
Catalog: parity-v2 digest ffda1fd9dadb6493c3a722302b7abd823a402ce956f4ccbad6b45c54bdda1e6c
Corpus: project microkernels; not an official suite.
Scale: smoke; official=False
Run class: development
Selected families: llvm-scalar-chain, llvm-control-diamonds, llvm-dense-matmul
Full catalog selected: False
Requested cells: 9
Observed cells: 9
Incomplete cells: 0
Blocked capabilities (not scored): arbitrary-precision, bit-packed, byte-buffer, object-node, regular-expression
Collection complete: True
Performance claim: not-evaluated

## Toolchains

Baseline version 0.0.1 revision 4e423e928db15f9bd61aae7ce014c2e1f7847256 dirty False artifact c8f07e2994b51f5e5eca172c9d2c7ec77c4461348474d87512e169992a5b5b90
Candidate version 0.0.1 revision ec6ba022519fd960c4457e0c890df2dd65366256 dirty False artifact 9480f2523c23658518a684dd3b1de5d6db94b0e56f95bf9188834626fdefc596
External clang 21.0.0 selection=path_lookup comparable=True
Runtime binding baseline=confirmed candidate=confirmed

## Cells

### llvm-scalar-chain/smoke/compile-and-run
- route: compile-and-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=2.683054708002601 candidate=4.130384999967646 ratio=1.6015471113095745 cv_b=10.472515347688278 cv_c=13.671139316083666
- time interval 1.4539008577440329 .. 1.7718256915958792 status=inconclusive (noise_cv)
- rss ratio=1.005036969894764 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=257568 candidate=389824 delta=132256 ratio=1.5134799353957014 status=direct_observation

### llvm-scalar-chain/smoke/native-build
- route: native-build
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=3.3115313329617493 candidate=4.89973987499252 ratio=1.7476215208936017 cv_b=48.40788897334809 cv_c=88.34461955707759
- time interval 1.4617678038787745 .. 2.157744016277388 status=inconclusive (noise_cv)
- rss ratio=1.0110635752349633 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=257568 candidate=389824 delta=132256 ratio=1.5134799353957014 status=direct_observation

### llvm-scalar-chain/smoke/native-run
- route: native-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=0.0027361160612101327 candidate=0.002451210249067281 ratio=0.9228040524763871 cv_b=31.08379942265161 cv_c=41.67217931939581
- time interval 0.7876319367741988 .. 1.0985729284838437 status=inconclusive (noise_cv)
- rss ratio=1.0227272727272727 status=regressed (interval_above_one)
- artifact bytes baseline=257568 candidate=389824 delta=132256 ratio=1.5134799353957014 status=direct_observation

### llvm-control-diamonds/smoke/compile-and-run
- route: compile-and-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=3.112915374978911 candidate=4.668532375013456 ratio=1.8362439002637985 cv_b=21.150250059100074 cv_c=35.59888835580722
- time interval 1.5515641078354512 .. 2.1625994938351467 status=inconclusive (noise_cv)
- rss ratio=1.0089342233913152 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=257568 candidate=389824 delta=132256 ratio=1.5134799353957014 status=direct_observation

### llvm-control-diamonds/smoke/native-build
- route: native-build
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=3.621501541987527 candidate=4.925345459021628 ratio=1.583327877219859 cv_b=27.657622486758626 cv_c=35.10435164424898
- time interval 1.4411698826445694 .. 1.7203704047990367 status=inconclusive (noise_cv)
- rss ratio=1.0117271151220366 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=257568 candidate=389824 delta=132256 ratio=1.5134799353957014 status=direct_observation

### llvm-control-diamonds/smoke/native-run
- route: native-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=0.002403247961964276 candidate=0.0020755364565285045 ratio=0.8385011553262637 cv_b=18.02204622467466 cv_c=24.715138200868374
- time interval 0.7838763547552571 .. 0.8938629734630341 status=inconclusive (noise_cv)
- rss ratio=1.0459770114942528 status=regressed (interval_above_one)
- artifact bytes baseline=257568 candidate=389824 delta=132256 ratio=1.5134799353957014 status=direct_observation

### llvm-dense-matmul/smoke/compile-and-run
- route: compile-and-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=2.8719920000294223 candidate=4.508761790988501 ratio=1.718886846056375 cv_b=15.762770348641423 cv_c=20.33701428812301
- time interval 1.58883239756614 .. 1.8841158013300296 status=inconclusive (noise_cv)
- rss ratio=1.007538581874007 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=257584 candidate=389824 delta=132240 ratio=1.5133859245915895 status=direct_observation

### llvm-dense-matmul/smoke/native-build
- route: native-build
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=2.502528709010221 candidate=5.283558249997441 ratio=1.8003257408741746 cv_b=32.941496016819976 cv_c=25.682868450976535
- time interval 1.3880195380574056 .. 2.2952919331090462 status=inconclusive (noise_cv)
- rss ratio=1.0205383736159928 status=regressed (interval_above_one)
- artifact bytes baseline=257584 candidate=389824 delta=132240 ratio=1.5133859245915895 status=direct_observation

### llvm-dense-matmul/smoke/native-run
- route: native-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=0.002407500333307932 candidate=0.0021044831120719514 ratio=0.8016982205620086 cv_b=41.773322042319435 cv_c=11.012922114963878
- time interval 0.6810681936824573 .. 0.9085356591014273 status=inconclusive (noise_cv)
- rss ratio=1.0340909090909092 status=regressed (interval_above_one)
- artifact bytes baseline=257584 candidate=389824 delta=132240 ratio=1.5133859245915895 status=direct_observation

