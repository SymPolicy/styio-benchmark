# PLAN-001 Benchmark capabilities survey and completion

Phase: completed · Revision: 1

This document is a render-only projection of `Plan.json`. Edit `Plan.json`; never edit this file.

## Intent

**Goal**: 落盘业界编程语言基准评测调研报告到 docs/，并按报告能力逐项补全 styio-benchmark：两个 runner 的统计严谨性升级、环境控制清单工具、baseline 对比比较器、门禁与 CI 接线。

**In scope**
- docs/ 调研报告与覆盖文档更新；async-runtime/ 与 native-cpp/ runner 统计升级及其 README、pytest 契约同步；tools/ 新增环境清单与 baseline diff 比较器（纯标准库）；scripts/benchmark-golden-gate.py、pyproject.toml、.github/workflows/tests.yml 门禁与 CI 接线；保留用户未提交改动与未跟踪文件。

**Out of scope**
- Styio 主仓库任何改动；真实基准 CI job（共享 runner 不跑墙钟基准）；Web 服务型基准（k6/TechEmpower 类）与新增语言运行时接入；非性能相关功能。

**Success**
- docs/BENCHMARKING-SURVEY.md 落盘且覆盖矩阵同步；两个 runner 报告含中位数/bootstrapped CI/CV/显著性字段且旧契约字段保持兼容，pytest 黑盒契约全绿；环境清单工具与 baseline 比较器可用并有测试；golden gate 全绿且 CI 接线完成。

**Risk boundary**
- 不改动用户未提交改动语义；不引入第三方运行时依赖（除非 DEC-4 选择允许）；状态与产物不含本机绝对路径、密钥或运行端点；真实基准仅本地执行。

## Decisions

Dossier status: resolved

### Q-001 本次交付包含哪些能力项？

Context: 调研报告列出了多项可补全能力：统计严谨性升级（CI/CV/显著性）、环境控制清单、baseline diff 比较器、门禁与 CI 接线、覆盖文档同步。你要求“逐项补全”，但本次交付的边界需要一次确定。

Resolution: all (user selection)

- [x] `all` 全部五项（推荐）：报告落盘 + 两 runner 统计升级 + 环境清单工具 + baseline 比较器 + 门禁/CI 接线
  - docs/ 落盘调研报告并同步覆盖矩阵
  - async-runtime 与 native-cpp runner 增加中位数/bootstrapped CI/CV/显著性字段
  - tools/ 新增环境控制清单工具与 baseline-vs-head 比较器
  - scripts/benchmark-golden-gate.py 与 CI 接线更新，覆盖新工具 smoke
  - 全部变更在本次交付内闭环并接受门禁
- [ ] `stats-report` 仅统计升级 + 报告落盘
  - docs/ 落盘调研报告
  - async-runtime 与 native-cpp runner 统计升级
  - 环境清单、比较器、门禁接线留待后续交付
- [ ] `report-only` 仅报告落盘与覆盖文档
  - docs/ 落盘调研报告并同步覆盖矩阵
  - runner 与工具代码保持不变

### Q-002 报告统计采用哪套口径？

Context: 调研结论：median-only 不足以支撑性能结论；业界主流是非参数路线（benchstat 的 Mann-Whitney U、Criterion.rs 的 bootstrap CI），配合 CV 与效应量。

Resolution: nonparametric (user selection)

- [x] `nonparametric` 非参数路线（推荐）：中位数 + bootstrap 95% CI + CV + Mann-Whitney U 显著性（对 C++ 基线）
  - 两个 runner 对每个核心指标输出 median、95% bootstrap CI、CV
  - 相对 C++ 基线的 A/B 显著性用 Mann-Whitney U 检验
  - 报告契约字段向后兼容（仅新增字段）
  - 统计函数在各自 Task 内实现，跨 runner 不共享代码
- [ ] `parametric` 参数路线：均值 ± 标准差 + 95% t 置信区间 + 双样本 t 检验
  - 输出 mean/stddev/t-CI/t 检验
  - 假设样本近似正态，对离群与多峰分布更敏感
- [ ] `status-quo` 维持现状：仅中位数，不增加统计字段
  - runner 报告不变
  - 仅文档与工具类能力进入交付

### Q-003 基准运行规模与默认重复次数如何处理？

Context: 当前 repeats=5；调研建议独立样本 ≥10（理想 20-30）。仓库 COVERAGE-MATRIX 已把 small/medium/large 规模档列为缺失项，报告也强调规模维度。

Resolution: scale-10 (user selection)

- [x] `scale-10` 默认 repeats=10 且新增规模 sweep（推荐）：runner 支持 --scale small/medium/large 预设
  - baseline 用例默认重复 10 次（可 --repeats 覆盖）
  - async-runtime 与 native-cpp 各增 small/medium/large 三档规模预设
  - smoke 用例保持 repeats=1 以控制 CI 时长
- [ ] `repeats-only-10` 仅默认 repeats=10，不做规模 sweep
  - 默认重复提高到 10
  - 规模 sweep 留待后续
- [ ] `keep-5` 维持 repeats=5，仅加统计字段
  - 运行时长不变
  - 样本量不足以支撑 <10% 级别的显著性结论

### Q-004 统计与比较器实现允许什么依赖？

Context: 仓库当前 dependencies=[]，仅 pytest 测试 extra；bootstrap 与 Mann-Whitney U 可用纯标准库手写，也可引入 scipy。

Resolution: stdlib (user selection)

- [x] `stdlib` 纯标准库零新增依赖（推荐）：手写 bootstrap CI 与 Mann-Whitney U，含单元测试
  - pyproject.toml 的 dependencies 保持为空
  - 统计函数自带单元测试，行为可审计
  - 实现量略增但无供应链风险
- [ ] `scipy` 允许 scipy 作为可选 extra
  - 统计计算委托 scipy（bootstrap/mannwhitneyu）
  - 新增可选依赖，基准运行需安装 extra

### Q-005 CI 中真实基准如何处置？

Context: 调研结论：共享 runner 上墙钟差异不可信（±10-20%），真实基准应留在受控本地。现有 test/smoke + test/golden-standard 只做契约校验，无自托管 runner。

Resolution: contract-only (user selection)

- [x] `contract-only` CI 保持契约/门禁（推荐）：golden gate 增加新工具 smoke 与新字段校验，真实基准仅本地
  - test/smoke 与 test/golden-standard 延续，新增新工具 byte-compile 与 fixture 校验
  - CI 不运行墙钟基准
  - golden gate 校验新报告字段存在性与统计合理性
- [ ] `self-hosted` CI 增加真实基准 job 骨架（要求自托管 runner 就绪后启用）
  - 新增 workflow job 定义真实基准，默认 disabled 直到自托管 runner 注册
  - golden gate 行为与 contract-only 相同

### Observed repository facts

- none

## Requirements

| Code | Statement | Sources |
| --- | --- | --- |
| REQ-001 | Publish an industry benchmarking survey that explains measurement discipline, statistical inference, environment control, scale selection, baseline comparison, reproducibility, and the boundary between controlled local benchmarks and contract-only CI, with attributable primary references and a repository gap mapping. | user-request, resolved-decisions |
| REQ-002 | Both benchmark runners retain every existing JSON, CSV, Markdown, CLI, unavailable, and unsupported contract while adding per-core-metric median, deterministic bootstrap 95% confidence interval, coefficient of variation, and Mann-Whitney U significance against the matching C++ baseline. | user-request, resolved-decisions, repository-contract |
| REQ-003 | Baseline execution defaults to ten repeats and offers small, medium, and large scale presets in both runners; explicit sizing and repeat overrides remain available, and the async smoke case continues to default to one repeat. | resolved-decisions, repository-contract |
| REQ-004 | A read-only, standard-library environment checklist reports whether a machine is suitable for controlled local wall-clock benchmarking without exposing host identity, absolute paths, environment values, secrets, or runtime endpoints. | user-request, resolved-decisions, risk-boundary |
| REQ-005 | A standard-library baseline-versus-candidate comparator consumes supported stored runner reports, matches stable metric identities, reports direction-aware median change and two-sided Mann-Whitney U evidence, and can distinguish regression, improvement, inconclusive, insufficient-sample, and incompatible-input outcomes. | user-request, resolved-decisions |
| REQ-006 | Production benchmark runners and tools add no third-party runtime dependency, keep project dependencies empty, and carry auditable unit tests for the bootstrap and Mann-Whitney calculations. | resolved-decisions |
| REQ-007 | The repository gate and CI byte-compile all runners and tools, validate deterministic report fixtures and the additive statistical schema, exercise the new tools and fast tests, and never execute a real wall-clock benchmark job on shared CI. | resolved-decisions, repository-contract |
| REQ-008 | Repository README, runner guidance, coverage matrix, benchmark-route guide, and golden-standard specification describe the delivered scales, statistics, tools, report fields, local-only measurement boundary, and fast contract verification accurately. | user-request, repository-contract |
| REQ-009 | Delivery preserves the semantics of all pre-existing modified and untracked files, including the async timeout and checkout-identity handling, README presentation changes, empty packaging-module configuration, and existing workflow structure. | user-request, risk-boundary, repository-observation |

## Architecture

Two disjoint Tasks form one parallel frontier: the survey is a self-contained evidence artifact, while all code, schema, fixture, documentation-sync, gate, and CI work stays in one integrated capability Task because the gate consumes the runner and tool contracts.

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

## Tasks

Every Task belongs to the same mutually independent parallel frontier.

### TASK-001 industry-benchmark-survey

Tier: standard · Verification: code · Frontier: parallel

Outcome: A repository-local, source-attributed survey gives maintainers a decision-ready benchmarking standard and maps its findings to the five authorized capability gaps without depending on implementation completion.

Risks: quality
Requirements: REQ-001
Writes: docs/BENCHMARKING-SURVEY.md

In scope
- Industry practices for programming-language and runtime microbenchmarks, sampling, warmup, nonparametric inference, environment control, reproducible reports, scale sweeps, baseline comparison, and CI placement.
- A factual current-state and gap mapping for this repository, framed as findings and recommendations rather than a claim that concurrent implementation is already complete.

Out of scope
- Production runner or tool behavior.
- Rewriting external sources or embedding raw benchmark measurements.

Outputs
- OUT-001 Industry benchmarking survey (`docs/BENCHMARKING-SURVEY.md`): Maintainers can trace each recommendation to an attributed primary or authoritative source and to one of the repository capability gaps.

Internal Node graph
- NODE-001 publish-evidence-survey · after: none · Synthesize attributed industry practices, repository observations, recommendations, and the five-item gap mapping into the survey artifact.
- NODE-002 validate-survey-contract · after: NODE-001 · Verify required topics, explicit local-versus-CI boundary, source links, and gap mapping without judging implementation state.

Design
- approach
  - Organize the report around methodology, sampling and warmup, median and bootstrap confidence intervals, CV interpretation, Mann-Whitney U and practical effect thresholds, environment isolation, small-medium-large scaling, report reproducibility, baseline comparison, and contract-only CI.
  - Prefer primary project documentation or papers for established tools such as Go benchmark and benchstat, Criterion.rs, Python pyperf, Google Benchmark, and comparable compiler/runtime benchmark guidance; paraphrase findings and attach direct links instead of copying long passages.
  - Separate sourced industry facts, repository observations, and recommendations so a reader can tell evidence from local policy.
  - Map the conclusions explicitly to report landing, both runner upgrades, the environment checklist, the baseline comparator, and gate/CI wiring while describing delivery status neutrally.
- privacy
  - Do not include machine identity, local paths, raw environment values, runtime endpoints, credentials, or backend execution data.

Acceptance
- AC-001 covers REQ-001, OUT-001
  - Given The repository capabilities and authoritative benchmarking references
  - When the survey document is validated
  - Then it contains distinct methodology, statistics, environment, scale, comparison, reproducibility, CI-boundary, and repository-gap sections plus attributable direct references
  - Oracle: a UTF-8 document contract check finds every required topic heading, all five capability mappings, an explicit statement that shared CI does not run wall-clock benchmarks, and at least five distinct direct reference links
  - Evidence: command from survey document contract

Focused regression
- `python3 -c "from pathlib import Path; text=Path('docs/BENCHMARKING-SURVEY.md').read_text(encoding='utf-8'); topics=('methodology','statistics','environment','scale','baseline','reproducibility','CI','async-runtime','native-cpp'); assert all(topic.lower() in text.lower() for topic in topics); assert text.count('](') >= 5"`
- paths: docs/BENCHMARKING-SURVEY.md

### TASK-002 integrated-benchmark-capabilities

Tier: complex · Verification: code · Frontier: parallel

Outcome: Both runners, both new tools, their additive report contracts, synchronized guidance, and the contract-only gate operate as one verified standard-library benchmark capability without changing the semantics of existing work.

Risks: performance, public_interface, schema, quality
Requirements: REQ-002, REQ-003, REQ-004, REQ-005, REQ-006, REQ-007, REQ-008, REQ-009
Writes: README.md, async-runtime/run-async-bench.py, async-runtime/test_async_runtime_blackbox.py, async-runtime/test_async_runtime_statistics.py, async-runtime/README.md, native-cpp/run-native-cpp-bench.py, native-cpp/test_native_cpp_bench.py, native-cpp/README.md, tools/benchmark-env-check.py, tools/benchmark-compare.py, tools/test_benchmark_tools.py, tests/fixtures/benchmark-capabilities, tests/test_benchmark_capability_contract.py, scripts/benchmark-golden-gate.py, pyproject.toml, .github/workflows/tests.yml, docs/COVERAGE-MATRIX.md, docs/IN_TREE_BENCHMARKS.md, docs/specs/GOLDEN-STANDARD-TEST-SUITE.md
Exclusive resources: benchmark report schema and contract gate

In scope
- Async and native-C++ runner statistics, scale presets, report serialization, summaries, focused tests, and runner documentation.
- Read-only environment preflight and baseline-versus-candidate comparison CLIs with deterministic fixtures and tests.
- Root and benchmark documentation synchronization, package test discovery, golden gate behavior, and the existing two-job CI workflow.
- Preservation and integration of existing modified and untracked content in every owned path.

Out of scope
- A shared statistics package or third-party numerical dependency.
- Real benchmark execution on shared CI, a new CI benchmark job, new runtime integrations, Styio source changes, or web-service benchmarks.
- Rewriting historical report fixture contents merely to satisfy the additive schema.

Outputs
- OUT-002 Additive async runner contract (`async-runtime/run-async-bench.py`): Baseline runs default to ten samples with three scale presets, smoke defaults to one, and every successful core metric carries deterministic uncertainty and C++ significance data while legacy fields remain usable.
- OUT-003 Additive native-C++ runner contract (`native-cpp/run-native-cpp-bench.py`): Three input scales, ten default repeats, per-record uncertainty, and matched native-C++ significance are emitted without changing unsupported route semantics.
- OUT-004 Controlled-benchmark preflight (`tools/benchmark-env-check.py`): Text and JSON modes report sanitized pass, warn, and unavailable checks without mutating the host, with optional strict failure for local enforcement.
- OUT-005 Baseline comparison CLI (`tools/benchmark-compare.py`): Supported async and native report pairs produce deterministic JSON and Markdown comparisons with stable metric keys, direction-aware effect, Mann-Whitney evidence, and opt-in regression failure.
- OUT-006 Fast capability tests and fixtures (`tests/fixtures/benchmark-capabilities`): Pure synthetic samples and privacy-safe fixtures prove exact statistics, edge cases, additive schemas, comparator classifications, tool exit behavior, dependency policy, and CI boundaries without measuring wall-clock performance.
- OUT-007 Integrated repository gate (`scripts/benchmark-golden-gate.py`): The golden gate byte-compiles all Python sources, validates old and new fixture shapes, runs fast tests and tool smoke contracts, retains explicit external-checkout skip behavior, and does not launch a shared-CI performance run.
- OUT-008 Updated benchmark capability guidance (`docs/COVERAGE-MATRIX.md`): All repository-facing documentation consistently explains the statistics, presets, tools, report compatibility, controlled-local measurement policy, and contract-only CI route.

Internal Node graph
- NODE-003 upgrade-async-runner · after: none · Add local nonparametric statistics, C++ significance, scale resolution, additive reports, deterministic unit coverage, and async guidance while preserving the existing timeout and checkout-identity fixes.
- NODE-004 upgrade-native-runner · after: none · Add local nonparametric statistics, native-C++ significance, scale resolution, additive reports, deterministic unit coverage, and native runner guidance without changing unsupported routes.
- NODE-005 deliver-environment-preflight · after: none · Implement the sanitized read-only checklist, advisory and strict exit semantics, text and JSON renderers, and injected-probe tests.
- NODE-006 deliver-report-comparator · after: none · Implement both report normalizers, stable metric matching, direction-aware classifications, JSON and Markdown output, opt-in gate exit semantics, and synthetic report fixtures.
- NODE-007 synchronize-capability-guidance · after: NODE-003, NODE-004, NODE-005, NODE-006 · Update root guidance, coverage matrix, benchmark route guide, and golden-standard contract after every public runner and tool interface is fixed.
- NODE-008 wire-contract-only-gate · after: NODE-003, NODE-004, NODE-005, NODE-006 · Integrate test discovery, fixture validation, byte-compilation, tool smoke, external-checkout skip behavior, dependency assertions, and the existing two-job workflow after all implementation branches are available.
- NODE-009 prove-integrated-capabilities · after: NODE-007, NODE-008 · Run the focused deterministic suite and correct every in-scope contract, compatibility, privacy, statistics, and CI-wiring defect.

Design
- async-contract
  - Preserve all existing numeric summary fields, raw successful samples, relative-performance fields, unavailable records, runtime selection, required-runtime failure behavior, and output filenames.
  - Add statistics beneath each sleep and no-op workload and a C++ comparison block per non-C++ runtime; append flattened CSV statistic and significance rows and display interval, CV, and significance status in Markdown without deleting existing rows or sections.
  - Baseline defaults to ten repeats. Small, medium, and large presets resolve to bounded tasks, sleep duration, no-op fanout, and workers before explicit sizing flags override them; the medium preset preserves the existing baseline sizes, and smoke preserves its existing sizes and one-repeat default.
- comparator-interface
  - Require baseline and candidate results.json inputs; accept optional JSON and Markdown output paths, labels, alpha, practical regression threshold percent, and fail-on-regression. Never echo input absolute paths into outputs.
  - Normalize async sample metrics by runtime key, workload, and metric and native samples by case, route, implementation, and elapsed metric. Report incompatible schema, missing comparable keys, and insufficient raw samples explicitly instead of inferring significance from aggregates.
  - Positive regression_percent always means worse after metric direction is applied. Classification is regression or improvement only when absolute practical change meets the default 5 percent threshold and p_value is below alpha 0.05; otherwise it is inconclusive, with insufficient_sample and incompatible_input reserved for those conditions.
  - Exit zero for a valid comparison in report mode, one only when fail-on-regression is requested and at least one regression exists, and two for invalid input or schema.
- compatibility-and-preservation
  - Merge around the existing uncommitted README styling, async timeout conversion and same-checkout logic, empty setuptools module declaration, and untracked workflow and planning files; do not revert or replace those semantics.
  - Do not persist machine-specific paths, identities, environment values, secrets, runtime endpoints, or backend execution data in new fixtures, tool outputs, tests, documentation, or gate diagnostics.
- environment-interface
  - Provide format selection for human text or JSON and an explicit strict option. Advisory mode returns zero after a valid report even when checks warn; strict mode returns one for actionable warnings, and malformed arguments or internal contract failures return two.
  - Use best-effort standard-library probes for logical CPU availability and affinity, background load relative to available CPUs, CPU frequency or governor stability where exposed, turbo or boost consistency where exposed, power-source stability where exposed, and platform support. Unsupported probes are unavailable rather than failures.
  - JSON contains a version, generated-at timestamp, summary counts, and ordered checks with id, status, observed category, and recommendation. It excludes hostname, user name, environment values, command lines, filesystem source paths, and raw device identifiers.
- gate-and-ci
  - Fast tests load runner modules without executing external toolchains, use deterministic samples and committed privacy-safe fixtures, run both new tool CLIs as subprocess smoke checks, assert production dependencies remain empty, and inspect workflow commands to prove no wall-clock benchmark job is present.
  - Golden gate retains historical report readability checks, validates the new synthetic schema rather than rewriting historical fixtures, executes all fast capability tests, and retains the existing async black-box pytest whose missing external checkout is an explicit skip.
  - Workflow keeps test-smoke and test-golden-standard with their current ordering and permissions; test-smoke byte-compiles both new tools and test-golden-standard invokes only the repository golden gate.
- native-contract
  - Preserve existing metadata, result fields, samples, validation-before-timing behavior, relative normalization, unsupported cached-jit record, output filenames, and command overrides.
  - Add scale metadata and small, medium, and large line-count presets with the existing 48-byte line shape; medium preserves 100000 lines, explicit line count and line bytes override presets, and default repeats becomes ten.
  - Add elapsed and throughput statistic objects plus the matched native_cpp comparison to JSON, append scalar fields to CSV, and add interval, CV, and significance columns to Markdown; native_cpp is labeled baseline and unsupported records are labeled not comparable.
- patterns
  - pattern_catalog refactoring-guru-catalog-22-v1; candidate Strategy; decision reject; pressure two fixed report shapes require normalization; expected_benefit no positive net benefit for a plugin hierarchy; simpler_alternative two direct normalizer functions and a shared internal record are sufficient; application keep schema dispatch at the comparator boundary and prove both shapes with fixtures; costs_and_rejections a strategy registry would add indirection and unsupported extension surface without a current third schema.
- statistical-contract
  - Each runner defines and tests its own median bootstrap, CV, average-rank U, tie-corrected two-sided p-value, and sample-quality helpers; the comparator implements the same documented math locally rather than importing either runner.
  - Use a fixed local random seed and 10000 bootstrap resamples so identical samples serialize identically and tests do not depend on process-global random state.
  - Statistic objects contain sample_count, median, ci95_low, ci95_high, cv, and quality. Comparison objects contain baseline identity, u, p_value, alpha, significant, direction, and status; unavailable values serialize as JSON null, never NaN or infinity.
  - Tests include odd and even medians, constant samples, tied ranks, separated samples, reversed samples, one-sample smoke, zero-mean CV, missing baselines, unsupported records, and known U/p-value tolerances.

Acceptance
- AC-002 covers REQ-002, REQ-003, REQ-006, REQ-009, OUT-002
  - Given Synthetic async samples for successful, tied, constant, missing-baseline, and one-repeat cases
  - When the async statistics and report tests run
  - Then legacy fields retain their values, every core metric has deterministic valid bounds and CV quality, C++ comparisons have correct statuses, baseline defaults to ten, all three scales resolve, and smoke defaults to one
  - Oracle: the focused async pytest module exits zero and asserts exact schema keys, numeric invariants, known U tolerances, CLI precedence, CSV rows, and Markdown markers
  - Evidence: command from async statistics contract tests
- AC-003 covers REQ-002, REQ-003, REQ-006, REQ-009, OUT-003
  - Given Synthetic native elapsed samples and supported and unsupported route records
  - When the native runner contract tests run
  - Then additive elapsed and throughput statistics, matched native_cpp significance, scale precedence, ten-repeat default, legacy record fields, and unsupported semantics are all preserved
  - Oracle: the focused native pytest module exits zero and asserts exact JSON and CSV additions, summary markers, numeric invariants, scale resolution, comparison status, and unchanged unsupported output
  - Evidence: command from native runner contract tests
- AC-004 covers REQ-004, REQ-005, REQ-006, OUT-004, OUT-005
  - Given Controlled fake environment probes and deterministic async and native report pairs
  - When both new tool test suites and subprocess CLIs run
  - Then the checklist is sanitized and non-mutating, the comparator matches only stable metrics and produces every classification, defaults and exit codes are exact, and neither tool requires a third-party package
  - Oracle: the focused tool pytest module exits zero, recursive privacy assertions find no forbidden identity or path fields, known sample pairs meet U/p-value tolerances, and subprocess return codes equal the documented advisory, strict, report, regression, and invalid-input contracts
  - Evidence: command from benchmark tool contract tests
- AC-005 covers REQ-007, REQ-008, REQ-009, OUT-006, OUT-007, OUT-008
  - Given The four capability branches and their deterministic fixtures
  - When the repository integration contract tests inspect package configuration, documentation, golden gate, and workflow and invoke fixture validation
  - Then dependencies remain empty, all guidance agrees, every new source is byte-compilable, the gate covers new fields and tools, the existing two CI jobs remain ordered, and no shared-CI wall-clock benchmark command or job exists
  - Oracle: the focused integration pytest module exits zero and checks exact required files, commands, documentation markers, dependency list, fixture schema, job names and dependency, and forbidden benchmark-job patterns
  - Evidence: command from capability gate and CI contract tests

Focused regression
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q async-runtime/test_async_runtime_statistics.py native-cpp/test_native_cpp_bench.py tools/test_benchmark_tools.py tests/test_benchmark_capability_contract.py`
- paths: README.md, async-runtime/run-async-bench.py, async-runtime/test_async_runtime_blackbox.py, async-runtime/test_async_runtime_statistics.py, async-runtime/README.md, native-cpp/run-native-cpp-bench.py, native-cpp/test_native_cpp_bench.py, native-cpp/README.md, tools/benchmark-env-check.py, tools/benchmark-compare.py, tools/test_benchmark_tools.py, tests/fixtures/benchmark-capabilities, tests/test_benchmark_capability_contract.py, scripts/benchmark-golden-gate.py, pyproject.toml, .github/workflows/tests.yml, docs/COVERAGE-MATRIX.md, docs/IN_TREE_BENCHMARKS.md, docs/specs/GOLDEN-STANDARD-TEST-SUITE.md

## Full regression

Run inside the sole Reviewer session after every repair is integrated.

- `python3 -c "from pathlib import Path; text=Path('docs/BENCHMARKING-SURVEY.md').read_text(encoding='utf-8'); topics=('methodology','statistics','environment','scale','baseline','reproducibility','CI','async-runtime','native-cpp'); assert all(topic.lower() in text.lower() for topic in topics); assert text.count('](') >= 5"`
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 scripts/benchmark-golden-gate.py`
- paths: docs/BENCHMARKING-SURVEY.md, README.md, async-runtime, native-cpp, tools, tests, scripts/benchmark-golden-gate.py, pyproject.toml, .github/workflows/tests.yml, docs/COVERAGE-MATRIX.md, docs/IN_TREE_BENCHMARKS.md, docs/specs/GOLDEN-STANDARD-TEST-SUITE.md
