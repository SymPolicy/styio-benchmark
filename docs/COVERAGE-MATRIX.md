# Benchmark Coverage Matrix

这份文档不再按 milestone 看 benchmark，而是按编译器真实流水线与模块切面看覆盖率。

目标是让性能结论能回答 3 个问题：

1. 慢的是哪一层：`Lexer / Parser / TypeInfer / StyioIR / LLVM IR / Execute`
2. 慢的是哪一类模块：资源、状态、集合、控制流、函数、流式算子、字典、标准流
3. 退化发生在什么规模：小输入、典型输入、放大输入

## 编译流水线视角

建议把 benchmark 固定拆成 6 层：

1. `Tokenize`
   - `StyioTokenizer::tokenize(...)`
2. `Parse`
   - `parse_main_block_with_engine_latest(...)`
3. `TypeInfer`
   - `StyioSemaContext::typeInfer(...)`
4. `StyioIR Lower`
   - `AstToStyioIRLowerer::toStyioIR(...)`
5. `LLVM IR Gen`
   - `StyioToLLVM::toLLVMIR(...)` + `dump_llvm_ir()`
6. `Execute`
   - CLI / JIT 执行

Styio 主仓库的 `styio_soak_test` probe 已能让第 1 到 5 层在一次隔离进程内分段计时；parity runner 每个 tier/sample 只启动一次 probe，并将五个边界共享给 phase cells；第 6 层已有标准化 CLI workload matrix 和 error-path matrix。

## 模块切面视角

每一层都不应该只测一个样例，而应该覆盖这些模块族：

- `Scalar`
  - 算术、比较、逻辑、格式化字符串
- `Bindings`
  - `flex/final/compound assign/parallel assign`
- `Functions`
  - 简单函数、块体函数、匿名函数、调用链
- `ControlFlow`
  - `match / cases / cond-flow / loop / break / continue`
- `Collections`
  - `list / tuple / dict / range / list-op`
- `Resources`
  - `@file / @{...} / redirect / write / auto-detect`
- `Streams`
  - iterator、zip、stdin/stdout、instant pull
- `StateAndSeries`
  - `snapshot / pulse / state-ref / history / series intrinsic`
- `Topology`
  - bounded ring、typed stdin list、特殊 lowering 合约

## 当前覆盖现状

### 已有

- `Tokenize`
  - 长输入摄入循环：`styio-probes/styio_soak_test.cpp`
- `Compiler stage matrix`
  - `tokenize / parse / typeInfer / styioIR / llvmIR`
  - 覆盖 `Scalar / Bindings / Functions / ControlFlow / Collections / Resources / Streams / StateAndSeries / Topology / Mixed`
- `Compiler micro benchmark matrix`
  - `lexer / parser / type / lower / llvm`
  - 当前覆盖 `lexer.long_identifiers`、`lexer.mixed_trivia`、`parser.expr_scalar_chain`、`parser.function_block_body`、`parser.match_cases`、`parser.iterator_resource_postfix`、`type.dict_heavy`、`type.snapshot_state`、`lower.stream_zip`、`lower.resource_io`、`lower.state_pulse`、`llvm.scalar_ir`、`llvm.resource_ir`、`llvm.state_ir`
- `Full-stack matrix`
  - 覆盖 CLI 从 parser 到 execute 的总墙钟时间
  - 覆盖 `Scalar / Bindings / Functions / ControlFlow / Collections / Resources / Streams / StateAndSeries / Topology`
- `Async runtime matrix`
  - 覆盖 `Styio task scheduler / C++ stackless coroutine / Go goroutine / Rust Tokio`
  - 当前冻结 `sleep` 阻塞任务并发收敛与 `noop` fanout 调度开销，报告入口为 `async-runtime/run-async-bench.py`
- `Native C++ comparison`
  - 覆盖 Styio 语言程序与手写原生 C++ 的黑盒性能对照
  - parity-v2 已覆盖 3 个 CLBG 风格和 8 个 LLVM TestSuite 风格的独立项目微内核；历史 ID 不代表纳入官方套件源码
  - 标准路线固定为 `compile-and-run / native-build / native-run`，其中 `native-run` 是原生性能主结论
  - 每条路线的开始/停止边界由 `workloads/parity-v2/contract.json` 冻结；构建产物不进入执行计时
  - `tools/standard_parity_gate.py` 负责等量批处理、随机交错、置信区间、路线分榜、内存门禁和隐私安全报告
- `Error-path matrix`
  - 覆盖 `lex / parse / type / runtime` 失败路径
  - 当前覆盖 `lex.unterminated_block_comment`、`parse.empty_match_cases`、`type.final_then_flex_i64`、`runtime.read_missing_file`
- `FFI / runtime helpers`
  - 文件句柄、拼接、字典、非法句柄
- `CLI long-run`
  - `m6/t02_running_max`
  - 3 条 state-inline 程序

### 标准测评状态

- parity-v2 已冻结 `smoke / development / reference` 三档规模，完整约束见
  [`STANDARD-PARITY.md`](STANDARD-PARITY.md)。
- Google Benchmark 风格的 0.5 秒最短测量、3 次预热、11 次保留样本、可复现随机交错和配对 bootstrap 置信区间已经落地。
- 时间与峰值 RSS 按 scale 和 route 分开计算；compiler phase 只用于归因，不进入原生性能总分。
- reference 严格门禁要求受控运行声明；共享 CI 只验证测评契约和统计实现，不发布性能结论。
- 报告只含稳定 ID、公开工具链版本与数值证据，不保存机器身份、地址、凭据或原始子进程文本。

## 建议的 benchmark 分类

### A. Phase Benchmark

按阶段分时，关注 `tokenize / parse / typeInfer / styioIR / llvmIR`。

每个 workload 至少输出：

- `phase_us`
- `share_pct`
- `avg_token_arena_kib`
- `avg_ast_arena_kib`
- `rss_growth_kib`

后续补：

- `peak_rss_kib`
- `nightly_declined_count`
- `legacy_fallback_count`
- `nightly_internal_bridge_count`

### B. Module Micro Benchmark

每个 benchmark 只压一个模块族，减少解释难度。

建议第一批：

- `lexer.long_identifiers`
- `lexer.mixed_trivia`
- `parser.expr_scalar_chain`
- `parser.function_block_body`
- `parser.match_cases`
- `parser.iterator_resource_postfix`
- `type.dict_heavy`
- `type.snapshot_state`
- `lower.stream_zip`
- `lower.resource_io`
- `lower.state_pulse`
- `llvm.scalar_ir`
- `llvm.resource_ir`
- `llvm.state_ir`

### C. Full Slice Benchmark

覆盖真实组合链路，不再按 milestone 命名，而按能力切片命名：

- `slice.scalar_core`
- `slice.function_bind`
- `slice.control_match`
- `slice.resource_file_io`
- `slice.stdin_stdout`
- `slice.stream_zip`
- `slice.snapshot_state`
- `slice.dict_heavy`
- `slice.topology_ring`
- `slice.full_pipeline_mixed`

### D. Error-path Benchmark

单独冻结失败路径成本：

- `lex.unterminated_block_comment`
- `parse.empty_match_cases`
- `type.final_then_flex_i64`
- `runtime.read_missing_file`

下一批继续补：

- `parse.hash_iterator_match_forward_chain`
- `type.unsupported_zip_sources`
- `runtime.write_to_stdin`
- `runtime.read_from_stdout`

## 规模维度

每个 benchmark 不应只测单一输入，至少要有三档：

- `small`
  - 防止优化把固定开销做差
- `medium`
  - 最接近日常开发输入
- `large`
  - 看复杂度和缓存行为

对于不同模块，规模参数应不同：

- Lexer/Parser
  - token 数、行数、trivia 密度、嵌套深度
- Dict/List
  - entry 数、key 长度、更新次数
- Streams/State
  - iterator 长度、zip 宽度、snapshot 窗口、pulse 次数
- LLVM
  - IR 指令数、基本块数、函数数

## 当前落地状态

1. 已完成 `FrontendPhaseBreakdownReport`
   - 从 2 个 workload 扩到覆盖主要模块切面的 compiler stage matrix
   - 已加入 `llvm_ir_us`
2. 已完成第一批模块微基准
   - `CompilerMicroBenchmarksReport` 已覆盖 `lexer / parser / type / lower / llvm` 热点切面
3. 已完成 parity workload catalog
   - `parity-v2/contract.json` 冻结十一类等价 Styio/C++ 工作负载、三条支持路线和五个阶段诊断
4. 已完成第一批 error-path benchmark 组
   - `CompilerErrorPathBenchmarksReport` 冻结 `lex / parse / type / runtime` 代表性失败路径的 wall-clock、退出码和诊断码
5. 已完成 benchmark 结果归档
   - `tools/perf-route.sh` 统一委托标准 runner 生成隐私安全的 `results.json`
6. 已完成异步运行时横向基准
   - `async-runtime/run-async-bench.py` 生成 Styio / C++ stackless coroutine / Go goroutine / Rust Tokio 对比报告，并支持在 `build/async-runtime-toolchains` 下本地 bootstrap Go/Rust
7. 已完成 parity 阶段探针
   - `ParityPhaseSweepReport` 从 manifest 读取 tier 和五个编译阶段，在一次进程内 probe 中发出五个边界；runner 以 Clang time-trace 对等来源并校验语言特定 source digest
8. 已完成标准统计与严格门禁
   - 等量自适应批处理、随机交错、原始样本保留、95% 配对分层 bootstrap、CV/时间/RSS 门禁和能力缺口披露均由同一 runner 实施
9. 进行中的容器/快照拷贝研究切片
   - 档案 `docs/RESEARCH-CONTAINER-SNAPSHOT-COPIES.md`，负载 `workloads/research/container-snapshot-copies/`
   - 这是 development 证据，不是 parity-v2 主张

## 评估标准

一个性能结论只有在下面条件同时满足时才成立：

- phase benchmark 显示对应阶段耗时下降
- 相邻阶段没有明显回退
- RSS / peak RSS 没显著变差
- full slice benchmark 没出现反向结论
- shadow gate / security / pipeline correctness 全绿

如果只有某个切面更快，但其他切面或大输入退化，这不算有效优化。
