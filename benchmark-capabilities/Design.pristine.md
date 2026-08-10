# Benchmark capabilities survey and completion

## Requirements
- survey-evidence: Publish an industry benchmarking survey that explains measurement discipline, statistical inference, environment control, scale selection, baseline comparison, reproducibility, and the boundary between controlled local benchmarks and contract-only CI, with attributable primary references and a repository gap mapping. — source: user-request, resolved-decisions
- additive-runner-statistics: Both benchmark runners retain every existing JSON, CSV, Markdown, CLI, unavailable, and unsupported contract while adding per-core-metric median, deterministic bootstrap 95% confidence interval, coefficient of variation, and Mann-Whitney U significance against the matching C++ baseline. — source: user-request, resolved-decisions, repository-contract
- scale-and-repeat-policy: Baseline execution defaults to ten repeats and offers small, medium, and large scale presets in both runners; explicit sizing and repeat overrides remain available, and the async smoke case continues to default to one repeat. — source: resolved-decisions, repository-contract
- environment-preflight: A read-only, standard-library environment checklist reports whether a machine is suitable for controlled local wall-clock benchmarking without exposing host identity, absolute paths, environment values, secrets, or runtime endpoints. — source: user-request, resolved-decisions, risk-boundary
- baseline-comparison: A standard-library baseline-versus-candidate comparator consumes supported stored runner reports, matches stable metric identities, reports direction-aware median change and two-sided Mann-Whitney U evidence, and can distinguish regression, improvement, inconclusive, insufficient-sample, and incompatible-input outcomes. — source: user-request, resolved-decisions
- stdlib-runtime: Production benchmark runners and tools add no third-party runtime dependency, keep project dependencies empty, and carry auditable unit tests for the bootstrap and Mann-Whitney calculations. — source: resolved-decisions
- contract-only-gate: The repository gate and CI byte-compile all runners and tools, validate deterministic report fixtures and the additive statistical schema, exercise the new tools and fast tests, and never execute a real wall-clock benchmark job on shared CI. — source: resolved-decisions, repository-contract
- synchronized-guidance: Repository README, runner guidance, coverage matrix, benchmark-route guide, and golden-standard specification describe the delivered scales, statistics, tools, report fields, local-only measurement boundary, and fast contract verification accurately. — source: user-request, repository-contract
- preserve-existing-work: Delivery preserves the semantics of all pre-existing modified and untracked files, including the async timeout and checkout-identity handling, README presentation changes, empty packaging-module configuration, and existing workflow structure. — source: user-request, risk-boundary, repository-observation

## Architecture
Summary: Two disjoint Tasks form one parallel frontier: the survey is a self-contained evidence artifact, while all code, schema, fixture, documentation-sync, gate, and CI work stays in one integrated capability Task because the gate consumes the runner and tool contracts.
Notes:
- The capability Task begins with four write-disjoint branches for the async runner, native-C++ runner, environment preflight, and report comparator; documentation synchronization and gate wiring each join all four branches, then focused verification joins both integration branches.
- Statistical helpers remain local to each runner, and the comparator owns its own standard-library implementation; no shared statistics module or third-party dependency is introduced.
- Bootstrap intervals use a local fixed-seed pseudo-random generator, 10000 percentile resamples of the median, and inclusive 2.5th and 97.5th percentile bounds; one sample yields a degenerate interval and an unavailable CV rather than fabricated variability.
- CV is sample standard deviation divided by the arithmetic mean for at least two non-negative timing or throughput samples; zero mean or fewer than two samples yields null with an explicit insufficient-sample status.
- Mann-Whitney U uses average ranks, a two-sided normal approximation with tie correction and continuity correction, O((n+m) log(n+m)) ranking, alpha 0.05, and null significance fields when either side has fewer than two samples or zero variance makes inference undefined.
- Async statistics cover sleep sequential milliseconds, sleep parallel milliseconds, per-sample speedup, no-op total microseconds, and no-op microseconds per task; every non-C++ runtime is compared with the C++ stackless-coroutine samples, while the C++ record is marked as the baseline.
- Native-C++ statistics cover elapsed seconds and the derived throughput samples for each case and route; each measurable Styio implementation is compared with the native_cpp elapsed samples in the same case and route, while unsupported routes remain explicitly not comparable.
- Existing scalar fields and CSV columns retain their names and meanings. New JSON statistic and C++-comparison objects, CSV suffix fields, and Markdown columns are additive; historical report fixtures remain readable without being rewritten.
- The comparator supports the two repository runner report shapes through direct normalization functions. Its defaults are alpha 0.05 and a 5 percent practical threshold; positive normalized change always means regression, and only a threshold-crossing change with p below alpha is classified as regression or improvement.
- The environment checklist is observational only. Platform-specific checks degrade to unavailable, never mutate power, affinity, scheduler, or kernel settings, and return zero in advisory mode; an explicit strict flag returns nonzero only for actionable warnings.
- CI retains the existing smoke then golden-standard job sequence. Smoke only byte-compiles; golden-standard validates fixtures and runs fast contract tests and tool smoke checks. No scheduled, self-hosted, disabled, or shared-runner wall-clock benchmark job is added.
- The survey Task does not edit or require any capability-owned file, and the capability Task does not link to or validate the survey artifact during focused acceptance; their only integration point is the final full regression after both Tasks finish.

## Task: industry-benchmark-survey
Outcome: A repository-local, source-attributed survey gives maintainers a decision-ready benchmarking standard and maps its findings to the five authorized capability gaps without depending on implementation completion.
Scope in:
- Industry practices for programming-language and runtime microbenchmarks, sampling, warmup, nonparametric inference, environment control, reproducible reports, scale sweeps, baseline comparison, and CI placement.
- A factual current-state and gap mapping for this repository, framed as findings and recommendations rather than a claim that concurrent implementation is already complete.
Scope out:
- Production runner or tool behavior.
- Rewriting external sources or embedding raw benchmark measurements.
Outputs:
- benchmarking-survey: Industry benchmarking survey — artifact: docs/BENCHMARKING-SURVEY.md — guarantee: Maintainers can trace each recommendation to an attributed primary or authoritative source and to one of the repository capability gaps.
Owns:
- docs/BENCHMARKING-SURVEY.md
Exclusive:
Difficulty: standard
Verification: code
Risks:
- quality
Nodes:
- publish-evidence-survey: Synthesize attributed industry practices, repository observations, recommendations, and the five-item gap mapping into the survey artifact.
- validate-survey-contract: Verify required topics, explicit local-versus-CI boundary, source links, and gap mapping without judging implementation state. — after: publish-evidence-survey
Requirements:
- survey-evidence
Design:
approach:
- Organize the report around methodology, sampling and warmup, median and bootstrap confidence intervals, CV interpretation, Mann-Whitney U and practical effect thresholds, environment isolation, small-medium-large scaling, report reproducibility, baseline comparison, and contract-only CI.
- Prefer primary project documentation or papers for established tools such as Go benchmark and benchstat, Criterion.rs, Python pyperf, Google Benchmark, and comparable compiler/runtime benchmark guidance; paraphrase findings and attach direct links instead of copying long passages.
- Separate sourced industry facts, repository observations, and recommendations so a reader can tell evidence from local policy.
- Map the conclusions explicitly to report landing, both runner upgrades, the environment checklist, the baseline comparator, and gate/CI wiring while describing delivery status neutrally.
privacy:
- Do not include machine identity, local paths, raw environment values, runtime endpoints, credentials, or backend execution data.
Acceptance:
- Given: The repository capabilities and authoritative benchmarking references — When: the survey document is validated — Then: it contains distinct methodology, statistics, environment, scale, comparison, reproducibility, CI-boundary, and repository-gap sections plus attributable direct references — Oracle: a UTF-8 document contract check finds every required topic heading, all five capability mappings, an explicit statement that shared CI does not run wall-clock benchmarks, and at least five distinct direct reference links — Evidence: command: survey document contract — Covers: survey-evidence, benchmarking-survey
Regression:
Commands:
- python3 -c "from pathlib import Path; text=Path('docs/BENCHMARKING-SURVEY.md').read_text(encoding='utf-8'); topics=('methodology','statistics','environment','scale','baseline','reproducibility','CI','async-runtime','native-cpp'); assert all(topic.lower() in text.lower() for topic in topics); assert text.count('](') >= 5"
Paths:
- docs/BENCHMARKING-SURVEY.md

## Task: integrated-benchmark-capabilities
Outcome: Both runners, both new tools, their additive report contracts, synchronized guidance, and the contract-only gate operate as one verified standard-library benchmark capability without changing the semantics of existing work.
Scope in:
- Async and native-C++ runner statistics, scale presets, report serialization, summaries, focused tests, and runner documentation.
- Read-only environment preflight and baseline-versus-candidate comparison CLIs with deterministic fixtures and tests.
- Root and benchmark documentation synchronization, package test discovery, golden gate behavior, and the existing two-job CI workflow.
- Preservation and integration of existing modified and untracked content in every owned path.
Scope out:
- A shared statistics package or third-party numerical dependency.
- Real benchmark execution on shared CI, a new CI benchmark job, new runtime integrations, Styio source changes, or web-service benchmarks.
- Rewriting historical report fixture contents merely to satisfy the additive schema.
Outputs:
- async-statistical-runner: Additive async runner contract — artifact: async-runtime/run-async-bench.py — guarantee: Baseline runs default to ten samples with three scale presets, smoke defaults to one, and every successful core metric carries deterministic uncertainty and C++ significance data while legacy fields remain usable.
- native-statistical-runner: Additive native-C++ runner contract — artifact: native-cpp/run-native-cpp-bench.py — guarantee: Three input scales, ten default repeats, per-record uncertainty, and matched native-C++ significance are emitted without changing unsupported route semantics.
- environment-checklist: Controlled-benchmark preflight — artifact: tools/benchmark-env-check.py — guarantee: Text and JSON modes report sanitized pass, warn, and unavailable checks without mutating the host, with optional strict failure for local enforcement.
- baseline-diff: Baseline comparison CLI — artifact: tools/benchmark-compare.py — guarantee: Supported async and native report pairs produce deterministic JSON and Markdown comparisons with stable metric keys, direction-aware effect, Mann-Whitney evidence, and opt-in regression failure.
- capability-contract-suite: Fast capability tests and fixtures — artifact: tests/fixtures/benchmark-capabilities — guarantee: Pure synthetic samples and privacy-safe fixtures prove exact statistics, edge cases, additive schemas, comparator classifications, tool exit behavior, dependency policy, and CI boundaries without measuring wall-clock performance.
- contract-only-delivery-gate: Integrated repository gate — artifact: scripts/benchmark-golden-gate.py — guarantee: The golden gate byte-compiles all Python sources, validates old and new fixture shapes, runs fast tests and tool smoke contracts, retains explicit external-checkout skip behavior, and does not launch a shared-CI performance run.
- synchronized-benchmark-guidance: Updated benchmark capability guidance — artifact: docs/COVERAGE-MATRIX.md — guarantee: All repository-facing documentation consistently explains the statistics, presets, tools, report compatibility, controlled-local measurement policy, and contract-only CI route.
Owns:
- README.md
- async-runtime/run-async-bench.py
- async-runtime/test_async_runtime_blackbox.py
- async-runtime/test_async_runtime_statistics.py
- async-runtime/README.md
- native-cpp/run-native-cpp-bench.py
- native-cpp/test_native_cpp_bench.py
- native-cpp/README.md
- tools/benchmark-env-check.py
- tools/benchmark-compare.py
- tools/test_benchmark_tools.py
- tests/fixtures/benchmark-capabilities
- tests/test_benchmark_capability_contract.py
- scripts/benchmark-golden-gate.py
- pyproject.toml
- .github/workflows/tests.yml
- docs/COVERAGE-MATRIX.md
- docs/IN_TREE_BENCHMARKS.md
- docs/specs/GOLDEN-STANDARD-TEST-SUITE.md
Exclusive:
- benchmark report schema and contract gate
Difficulty: complex
Verification: code
Risks:
- performance
- public_interface
- schema
- quality
Nodes:
- upgrade-async-runner: Add local nonparametric statistics, C++ significance, scale resolution, additive reports, deterministic unit coverage, and async guidance while preserving the existing timeout and checkout-identity fixes.
- upgrade-native-runner: Add local nonparametric statistics, native-C++ significance, scale resolution, additive reports, deterministic unit coverage, and native runner guidance without changing unsupported routes.
- deliver-environment-preflight: Implement the sanitized read-only checklist, advisory and strict exit semantics, text and JSON renderers, and injected-probe tests.
- deliver-report-comparator: Implement both report normalizers, stable metric matching, direction-aware classifications, JSON and Markdown output, opt-in gate exit semantics, and synthetic report fixtures.
- synchronize-capability-guidance: Update root guidance, coverage matrix, benchmark route guide, and golden-standard contract after every public runner and tool interface is fixed. — after: upgrade-async-runner, upgrade-native-runner, deliver-environment-preflight, deliver-report-comparator
- wire-contract-only-gate: Integrate test discovery, fixture validation, byte-compilation, tool smoke, external-checkout skip behavior, dependency assertions, and the existing two-job workflow after all implementation branches are available. — after: upgrade-async-runner, upgrade-native-runner, deliver-environment-preflight, deliver-report-comparator
- prove-integrated-capabilities: Run the focused deterministic suite and correct every in-scope contract, compatibility, privacy, statistics, and CI-wiring defect. — after: synchronize-capability-guidance, wire-contract-only-gate
Requirements:
- additive-runner-statistics
- scale-and-repeat-policy
- environment-preflight
- baseline-comparison
- stdlib-runtime
- contract-only-gate
- synchronized-guidance
- preserve-existing-work
Design:
statistical-contract:
- Each runner defines and tests its own median bootstrap, CV, average-rank U, tie-corrected two-sided p-value, and sample-quality helpers; the comparator implements the same documented math locally rather than importing either runner.
- Use a fixed local random seed and 10000 bootstrap resamples so identical samples serialize identically and tests do not depend on process-global random state.
- Statistic objects contain sample_count, median, ci95_low, ci95_high, cv, and quality. Comparison objects contain baseline identity, u, p_value, alpha, significant, direction, and status; unavailable values serialize as JSON null, never NaN or infinity.
- Tests include odd and even medians, constant samples, tied ranks, separated samples, reversed samples, one-sample smoke, zero-mean CV, missing baselines, unsupported records, and known U/p-value tolerances.
async-contract:
- Preserve all existing numeric summary fields, raw successful samples, relative-performance fields, unavailable records, runtime selection, required-runtime failure behavior, and output filenames.
- Add statistics beneath each sleep and no-op workload and a C++ comparison block per non-C++ runtime; append flattened CSV statistic and significance rows and display interval, CV, and significance status in Markdown without deleting existing rows or sections.
- Baseline defaults to ten repeats. Small, medium, and large presets resolve to bounded tasks, sleep duration, no-op fanout, and workers before explicit sizing flags override them; the medium preset preserves the existing baseline sizes, and smoke preserves its existing sizes and one-repeat default.
native-contract:
- Preserve existing metadata, result fields, samples, validation-before-timing behavior, relative normalization, unsupported cached-jit record, output filenames, and command overrides.
- Add scale metadata and small, medium, and large line-count presets with the existing 48-byte line shape; medium preserves 100000 lines, explicit line count and line bytes override presets, and default repeats becomes ten.
- Add elapsed and throughput statistic objects plus the matched native_cpp comparison to JSON, append scalar fields to CSV, and add interval, CV, and significance columns to Markdown; native_cpp is labeled baseline and unsupported records are labeled not comparable.
environment-interface:
- Provide format selection for human text or JSON and an explicit strict option. Advisory mode returns zero after a valid report even when checks warn; strict mode returns one for actionable warnings, and malformed arguments or internal contract failures return two.
- Use best-effort standard-library probes for logical CPU availability and affinity, background load relative to available CPUs, CPU frequency or governor stability where exposed, turbo or boost consistency where exposed, power-source stability where exposed, and platform support. Unsupported probes are unavailable rather than failures.
- JSON contains a version, generated-at timestamp, summary counts, and ordered checks with id, status, observed category, and recommendation. It excludes hostname, user name, environment values, command lines, filesystem source paths, and raw device identifiers.
comparator-interface:
- Require baseline and candidate results.json inputs; accept optional JSON and Markdown output paths, labels, alpha, practical regression threshold percent, and fail-on-regression. Never echo input absolute paths into outputs.
- Normalize async sample metrics by runtime key, workload, and metric and native samples by case, route, implementation, and elapsed metric. Report incompatible schema, missing comparable keys, and insufficient raw samples explicitly instead of inferring significance from aggregates.
- Positive regression_percent always means worse after metric direction is applied. Classification is regression or improvement only when absolute practical change meets the default 5 percent threshold and p_value is below alpha 0.05; otherwise it is inconclusive, with insufficient_sample and incompatible_input reserved for those conditions.
- Exit zero for a valid comparison in report mode, one only when fail-on-regression is requested and at least one regression exists, and two for invalid input or schema.
gate-and-ci:
- Fast tests load runner modules without executing external toolchains, use deterministic samples and committed privacy-safe fixtures, run both new tool CLIs as subprocess smoke checks, assert production dependencies remain empty, and inspect workflow commands to prove no wall-clock benchmark job is present.
- Golden gate retains historical report readability checks, validates the new synthetic schema rather than rewriting historical fixtures, executes all fast capability tests, and retains the existing async black-box pytest whose missing external checkout is an explicit skip.
- Workflow keeps test-smoke and test-golden-standard with their current ordering and permissions; test-smoke byte-compiles both new tools and test-golden-standard invokes only the repository golden gate.
compatibility-and-preservation:
- Merge around the existing uncommitted README styling, async timeout conversion and same-checkout logic, empty setuptools module declaration, and untracked workflow and planning files; do not revert or replace those semantics.
- Do not persist machine-specific paths, identities, environment values, secrets, runtime endpoints, or backend execution data in new fixtures, tool outputs, tests, documentation, or gate diagnostics.
patterns:
- pattern_catalog refactoring-guru-catalog-22-v1; candidate Strategy; decision reject; pressure two fixed report shapes require normalization; expected_benefit no positive net benefit for a plugin hierarchy; simpler_alternative two direct normalizer functions and a shared internal record are sufficient; application keep schema dispatch at the comparator boundary and prove both shapes with fixtures; costs_and_rejections a strategy registry would add indirection and unsupported extension surface without a current third schema.
Acceptance:
- Given: Synthetic async samples for successful, tied, constant, missing-baseline, and one-repeat cases — When: the async statistics and report tests run — Then: legacy fields retain their values, every core metric has deterministic valid bounds and CV quality, C++ comparisons have correct statuses, baseline defaults to ten, all three scales resolve, and smoke defaults to one — Oracle: the focused async pytest module exits zero and asserts exact schema keys, numeric invariants, known U tolerances, CLI precedence, CSV rows, and Markdown markers — Evidence: command: async statistics contract tests — Covers: additive-runner-statistics, scale-and-repeat-policy, stdlib-runtime, preserve-existing-work, async-statistical-runner
- Given: Synthetic native elapsed samples and supported and unsupported route records — When: the native runner contract tests run — Then: additive elapsed and throughput statistics, matched native_cpp significance, scale precedence, ten-repeat default, legacy record fields, and unsupported semantics are all preserved — Oracle: the focused native pytest module exits zero and asserts exact JSON and CSV additions, summary markers, numeric invariants, scale resolution, comparison status, and unchanged unsupported output — Evidence: command: native runner contract tests — Covers: additive-runner-statistics, scale-and-repeat-policy, stdlib-runtime, preserve-existing-work, native-statistical-runner
- Given: Controlled fake environment probes and deterministic async and native report pairs — When: both new tool test suites and subprocess CLIs run — Then: the checklist is sanitized and non-mutating, the comparator matches only stable metrics and produces every classification, defaults and exit codes are exact, and neither tool requires a third-party package — Oracle: the focused tool pytest module exits zero, recursive privacy assertions find no forbidden identity or path fields, known sample pairs meet U/p-value tolerances, and subprocess return codes equal the documented advisory, strict, report, regression, and invalid-input contracts — Evidence: command: benchmark tool contract tests — Covers: environment-preflight, baseline-comparison, stdlib-runtime, environment-checklist, baseline-diff
- Given: The four capability branches and their deterministic fixtures — When: the repository integration contract tests inspect package configuration, documentation, golden gate, and workflow and invoke fixture validation — Then: dependencies remain empty, all guidance agrees, every new source is byte-compilable, the gate covers new fields and tools, the existing two CI jobs remain ordered, and no shared-CI wall-clock benchmark command or job exists — Oracle: the focused integration pytest module exits zero and checks exact required files, commands, documentation markers, dependency list, fixture schema, job names and dependency, and forbidden benchmark-job patterns — Evidence: command: capability gate and CI contract tests — Covers: contract-only-gate, synchronized-guidance, preserve-existing-work, capability-contract-suite, contract-only-delivery-gate, synchronized-benchmark-guidance
Regression:
Commands:
- PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q async-runtime/test_async_runtime_statistics.py native-cpp/test_native_cpp_bench.py tools/test_benchmark_tools.py tests/test_benchmark_capability_contract.py
Paths:
- README.md
- async-runtime/run-async-bench.py
- async-runtime/test_async_runtime_blackbox.py
- async-runtime/test_async_runtime_statistics.py
- async-runtime/README.md
- native-cpp/run-native-cpp-bench.py
- native-cpp/test_native_cpp_bench.py
- native-cpp/README.md
- tools/benchmark-env-check.py
- tools/benchmark-compare.py
- tools/test_benchmark_tools.py
- tests/fixtures/benchmark-capabilities
- tests/test_benchmark_capability_contract.py
- scripts/benchmark-golden-gate.py
- pyproject.toml
- .github/workflows/tests.yml
- docs/COVERAGE-MATRIX.md
- docs/IN_TREE_BENCHMARKS.md
- docs/specs/GOLDEN-STANDARD-TEST-SUITE.md

## Full regression
Commands:
- python3 -c "from pathlib import Path; text=Path('docs/BENCHMARKING-SURVEY.md').read_text(encoding='utf-8'); topics=('methodology','statistics','environment','scale','baseline','reproducibility','CI','async-runtime','native-cpp'); assert all(topic.lower() in text.lower() for topic in topics); assert text.count('](') >= 5"
- PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 scripts/benchmark-golden-gate.py
Paths:
- docs/BENCHMARKING-SURVEY.md
- README.md
- async-runtime
- native-cpp
- tools
- tests
- scripts/benchmark-golden-gate.py
- pyproject.toml
- .github/workflows/tests.yml
- docs/COVERAGE-MATRIX.md
- docs/IN_TREE_BENCHMARKS.md
- docs/specs/GOLDEN-STANDARD-TEST-SUITE.md
