# Styio Analyzer v1：版本比较规格

日期：2026-09-08。状态：v1 命令已实现，待独立验收；行为以本文为准。

依据：[系统设计](STYIO-ANALYZER-DESIGN.md)。执行顺序和验收证据见
[实现计划](STYIO-ANALYZER-IMPLEMENTATION-PLAN.md)。本文定义首轮可观察行为；
模块内部组织由实现者结合现有源码决定。

## 交付目标与边界

给定 Styio 基线、候选两套构建，对同一批 Styio 程序执行相同工作，产出
可复算的 JSON 证据和 Markdown 差异报告。没有 Agent 参与时也能执行。

首轮包括已有 parity-v2 的 11 个程序族及三条原生路线的版本比较、正确性
校验、配对耗时、独立 RSS 重放、生成产物体积和覆盖披露。默认可运行小范围，
也可显式选择全部程序族。源程序、输入和 oracle 继续来自现有唯一 catalog。

本轮属于评估工具建设，可以修改 `styio-benchmark` 的执行器、分析器、
测试、文档和必要的契约 CI。Styio 编译器、运行时、优化 pass、语义测试和
默认构建行为保持只读；允许在隔离目录构建既有源码以准备被测工具链。
发现 Styio 缺陷时留下独立复现和交接，不在本任务中修复编译器。

LNT、定时任务、完整应用扩充、LLVM remarks、阶段探针扩展、IDE 接入、
真实增量编译及编译器优化属于后续交付。本轮不创建第二份 workload catalog，
也不替换现有 core、async 或 Styio/C++ parity 的独立契约。

## 命令接口

计划提供 `tools/styio_analyzer.py`，包含两个子命令：

```bash
python3 tools/styio_analyzer.py compare \
  --baseline-root /path/to/baseline \
  --baseline-build-dir /path/to/baseline/build \
  --candidate-root /path/to/candidate \
  --candidate-build-dir /path/to/candidate/build \
  --contract workloads/parity-v2/contract.json \
  --family llvm-scalar-chain \
  --scale smoke \
  --out-dir reports/analyzer/example

python3 tools/styio_analyzer.py verify \
  --contract workloads/parity-v2/contract.json \
  --report reports/analyzer/example/results.json
```

| 参数或行为 | 定义 |
|---|---|
| 两组 root/build-dir | 必填，分别解析对应 Styio 工具链；不得回退到另一组或共享 checkout 的构建 |
| `--contract` | 默认现有 parity-v2 contract |
| `--family` | 可重复；默认全部 11 个程序族；未知、空选择报错 |
| `--scale` | 单选 `smoke`、`development`、`reference`；默认 `smoke` |
| `--run-class` | 单选 `development`、`controlled`；默认 `development`，不能自动升级 |
| 路线 | 每个所选程序族均采集 `compile-and-run`、`native-build`、`native-run` |
| 产物 | `results.json` 为数值来源；`summary.md` 由同一结果生成 |
| `verify` | 离线复算并检查报告一致性，不执行被测程序 |

`compare` 完成所选用例的执行且必要证据有效时退出 0；发生构建、执行、
正确性或必要采集失败时退出非零，并尽可能写出披露失败的报告。
发现性能退化本身不改变 `compare` 退出码。`verify` 退出 0 仅表示报告
符合规格、覆盖披露及复算一致，包括如实记录的失败或不确定结果；它不表示
候选通过性能验收。命令帮助和摘要必须说明这种区别。

## 配对执行与隔离

1. 两侧使用同一个 catalog 中的 **Styio** 源程序、同一输入及独立 oracle。
   不能让 candidate 经由 C++ 编译适配器执行，也不能以两侧输出相同替代 oracle。
2. 基线和候选各使用自己的工作目录、编译器及对应运行时输入。原生构建统一
   使用关闭 Styio native runtime cache 的冷构建配置，使用现有
   `STYIO_NATIVE_CACHE=0` 接口，仅作用于本轮子进程；不改用户全局环境或缓存。
   记录语义化配置，无法确认可比时披露原因，不把未知配置写成已验证。
3. 两侧构建配置、目标和外部 Clang 工具链应相同；Styio 编译器及其对应
   运行时版本是实验变量。若依赖配置无法保持一致，标为不可比较。
4. 复用已有等量批处理、校准、交错及统计实现。两侧采用相同 batch count；
   默认沿用现有 3 次预热、11 次保留样本和 0.5 秒最短保留批次要求。
5. 每一对样本在同一轮交错采集，并保存配对次序与原始批次值。不能运行两次
   完整 Styio/C++ 测试后，把其中的 Styio 数组拼成“配对实验”。
6. `native-run` 在计时外编译产物；另外两条路线按 catalog 边界重新构建。
   保留批次中的程序结果按既有规则校验。时间采集不启用 RSS 采样或其他诊断。
7. RSS 使用等价的独立重放。生成产物体积从计时外的原生产物读取，以字节
   表示；不把它标为 text section 体积，也不为单个体积观测生成置信区间。
8. 保留所有已采集数值。缺少样本、输出错误、环境不一致、时长不足和高噪声
   均显式披露。不得删除异常样本或重复运行直到出现希望的结论。

编译器版本、构建身份及配置用于区分被测对象。优先复用现有构建凭证；
如需补充身份信息，使用最少的修订信息、dirty 声明和实际编译器产物标识，
不建设递归哈希体系。版本字符串相同不证明产物相同，Git 修订也不单独证明
可执行文件与源码对应。无法追溯的产物不能伪装成某个已确认版本。

当前适配器在计时外使用一次临时 CXX 探测，观察被测 Styio 实际传出的
运行时源码输入和原生构建配置；探测不链接产物，调用不计入性能样本。
执行 CXX 时保留调用路径的名称及符号链接语义，物理文件标识仅用于身份
比较。报告同时披露驱动模式、目标和公开 CMake 构建配置；配置缺失或不一致
时标为不可比，不能用相邻目录中的文件推定内嵌配置。

中途失败时，`pair_orders` 和 `sample_schedule` 描述已经完成的时间配对
前缀。未成对的时间值分别保存在 `unpaired_time_samples_s` 与
`unpaired_raw_batch_time_samples_s`，RSS 未成对值保存在
`unpaired_rss_samples_kib`。离线校验检查已有数值、归一化及配对次序，
不以目标样本数未满足为由跳过检查；不足目标样本数的指标不输出聚合统计。

进程、构建目录及临时产物的隔离只作用于本任务拥有的资源。不要切换他人
工作树、覆盖构建或终止其他任务；也不要增加整轮固定超时或“超时就换方案”
逻辑。测量受资源竞争影响时保留不确定结果和原因。

## 报告内容

使用独立的版本比较报告标识，例如 `styio.analyzer.comparison.v1`，明确
`comparison_kind = styio-revision`。这是新比较类型的结果，不是修改既有
parity 报告含义。内部数值模型和隐私输出实现应复用。

| 信息 | 要求 |
|---|---|
| 实验身份 | catalog 身份、工具链身份、公开构建配置、尺度、run class；披露 corpus 为项目微内核 |
| 选择与覆盖 | 所选 cell ID、实际观察到的 ID、未完成原因、catalog 的能力排除项；子集不得呈现为全套 |
| 正确性 | baseline/candidate 各自的构建、执行和 oracle 结果 |
| 耗时 | baseline/candidate 原始批次值、batch count、归一化样本、配对次序、中位数、CV、配对比值和区间 |
| RSS | 两侧独立重放的样本、统计量及相同方向的比值；指标无法提供时显式不可用 |
| 产物体积 | 两侧原生产物的字节数、差值及比值，注明来自计时外构建 |
| 比较与限制 | 每个指标的比较状态和原因；收集是否完成与性能方向分别表示 |

持久化 JSON/Markdown 使用仓库相对路径、公开工具链配置和数值。不得包含
本机身份、真实绝对路径、命令全文、环境变量值、端点、凭据或原始子进程
输出。执行时使用的路径不直接进入证据报告；复现文档使用占位路径。

Markdown 至少包含：本次覆盖范围、逐用例的三条路线、baseline/candidate
数值与比值、正确性及不确定原因、内存和体积变化、未完成项。不得把运行时、
构建时间和诊断阶段混成一个总分，也不得用遗漏困难用例改善摘要。

## 比较语义

所有成本比值统一为 `candidate / baseline`：小于 1 表示该成本更低。
若展示由耗时换算的吞吐，必须注明是派生量及其相反方向，不能标为独立测量。

时间与 RSS 的配对统计沿用现有数值实现和相应测量质量规则。第一版不新增
“所有程序必须提升若干百分点”的验收门槛，也不套用跨语言 parity 的速度目标。

| 状态 | 含义 |
|---|---|
| `improved` | 有效配对数据的置信区间整体低于 1；说明观测到的成本下降 |
| `regressed` | 有效配对数据的置信区间整体高于 1；说明观测到的成本上升 |
| `no_detected_change` | 数据满足测量要求，区间包含 1；只表示未检测到差异，不证明等效 |
| `inconclusive` | 噪声、时长、配对数量或可比性不足；保留数值但不判断方向 |
| 不可用/失败 | 构建、执行、正确性或指标采集缺失；用状态和原因表示，不以 0 填充 |

体积作为直接观察值列出，缺少重复观测时不使用基于置信区间的状态。
每个用例的统计结论是研究线索，不宣称已控制整套多重比较的误报率。
真实 A/A 实验可能出现噪声或偶发差异；应调查测量条件，不能通过改阈值或
重跑至通过制造“零差异”。确定性 A/A 数值测试则必须正确归类。

## 与已有工具的关系

从 `standard_parity_gate.py` 中提取必要的参与方无关采样与统计模块，或用
同等简洁方式共享实现。既有 Styio/C++ 入口仍输出原来的字段和决策，内部
通过适配器调用共享实现。保留公开入口不等于允许留下两份相同算法。

`core-benchmark-compare.py` 的历史摘要比较、async 领域报告及现有研究
档案继续维持各自用途。首轮不吸收它们的格式迁移，也不让它们成为新配对
结果的替代来源。后续扩展在同一 Analyzer 上增加领域适配，不另建评价体系。
