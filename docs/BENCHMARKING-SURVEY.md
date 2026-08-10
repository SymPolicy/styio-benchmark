# Benchmarking Survey 基准评测调研报告

> 范围：面向 styio-benchmark 维护者的业界编程语言 / 运行时微基准实践调研，覆盖测量纪律（methodology）、统计推断（statistics）、环境控制（environment）、规模选择（scale）、基线比较（baseline comparison）、可复现性（reproducibility）与“受控本地测量 + 契约型 CI”边界。
> 定位：本文档是证据与建议文档，不是实现状态声明。各节按「业界实践 / 仓库观察 / 建议」分层，读者可区分外部证据与本地政策；能力项落地状态由交付计划与门禁跟踪，本文档不作完成断言。
> 日期：2026-08-11

## 1. Methodology 方法论与测量纪律

### 1.1 业界实践

微基准（microbenchmark）的核心问题不是“跑多少遍”，而是“测量对象是否可解释”。Go 官方 `testing` 包的基准框架要求实现者显式控制迭代次数，并通过 `b.N` 自适应放大样本，避免单次调用计时开销污染结果 [1]。Google Benchmark 进一步要求区分“被测操作”与“夹具开销”，并提供防编译器优化机制（如 `DoNotOptimize`），防止无副作用的被测代码被消除 [5]。OpenJDK JMH 在 JVM 生态中把“防止 JIT 与死代码消除扭曲测量”当作一等公民，默认启用预热（warmup）与 fork 隔离 [6]。

共同的方法论要点：

- **黑盒优先**：通过进程级 / CLI 级黑盒入口测量，避免在运行时内部插桩改变被测量的执行路径。
- **预热与稳定化**：正式采样前先预热，等待 JIT、缓存、频率与电源状态稳定；Criterion.rs 用户指南明确分离预热与采样阶段 [3]。
- **足够的独立样本**：单次计时包含大量噪声，需要多次独立重复并报告分布，而不是单个数值。
- **离群与抖动**：不预先“删掉难看的点”，而是用对离群稳健的统计量（中位数）与不确定性区间表达。

### 1.2 仓库观察

- `async-runtime/run-async-bench.py` 以黑盒子进程方式对比 Styio task scheduler、C++20 stackless coroutine、Go goroutine、Rust Tokio；固定 `sleep`（阻塞任务并发收敛）与 `noop`（扇出调度开销）两个 workload；`--case smoke` 固定 repeats=1 用于契约检查，`--case baseline` 与 `--case stress` 使用多次重复，报告以中位数为聚合口径。
- `native-cpp/run-native-cpp-bench.py` 以黑盒 CLI 对比 Styio 与手写原生 C++（stdin echo），固定 `full-cli / cached-jit / runtime-only` 三条路线；`cached-jit` 在 Styio 暴露可复用编译/JIT 执行入口前按 `unsupported` 报告，避免占位结果污染对比。
- 两个 runner 当前把“中位数”作为唯一聚合口径，未输出不确定性区间与显著性；`docs/COVERAGE-MATRIX.md` 已把“规模 sweep”与“基准产物自动 diff”列为缺失项。

### 1.3 建议

- 延续黑盒子进程边界，不改为进程内插桩（对应能力缺口 2、4）。
- 在正式采样前加入预热阶段；对 `sleep` 这类含固定延时的 workload，预热轮数应单独配置（对应缺口 2）。
- 把“中位数 + 不确定性区间”作为默认报告口径，并在文档中说明离群处理策略（对应缺口 2）。

## 2. Statistics 统计推断

### 2.1 业界实践

性能样本通常右偏、多峰且含离群，业界主流采用非参数路线：

- **中位数而不是均值**：benchstat 以中位数为聚合值，并给出 A/B 差异的置信区间 [2]；Criterion.rs 默认输出 median 与置信区间 [3]。
- **Bootstrap 置信区间**：Criterion.rs 使用 bootstrap 重采样生成中位数等统计量的置信区间 [3]；pyperf 则报告均值、标准差、中位数、MAD 与分位数，并以稳定性检查辅助判断噪声 [4]。bootstrap 不假设分布形态；重采样次数由精度与分析成本共同决定，本仓库采用已授权的固定种子 10000 次 percentile 重采样，取 2.5% 与 97.5% 分位界。样本只有一个时区间退化，应如实报告而不是编造可变性。
- **变异系数（CV）**：pyperf 的稳定性检查会比较标准差相对均值的比例 [4]；将这一比例显式输出为 CV，能直接表达测量噪声。CV 很小说明测量更稳定，CV 很大说明应增大样本或改进环境隔离。零均值或样本不足时 CV 无定义，应输出 null 与显式 insufficient-sample 语义。
- **Mann-Whitney U 检验**：benchstat 使用 Mann-Whitney U（Wilcoxon 秩和）检验两个样本分布是否显著不同 [2]。常见实现先对两侧合并样本计算平均秩，再使用双侧正态近似，并做 tie correction 与连续性校正，排序复杂度 O((n+m) log(n+m))；任一侧样本少于 2 个或合并样本的秩方差为零（例如全部值相同）时统计推断无定义，应输出 null 而不是伪造结论 [7]。
- **样本量**：benchstat 建议每个 benchmark 至少运行 10 次 [2]；噪声更大或目标效应更小时应增加样本，不能仅凭重复次数宣称小幅差异可信。

### 2.2 仓库观察

- 两个 runner 目前只输出中位数（`repeat_policy: "median"`、`median_s`），没有 CI / CV / 显著性字段。
- `async-runtime` 已有 C++ stackless coroutine 基线的相对比值字段（`sleep_vs_cpp_stackless`、`noop_vs_cpp_stackless`），可作为显著性检验的配对基线；`native-cpp` 以 `native_cpp` 实现为基线，输出 `median_s` 与派生吞吐。
- 仓库依赖保持为空（`pyproject.toml` 的 `dependencies = []`），测试仅依赖 pytest。

### 2.3 建议

- 每个核心指标增加四个字段：median、95% bootstrap CI（固定种子、10000 次 percentile 重采样）、CV、对匹配 C++ 基线的 Mann-Whitney U（u / p_value / alpha=0.05 / significant / direction）。序列化时缺失值用 JSON null，禁用 NaN 与 Infinity（对应缺口 2）。
- 统计函数在各自 runner 内实现（跨 runner 不共享代码），并配套可审计的单元测试（对应缺口 2，且符合零新增依赖约束）。
- 单样本、合并秩方差为零等无法推断的情形输出显式 insufficient-sample / unavailable 语义，而不是编造区间（对应缺口 2、4）。

## 3. Environment 环境控制

### 3.1 业界实践

墙钟测量对运行环境极其敏感。Criterion.rs 与 pyperf 的测量指南都把环境控制列为结果可信的前提 [3][4]：专用或空闲机器、关闭动态调频与 turbo、固定 CPU 亲和性、关闭节能策略、隔离容器或 cgroup、避免后台任务；同时记录机器与工具链指纹用于报告归档。业界普遍做法是“环境清单 + 准入检查”：正式测量前检查关键环境项，不满足时给出 warn/block，而不是静默产出不可比的数字。

### 3.2 仓库观察

- 仓库目前没有环境预检工具；`async-runtime` runner 已有部分内置保护（拒绝非 Release 的 Styio 构建目录，C++ 基线固定 `-O3` 与 Clang），但属于执行期断言，不是独立的准入清单。
- 归档产物已覆盖运行元数据，但环境控制项未系统化。

### 3.3 建议

- 新增只读环境清单工具：检查 CPU 频率策略、当前负载、可用内存、工具链版本等可观测项，输出 pass / warn / unavailable；提供文本与 JSON 两种输出模式；可选 strict 失败模式用于本地准入；工具本身不修改主机状态（对应缺口 3）。
- 输出必须脱敏：不包含主机标识、绝对路径、环境变量原值、密钥或运行端点（对应缺口 3 与隐私边界）。

## 4. Scale 规模选择

### 4.1 业界实践

单一输入规模无法区分“固定开销优化”与“渐进复杂度行为”。主流框架都支持参数化规模：Google Benchmark 提供 `Range` / `Ranges` 参数族 [5]，JMH 提供 `@Param` [6]。业界惯例是至少三档：small（覆盖固定开销与启动路径）、medium（接近日常输入）、large（暴露复杂度与缓存 / 内存行为），且每类模块应有自己的规模参数（输入行数、元素数、嵌套深度、并发数等）。

### 4.2 仓库观察

- `docs/COVERAGE-MATRIX.md` 已定义 `small / medium / large` 三档设想与各模块族的规模参数方向，但两个 runner 目前只有 `--case` 预设与 `--tasks` / `--repeats` 等显式参数，没有规模档预设；“规模 sweep 还没系统化”被列为缺失项。

### 4.3 建议

- 两个 runner 增加 `--scale small|medium|large` 预设（对应缺口 2）；显式 `--tasks` 等覆盖参数优先级高于预设，CLI precedence 需由测试固定。
- baseline 用例默认 repeats=10（可 `--repeats` 覆盖），smoke 用例保持 repeats=1 以控制 CI 时长（对应缺口 2、5）。

## 5. Baseline comparison 基线比较

### 5.1 业界实践

benchstat 定义了一种可复用的 A/B 基线比较模型：输入两份运行产物（baseline 与 candidate），按稳定的指标身份匹配，报告方向感知的中位数变化与 Mann-Whitney U 显著性 [2]。比较结论应能区分：回归（显著变差）、改进（显著变好）、inconclusive（样本充足但统计显著性与实用阈值未同时满足）、insufficient-sample（样本量不足以推断）、incompatible-input（schema 或指标身份无法匹配）。比较器只消费已归档的报告文件，不在比较时重新跑基准。

### 5.2 仓库观察

- 两个 runner 已把 `results.json / benchmarks.csv / summary.md` 落盘归档；`native-cpp` 每条路线按最快实现归一化为 `1.00x`；`docs/COVERAGE-MATRIX.md` 将“基准产物比较缺少自动 diff”列为缺失项。
- 目前没有消费归档报告做 baseline-vs-head 自动比较的工具。

### 5.3 建议

- 新增纯标准库比较器：读取两份支持格式的归档报告，匹配稳定指标键，输出 JSON 与 Markdown 对比；含方向感知中位数变化、Mann-Whitney U 证据与五类判定（regression / improvement / inconclusive / insufficient-sample / incompatible-input）；可选严格失败退出码用于本地门禁（对应缺口 4）。
- 报告格式升级保持向后兼容（只增字段），历史归档无需重写即可被比较器消费（对应缺口 2、4）。

## 6. Reproducibility 可复现性

### 6.1 业界实践

可复现性要求“同一输入与同一环境产生一致可核对的产物”：固定随机种子（bootstrap）、记录工具链与构建配置、归档原始样本而不是只归档聚合值、序列化确定性（键排序、无 NaN/Inf）、以及明确的报告版本。pyperf 与 Criterion.rs 都会把元数据（版本、环境、测量配置）与样本一起归档 [3][4]。

### 6.2 仓库观察

- `async-runtime` 的 `results.json` 已保留成功样本（不只聚合值），报告目录含 `metadata.json`，为确定性统计与复核提供了基础。
- 当前中位数聚合没有固定种子的不确定区间，无法复核“这个差异是否显著”。

### 6.3 建议

- bootstrap 使用固定种子（本地伪随机生成器），保证同一组样本的 CI 逐字节可复现（对应缺口 2）。
- 报告继续保留原始样本，并新增统计参数（seed、resample 次数、alpha）到元数据（对应缺口 2、4、5）。

## 7. CI boundary 契约型 CI 边界

### 7.1 业界实践

共享 CI 上的墙钟测量不可信：共享 runner 存在资源争抢、频率漂移与后台负载，产出不可比的数据。业界共识是把“测量”留在受控的本地或专用环境，CI 只承担契约验证：编译、确定性单元测试、黑盒冒烟、产物 schema 校验。

### 7.2 仓库观察

- 现有 workflow 为两个有序 job：`test / smoke`（byte-compile 全部 runner / tool 源码）与 `test / golden-standard`（依赖 smoke，安装测试 extra 后运行 `scripts/benchmark-golden-gate.py`）。
- golden gate 当前执行 byte-compile、报告 fixture 校验、async 黑盒 pytest（无 Styio checkout 时显式 skip），不启动任何性能测量。
- **共享 CI 不执行墙钟基准（shared CI never runs wall-clock benchmarks）**：当前 workflow 没有任何 benchmark 执行 job，全部为契约型检查。

### 7.3 建议

- 延续契约型 CI：新增内容继续以 byte-compile、确定性 fixture / 统计断言、工具 smoke 的形式进入 gate，严禁在共享 CI 上新增墙钟基准 job（对应缺口 5）。
- smoke 用例保持 repeats=1，保证契约验证在 CI 时长预算内（对应缺口 2、5）。

## 8. Repository gap mapping 仓库差距映射

本节把本报告结论显式映射到已授权的五个能力项（决策 Q-001 选择 `all`）。落地状态由交付计划与门禁跟踪，本文档不作实现完成断言。

| # | 能力缺口（已授权） | 业界依据（见 References） | 规划交付物 | 落地状态 |
|---|---|---|---|---|
| 1 | 调研报告落盘与覆盖文档同步 | 本报告 §1–§7；[1][2][3][4][5][6] | `docs/BENCHMARKING-SURVEY.md`（本文档）与 `docs/COVERAGE-MATRIX.md` 同步 | 计划承接，以门禁为准 |
| 2 | 两个 runner 统计严谨性升级 | [2][3][4][7] | `async-runtime` 与 `native-cpp` runner：中位数 + 固定种子 bootstrap 95% CI + CV + 对 C++ 基线的 Mann-Whitney U；默认 repeats=10；small / medium / large 规模预设；smoke 保持 repeats=1；报告契约仅增字段 | 计划承接，以门禁为准 |
| 3 | 环境控制清单工具 | [3][4] | `tools/benchmark-env-check.py`：只读、纯标准库、脱敏输出、可选 strict 模式 | 计划承接，以门禁为准 |
| 4 | baseline-vs-head 比较器 | [2] | `tools/benchmark-compare.py`：稳定指标匹配、方向感知中位数变化、Mann-Whitney U 证据、五类判定 | 计划承接，以门禁为准 |
| 5 | 门禁与 CI 接线 | [3][4] 与 §7 | `scripts/benchmark-golden-gate.py` 与 `.github/workflows/tests.yml`：新统计字段 / fixture / 工具 smoke 进入 gate；不新增共享 CI 墙钟基准 job | 计划承接，以门禁为准 |

## References 参考来源

1. [Go testing 包基准框架（测量纪律与 b.N 自适应迭代）](https://pkg.go.dev/testing#hdr-Benchmarks)
2. [benchstat（A/B 比较、Mann-Whitney U 与置信区间）](https://github.com/golang/perf/tree/master/benchstat)
3. [Criterion.rs 用户指南（预热、采样、bootstrap 置信区间）](https://bheisler.github.io/criterion.rs/book/index.html)
4. [pyperf 文档（环境控制、分布统计、稳定性检查与报告可复现性）](https://pyperf.readthedocs.io/en/latest/)
5. [Google Benchmark（参数化规模、夹具与防优化机制）](https://google.github.io/benchmark/)
6. [OpenJDK JMH（预热、fork、参数化 @Param）](https://github.com/openjdk/jmh)
7. [SciPy mannwhitneyu 文档（Mann-Whitney U 双侧检验与样本量约束）](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.mannwhitneyu.html)
