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

Styio 主仓库的 `styio_soak_test` probe 已能让第 1 到 5 层稳定在进程内分段计时；第 6 层已有标准化 CLI workload matrix 和 error-path matrix。

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
  - 每个核心指标输出 median + 确定性 bootstrap 95% CI + CV，并对非 C++ 运行时输出相对 C++ stackless coroutine 的双侧 Mann-Whitney U 显著性
  - 支持 `small / medium / large` 三档规模预设，baseline 默认重复 10 次，smoke 保持 1 次
- `Native C++ comparison`
  - 覆盖 Styio 语言程序与手写原生 C++ 的黑盒性能对照
  - 当前第一组 workload 是 `stdin_echo`，对照 `@stdin >> #(line) => { line -> @stdout }` 与 `std::getline` / `std::cout` 循环
  - 标准路线固定为 `full-cli / cached-jit / runtime-only`
  - `cached-jit` 在 Styio 暴露可复用编译/JIT 产物执行入口前必须报告为 `unsupported`
  - `runtime-only` 当前使用 Styio 选定 build 的导出 C runtime ABI，先隔离标准流 helper 成本
  - 报告入口为 `native-cpp/run-native-cpp-bench.py`，并由 `tools/perf-route.sh` 的 `native_cpp_comparison` section 纳入总报告；输出 `results.json / benchmarks.csv / summary.md`，按每条 route 的最快实测实现归一化为 `1.00x`
  - 每条 measurable 记录输出 elapsed/throughput 的 median + bootstrap 95% CI + CV，并与同 case/route 的 `native_cpp` 做 Mann-Whitney U 显著性；`native_cpp` 标记为 baseline，`unsupported` 标记为 not comparable
  - 支持 `small / medium / large` 三档 line-count 预设，默认重复 10 次
- `统计与工具面`
  - `tools/benchmark-env-check.py`：只读环境清单（CPU 可用性/亲和性、后台负载、频率 governor、turbo/boost、电源），输出 sanitized 的 pass/warn/unavailable，strict 模式可本地强制
  - `tools/benchmark-compare.py`：baseline-vs-head 报告比较器，输出方向感知的 median 变化与 Mann-Whitney U 证据，分类为 regression / improvement / inconclusive / insufficient-sample / incompatible-input
  - `scripts/benchmark-golden-gate.py` 与 `.github/workflows/tests.yml`：契约门禁只做 byte-compile、fixture 校验、快速测试与工具 smoke，共享 CI 不运行墙钟基准
- `Error-path matrix`
  - 覆盖 `lex / parse / type / runtime` 失败路径
  - 当前覆盖 `lex.unterminated_block_comment`、`parse.empty_match_cases`、`type.final_then_flex_i64`、`runtime.read_missing_file`
- `FFI / runtime helpers`
  - 文件句柄、拼接、字典、非法句柄
- `CLI long-run`
  - `m6/t02_running_max`
  - 3 条 state-inline 程序

### 缺失

- `模块微基准` 还不够细
  - 第一批热点切面已经独立拆组，但还没有扩到 `bindings/topology/resources` 等更细颗粒度
- `Error-path` 还不够细
  - 当前只冻结了 4 条代表性失败路径，尚未扩到 `bindings/resources/stdin/stdout` 的更多错误子码

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
3. 已完成 full CLI benchmark 组
   - `FullStackWorkloadMatrixReport` 冻结 end-to-end wall-clock
4. 已完成第一批 error-path benchmark 组
   - `CompilerErrorPathBenchmarksReport` 冻结 `lex / parse / type / runtime` 代表性失败路径的 wall-clock、退出码和诊断码
5. 已完成 benchmark 结果归档
   - `tools/perf-route.sh` 现在会生成 `metadata.tsv / sections.tsv / results.json / benchmarks.csv / summary.md`
6. 已完成异步运行时横向基准
   - `async-runtime/run-async-bench.py` 生成 Styio / C++ stackless coroutine / Go goroutine / Rust Tokio 对比报告，并支持在 `build/async-runtime-toolchains` 下本地 bootstrap Go/Rust
7. 已完成第一组原生 C++ 横向基准
   - `native-cpp/run-native-cpp-bench.py` 生成 Styio / native C++ 的 stdin echo 黑盒对比报告
   - 三条标准路线已嵌入 `tools/perf-route.sh`：`full-cli`、`cached-jit`、`runtime-only`
   - `full-cli` Styio 侧包含 source read、parse、lowering、LLVM/JIT 与执行成本
   - `cached-jit` 当前明确报告 `unsupported`，直到 Styio 提供真实缓存执行入口
   - `runtime-only` 当前对比 native C++ 与 Styio runtime helper harness，隔离标准流 helper 成本
8. 下一批优先项
   - 把 `small / medium / large` 规模 sweep 扩展到 compiler stage、module micro 与 full-stack benchmark 族
   - 扩展 native C++ 对照到 compute-heavy workload，补真实 cached-JIT 执行入口
   - 扩 error-path benchmark 子类
   - 增加 benchmark catalog 与受控本地 baseline 选择、版本治理

## 评估标准

一个性能结论只有在下面条件同时满足时才成立：

- phase benchmark 显示对应阶段耗时下降
- 相邻阶段没有明显回退
- RSS / peak RSS 没显著变差
- full slice benchmark 没出现反向结论
- shadow gate / security / pipeline correctness 全绿

如果只有某个切面更快，但其他切面或大输入退化，这不算有效优化。
