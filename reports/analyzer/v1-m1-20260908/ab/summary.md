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

Baseline version 0.0.1 revision untraceable dirty None artifact fc8f94af6e7d42ad796dd9c3f47cea1e1970b47b1ebaf5f7ad12e6eb3d2acca0
Candidate version 0.0.1 revision ec6ba022519fd960c4457e0c890df2dd65366256 dirty True artifact ff4b24401b1de81ee818196005b31312d54d6a7990dbd0633979ee3b6a946586
External clang 21.0.0 comparable=True

## Cells

### llvm-scalar-chain/smoke/compile-and-run
- route: compile-and-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=2.874171666975599 candidate=4.672165166994091 ratio=1.5692760702852582 cv_b=10.181585171740238 cv_c=8.465131383583998
- time interval 1.4800715134606195 .. 1.6711196318338781 status=inconclusive (noise_cv)
- rss ratio=1.0139446063331952 status=regressed (interval_above_one)
- artifact bytes baseline=257568 candidate=389824 delta=132256 ratio=1.5134799353957014 status=direct_observation

### llvm-scalar-chain/smoke/native-build
- route: native-build
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=2.8995345829753205 candidate=4.558391958009452 ratio=1.6829491373163838 cv_b=9.58851504663863 cv_c=8.630063561733872
- time interval 1.5842214468327587 .. 1.7898534394826342 status=inconclusive (noise_cv)
- rss ratio=1.0076074051268495 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=257568 candidate=389824 delta=132256 ratio=1.5134799353957014 status=direct_observation

### llvm-scalar-chain/smoke/native-run
- route: native-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=0.002463003178028453 candidate=0.0021713296835123965 ratio=0.8965549476948618 cv_b=35.047743616553156 cv_c=21.423189679319936
- time interval 0.7603799686128712 .. 1.054044047528246 status=inconclusive (noise_cv)
- rss ratio=1.0224719101123596 status=regressed (interval_above_one)
- artifact bytes baseline=257568 candidate=389824 delta=132256 ratio=1.5134799353957014 status=direct_observation

### llvm-control-diamonds/smoke/compile-and-run
- route: compile-and-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=3.2089589160168543 candidate=4.870087249961216 ratio=1.3735770957827964 cv_b=36.24636300494224 cv_c=11.993465106777625
- time interval 1.1209430790023749 .. 1.6409843861630788 status=inconclusive (noise_cv)
- rss ratio=1.0058193822379542 status=regressed (interval_above_one)
- artifact bytes baseline=257568 candidate=389824 delta=132256 ratio=1.5134799353957014 status=direct_observation

### llvm-control-diamonds/smoke/native-build
- route: native-build
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=3.3443406249862164 candidate=5.443815582955722 ratio=1.6209520324167648 cv_b=50.28730880143174 cv_c=56.089799895811986
- time interval 1.2141653446649423 .. 2.2306891516669576 status=inconclusive (noise_cv)
- rss ratio=1.0148561692023566 status=regressed (interval_above_one)
- artifact bytes baseline=257568 candidate=389824 delta=132256 ratio=1.5134799353957014 status=direct_observation

### llvm-control-diamonds/smoke/native-run
- route: native-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=0.002947812692190592 candidate=0.0025895880768075584 ratio=0.9227809394136868 cv_b=24.331642077596506 cv_c=25.149051327912165
- time interval 0.8028231006323552 .. 1.0758715215108958 status=inconclusive (noise_cv)
- rss ratio=1.0454545454545454 status=regressed (interval_above_one)
- artifact bytes baseline=257568 candidate=389824 delta=132256 ratio=1.5134799353957014 status=direct_observation

### llvm-dense-matmul/smoke/compile-and-run
- route: compile-and-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=3.646611790987663 candidate=5.688579750014469 ratio=1.6294665016026622 cv_b=15.105355021924503 cv_c=21.39535053875603
- time interval 1.4144592727876881 .. 1.9221879156684423 status=inconclusive (noise_cv)
- rss ratio=1.0137369926066642 status=no_detected_change (interval_contains_one)
- artifact bytes baseline=257584 candidate=389824 delta=132240 ratio=1.5133859245915895 status=direct_observation

### llvm-dense-matmul/smoke/native-build
- route: native-build
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=2.916192583972588 candidate=5.456029249995481 ratio=1.71452325543302 cv_b=21.7241795652874 cv_c=10.39591395326058
- time interval 1.5705976177638596 .. 1.8844396221107584 status=inconclusive (noise_cv)
- rss ratio=1.0127848351347537 status=regressed (interval_above_one)
- artifact bytes baseline=257584 candidate=389824 delta=132240 ratio=1.5133859245915895 status=direct_observation

### llvm-dense-matmul/smoke/native-run
- route: native-run
- correctness baseline=True candidate=True
- status: pass; reasons: none
- time medians baseline=0.0028356534963542663 candidate=0.0024972003086952974 ratio=0.890605492469324 cv_b=18.36115318939348 cv_c=11.07877249925558
- time interval 0.7893521566554363 .. 0.9919908466100804 status=inconclusive (noise_cv)
- rss ratio=1.0337078651685394 status=regressed (interval_above_one)
- artifact bytes baseline=257584 candidate=389824 delta=132240 ratio=1.5133859245915895 status=direct_observation

