# Styio Analyzer 首轮实现与独立验收计划

日期：2026-09-08。状态：计划已落地；v1 实现已提交独立验收，不以本文代替验收结论。

阅读顺序：[系统设计](STYIO-ANALYZER-DESIGN.md) →
[v1 行为规格](STYIO-ANALYZER-V1.md) → 本计划。
[开始提示词](prompts/styio-analyzer-v1.cursor.md) 用于派发；以用户最新指令、
仓库规则和 v1 规格为约束，不因提示词简写而扩大或缩小交付范围。

## 本轮交付

实现 v1 的 Styio 基线/候选比较工具。用户选择两个构建及程序族，一次执行
得到正确性、三条路线的配对耗时、独立 RSS 重放、产物体积、覆盖和限制说明。
结果可离线复算，既有 Styio/C++ parity 行为保持稳定。

本轮只建设评估设施，不实施 Styio 性能优化。Cursor 完成实现、自审、定向
验证和一次最终完整回归，然后提交证据包给 Codex 独立验收。性能结果可以是
没有收益或不确定，不能为得到正向数字而修改被测编译器。

长期交付顺序保留为：

| 阶段 | 交付 | 与本轮的关系 |
|---|---|---|
| M1 | v1 同语言版本比较 | 本次 Cursor 完成并交回验收的全部范围 |
| M2 | LNT 结果适配和历史查询 | M1 验收后单独派发；部署条件在该阶段落实 |
| M3 | 完整应用、规模语料及优化归因 | 复用 M1 结果模型，另行明确语料和编译器观测接口 |
| M4 | async、运行时和语言服务领域接入 | 复用既有领域入口，另行定义指标和验收 |

## 仓库依据与文件责任

2026-09-08 检查到的主要复用点：

| 文件 | 已有能力 | 本轮处理 |
|---|---|---|
| `tools/standard_parity_gate.py` | `load_catalog`、`calibrate_batch_count`、`_interleaved_pair_orders`、`ratio_dimension`、`bootstrap_geomean_ratio_ci`、`_measure_cell`、隐私序列化 | 抽取必要通用实现，保留既有入口与输出语义 |
| `native-cpp/process_tree_rss.py`、`standard_process_tree_rss.py` | 批处理、进程执行、RSS 重放 | 复用；仅在适配两套 Styio 构建确实需要时修改 |
| `workloads/parity-v2/contract.json`、`generators.py` | 11 个项目程序族、三条路线、尺度和 oracle | 读取现有 Styio 语料，不复制或更换算法、输入和 oracle |
| `tools/styio_analyzer.py` | 尚不存在 | 增加 v1 命令入口 |
| 必要的共享 Python 模块 | 尚未规定文件布局 | 实现者选择最小布局，不引入插件框架或第二个执行引擎 |
| `tests/test_standard_parity_*.py`、`test_standard_cpp_strength.py` | 既有 parity 约束 | 保留原有断言语义；模块提取时调整导入位置 |
| `tests/test_styio_analyzer.py` | 尚不存在 | 集中新增有行为意义的 Analyzer 测试，可按实际复杂度拆分 |
| `README.md`、Analyzer 文档、必要的契约 CI | 项目入口与交付说明 | 实现后同步真实接口；CI 只运行确定性验证 |

不要改动 Styio 仓库的源码、优化 pass、运行时或语义测试。不要迁移已有
core/async 报告，也不要改变 parity 的数值门槛。工作树可能有其他任务的
未提交改动，先确认当前差异，保留他人工作；禁止用 reset/checkout 覆盖。

## 执行步骤

步骤有依赖，按序形成一个交付闭环。无需为普通实现选择反复请求确认。

### 1. 确认被测对象与现有实现

- 阅读上述文件和实际测试，核对当前函数签名、字段及新旧目录状态。
- 记录本轮起始差异，建立最小的修改清单。历史文件已被他人删除时，不为
  复用旧代码恢复它们。
- 准备两套可追溯的 Styio 构建。优先使用明确指定或可核验的既有产物；
  必要时在隔离工作树构建已存在的修订，不能覆盖共享构建或修改源码。
- 核对各构建使用自己的运行时输入，以及相同的外部构建配置。无法取得
  第二版本时继续完成工具和确定性验证，最终明确真实 A/B 证据仍缺失。

此步骤不是交付终点；核对完成后继续实现，不停在计划或发现列表。

### 2. 提取并复用采样与统计

- 将参与方从硬编码的 `styio/cpp` 组织为可供适配器调用的两侧参数。
  parity 适配器继续映射到原字段；Analyzer 映射到 `baseline/candidate`。
- 共享等量校准、配对次序、归一化、数值统计和输出隐私校验。不要通过重跑
  两个完整 parity shard 或交换 JSON 键名来代替真实配对采集。
- 提取的逻辑须一次性迁移到共享实现；删除被替代的重复函数体，保留必要的
  公开适配入口。用一次性源码检索确认迁移，不建立永久迁移检查门禁。
- 此时做受影响的定向测试，发现本范围普通问题直接修复。

### 3. 实现两套 Styio 的执行适配

- 实现 v1 `compare` 参数和选择语义；每个程序族跑三条既定路线。
- 分别解析两套构建，不使用会回退到另一套编译器的目录发现逻辑。
- 两侧都编译 catalog 的 Styio 源程序，各自通过独立 oracle；不编译 C++
  参考程序来冒充 candidate。Clang 仍可作为 Styio 原生构建依赖。
- 冷构建配置仅传入被测子进程；隔离临时文件和运行时输入。执行时间、RSS
  重放、产物体积按规格分开取得。
- 失败记录保留已完成用例、样本和稳定原因。错误输出只用于本地即时诊断，
  公开报告不保存原始子进程文本。

### 4. 实现比较、报告和离线复算

- 按 v1 内容写 `results.json` 和 `summary.md`；前者是数值来源。
- 统一 `candidate / baseline` 方向，复用配对区间，区分未检测到变化、
  数据不确定、执行失败与观测到的性能方向。
- `verify` 重算批次归一化、配对统计和派生结论，检查选择与实际覆盖一致。
  正确呈现部分执行或不可用数据，不把缺少指标填为零。
- 新字段通过明确的隐私输出适配加入，不放宽既有 parity 对私有数据的限制。
- 摘要写明退出成功、报告有效和候选性能通过各自的含义。

### 5. 完成定向验证、真实演示和交付自审

- 完成下面 AC-01 至 AC-09，真实演示可以没有性能收益。
- 更新 README 和使用文档为实际实现；维护本规格的行为语义。普通内部模块
  变化只需同步文档，不能通过修改规格掩盖未实现的必要能力。
- 如通用逻辑迁移涉及 CI，沿用现有契约 CI 加入确定性测试；不在共享 CI
  上新增速度门槛、不创建定时任务或启动 LNT 服务。
- 审阅所有本轮源码、测试、报告和文档，完成范围内修复及定向验证。
- 之后执行一次最终完整回归。通过后整理交接包，停止在“待独立验收”，
  不自动继续 M2，也不代替 Codex 宣布最终验收通过。

## 验收矩阵

| ID | 必须满足的行为 | 最小有效证据 |
|---|---|---|
| AC-01 | 两套工具链分别使用自己的编译器、工作目录和运行时输入 | 隔离的编译器替身记录执行契约；缺少 candidate 时不能回退到 baseline；真实产物身份记录 |
| AC-02 | 两侧均运行同一 Styio 源、输入和独立 oracle | candidate 错误、两侧同样错误均产生正确性失败；正常输入通过 |
| AC-03 | 三条路线边界正确，配对校准等量，时间与 RSS 分离 | 适配器的实际调用事件或受控执行证据，确认构建、运行、重放所属阶段；不得只测试函数名称 |
| AC-04 | 比值方向、区间与状态正确 | 手工可核对的固定数值：11 对 baseline=1 秒，candidate 分别为 0.8/1/1.2 秒，得到对应方向；额外覆盖高噪声和不足样本 |
| AC-05 | 报告可离线复算且不泄露本机信息 | 改动摘要数值、归一化或配对内容后被检测；缺失/非有限数值正确处理；合成隐私哨兵不进入 JSON/Markdown |
| AC-06 | 子集、失败、未支持能力和缺失指标不会虚增评价 | 有失败及缺失指标的样本报告；11 个族的目录选择覆盖测试；摘要准确披露范围 |
| AC-07 | 既有 parity 方法和公开行为保持原义 | 现有 catalog、统计、隐私、C++ 基线测试通过；源码审阅确认没有弱化断言或复制采样器 |
| AC-08 | 工具能执行真实编译器，而非只通过替身测试 | 一份真实 A/A 和一份真实 A/B 的数值报告、摘要及离线复算结果；观察到的方向可以为无差异或不确定 |
| AC-09 | 交付范围、文档和验证完整 | 修改清单、一次性迁移核查说明、最终回归记录、已知限制与交接包；无 Styio 源码优化改动 |

AC-04 的构造数据只验证 Analyzer 数学行为，必须与真实性能报告分开保存，
明确标为测试夹具。AC-08 的 A/A 是测量偏差检查，不能要求随机样本必然
产生零差异；出现异常先解释，不能删样本或改算法以得到预期结论。

## 验证命令与真实演示

以下 Analyzer 命令和新测试文件在本计划编写时尚未实现；由 Cursor 交付。
其他列出的测试与 catalog 命令已经存在。命令均从 `styio-benchmark` 执行，
`/path/to/...` 由执行者替换为实际隔离构建，真实路径不写入公开证据。

开发阶段按改动运行必要的定向检查，不在每一步重复整套回归：

```bash
python3 -m pytest -q tests/test_styio_analyzer.py

python3 -m pytest -q \
  tests/test_standard_parity_gate.py \
  tests/test_standard_parity_catalog.py \
  tests/test_standard_cpp_strength.py
```

真实 A/A：将同一套工具链同时作为两侧，运行 `llvm-scalar-chain` 的 smoke
三路线，验证执行和报告流程。真实 A/B：使用不同、可追溯的 Styio 构建，
至少覆盖以下三个已有程序族及其全部三路线：

```bash
python3 tools/styio_analyzer.py compare \
  --baseline-root /path/to/baseline \
  --baseline-build-dir /path/to/baseline/build \
  --candidate-root /path/to/candidate \
  --candidate-build-dir /path/to/candidate/build \
  --family llvm-scalar-chain \
  --family llvm-control-diamonds \
  --family llvm-dense-matmul \
  --scale smoke \
  --run-class development \
  --out-dir reports/analyzer/v1-ab

python3 tools/styio_analyzer.py verify \
  --report reports/analyzer/v1-ab/results.json
```

这里验证工具的真实能力，不是发布 reference 性能成绩。实现需要支持
完整 11 族选择，但首轮无需为验收反复运行完整 reference 性能套件。
若真实构建不可用或正确性失败，必须披露 AC-08 未完成，不用替身结果替代。

所有改动、自审、范围内修复及定向验证完成后，只执行一次最终完整回归：

```bash
python3 tools/standard_parity_gate.py catalog-check \
  --contract workloads/parity-v2/contract.json
python3 tools/standard_parity_gate.py cpp-strength \
  --contract workloads/parity-v2/contract.json
STYIO_ROOT=/path/to/candidate python3 -m pytest -q
```

最终回归记录实际执行、通过、失败和跳过的范围。由可用构建决定的条件性
测试跳过要逐项说明；不能把跳过写成通过。若完整回归失败，先诊断原因并
向开发者提交具体修复与验证建议，后续修复和是否重跑由开发者决定。
在最终回归之后再修改覆盖的源码或测试，原回归不能继续证明新状态。

## Cursor 交接包

在 `reports/analyzer/<delivery-id>/HANDOFF.md` 记录以下内容，并链接实际
结果；按 reports 目录既有规则保存必要的审阅资产。只建立这份交接记录，
不另建任务数据库、哈希清单或重复日志系统。

```markdown
# Styio Analyzer v1 交接

状态：实现完成，待 Codex 独立验收（或列明未完成项）

- 实现身份：相关仓库修订；未提交部分的仓库相对补丁或差异说明
- 修改范围：文件及行为变化，明确与起始工作树其他改动的边界
- 复用方式：共享采样/统计所在模块，旧函数体的一次性迁移核查结果
- 使用方式：compare / verify 的占位路径命令
- 被测身份：A/A、A/B 的两侧构建身份与公开配置
- 真实证据：results.json、summary.md、离线复算结果
- 验收矩阵：AC-01 至 AC-09，各自状态和对应证据
- 定向验证：命令模板、结果、必要的跳过说明
- 最终完整回归：运行一次的结果、覆盖范围、失败及跳过说明
- 已知限制：未完成能力、不可比环境、未验证项
- 范围外发现：简短症状和最小复现，不混入本轮修复
```

不要把同一构建改个名字冒充不同版本，不把测试夹具放进真实结果，不以
“测试通过”代替原始测量证据。实现者可完成自审，最终验收结论由 Codex
在收到交付后独立给出。

## Codex 回收验收顺序

1. 对照 v1 规格和 AC 矩阵审阅实际源码差异，核实测量与实现边界。
2. 核对真实两侧产物、语料和配置，独立抽算配对数值与报告状态。
3. 审查正确性失败、噪声、缺失覆盖、隐私和退出码等必要行为。
4. 复用未发生变化的已有绿色证据；只针对疑点做必要复现，不默认重跑完整
   回归。最终给出通过、附具体缺口的待补充，或不通过结论。

验收不要求 Styio 因本轮工作变快；要求这套工具能可靠描述并解释版本差异，
为后续的优化研究提供稳定基础。
