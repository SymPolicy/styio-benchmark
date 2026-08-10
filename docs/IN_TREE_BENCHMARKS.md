# Styio Benchmark Routes

这份文档从 Styio 主仓库的 `benchmark/` 目录迁移而来。当前仓库是独立的 benchmark 编排仓库；需要通过 `--styio-root` 或 `STYIO_ROOT` 指向一个 Styio 源码 checkout。

约束很简单：

- 这里是性能编排、报告与回归工件的唯一权威入口
- Styio 内部 C++ probe target 名称仍由 Styio 主仓库注册，probe 源文件由
  `styio-probes/` 维护
- 其他文档只保留摘要与跳转，不重复维护 workload 细节
- 变更 benchmark 覆盖面或执行路线时，只更新这里和 [COVERAGE-MATRIX.md](./COVERAGE-MATRIX.md)

## 目录

- Styio 主仓库的 `styio_soak_test`
  - 基准与单线程 soak 测试 probe；源文件在 `styio-probes/`
- Styio 主仓库的 `styio_task_scheduler_perf_test`
  - task scheduler probe；源文件在 `styio-probes/`
- `tools/perf-route.sh`
  - 一键性能路线 + 结果归档
- `tools/perf-report.py`
  - 解析原始日志并生成 `json/csv/markdown` 摘要
- `async-runtime/`
  - Styio task scheduler 与 C++ / Go / Rust 的异步运行时横向基准框架
- `tools/parser-shadow-suite-gate.sh`
  - parser shadow gate 脚本
- `tools/benchmark-env-check.py`
  - 只读环境清单：本地受控墙钟基准前检查 CPU 可用性/亲和性、后台负载、频率 governor、turbo/boost、电源
- `tools/benchmark-compare.py`
  - baseline-vs-head 报告比较器：方向感知 median 变化 + Mann-Whitney U 证据，输出 regression/improvement/inconclusive/insufficient-sample/incompatible-input
- `tools/soak-minimize.sh`
  - soak 失败二分最小化
- `scripts/benchmark-golden-gate.py`
  - 契约门禁：byte-compile、历史与新 fixture 校验、快速能力测试、工具 smoke、依赖与 CI 边界断言
- `tests/`
  - 快速能力契约测试与确定性报告 fixture
- `docs/REGRESSION-TEMPLATE.md`
  - 回归记录模板
- `docs/COVERAGE-MATRIX.md`
  - 按编译阶段与模块切面的 benchmark 覆盖矩阵
- `regressions/`
  - 最小化失败工件目录
- `reports/`
  - 本地 benchmark 运行产物目录（默认不入库）

## 路线

### 快速

```bash
./tools/perf-route.sh --styio-root /path/to/styio --quick
```

### 标准

```bash
./tools/perf-route.sh --styio-root /path/to/styio --phase-iters 5000 --micro-iters 5000 --execute-iters 20
```

### 深跑

```bash
./tools/perf-route.sh --styio-root /path/to/styio --phase-iters 5000 --micro-iters 5000 --execute-iters 20 --deep-soak
```

这三条路线都会覆盖：

- compiler stage benchmark matrix（`tokenize/parse/type/lower/llvm_ir`）
- compiler micro benchmark matrix（`lexer/parser/type/lower/llvm` 热点切面）
- full-stack workload matrix（CLI wall-clock）
- native C++ comparison routes（`full-cli/cached-jit/runtime-only`）
- compiler error-path benchmark matrix（`lex/parse/type/runtime` 失败路径）
- parser engine 回归
- pipeline guard rail
- parser/security guard rail
- parser shadow gates
- soak smoke
- 可选 `soak_deep`

## 统计与规模口径

- 两个 runner 对每个核心指标输出 `median`、确定性 bootstrap 95% CI（固定种子 10000 次重采样）、`CV`，并对匹配的 C++ 基线输出双侧 Mann-Whitney U 显著性；报告 JSON 字段为增量扩展，旧字段全部保留。
- baseline 默认重复 10 次；`async-runtime/run-async-bench.py` 与 `native-cpp/run-native-cpp-bench.py` 均支持 `--scale small/medium/large`，显式 sizing/repeats 参数优先级最高；async smoke 保持原有尺寸与 1 次重复。
- 真实墙钟基准只在受控本地执行：先跑 `tools/benchmark-env-check.py` 确认环境，再用 `tools/benchmark-compare.py` 对比存档 baseline 与新结果。共享 CI 只跑契约测试与门禁，不运行任何墙钟基准 job。

覆盖设计与补强方向见 [COVERAGE-MATRIX.md](./COVERAGE-MATRIX.md)。

每次运行默认会把产物写到 `reports/<timestamp>/`，包括：

- `metadata.tsv`
- `sections.tsv`
- `logs/*.log`
- `results.json`
- `benchmarks.csv`
- `summary.md`

也可以显式指定：

```bash
./tools/perf-route.sh \
  --styio-root /path/to/styio \
  --phase-iters 5000 \
  --micro-iters 5000 \
  --execute-iters 20 \
  --error-iters 50 \
  --label baseline \
  --out-dir reports/first-full-baseline
```

## Compiler Stage Benchmark

这是 opt-in benchmark，默认不会进 `ctest -L soak_smoke`，需要显式设置迭代次数：

```bash
STYIO_SOAK_PHASE_BENCH_ITERS=5000 \
./build/bin/styio_soak_test \
  --gtest_filter=StyioSoakSingleThread.FrontendPhaseBreakdownReport
```

核心输出字段：

- `tokenize_us`
- `parse_us`
- `type_us`
- `lower_us`
- `llvm_ir_us`
- `parse_share_pct`
- `lower_share_pct`
- `llvm_ir_share_pct`
- `avg_token_arena_kib`
- `avg_ast_arena_kib`
- `rss_growth_kib`

当前 matrix 覆盖这些模块切面：

- `Scalar`: `scalar_core`
- `Bindings`: `bindings_chain`
- `Functions`: `function_block_body`
- `ControlFlow`: `control_match`
- `Collections`: `dict_heavy`
- `Resources`: `resource_file_io`
- `Streams`: `stdin_transform`, `stream_zip_files`
- `StateAndSeries`: `snapshot_state`, `series_window_avg`, `state_pulse_inline`
- `Topology`: `topology_ring`
- `Mixed`: `mixed_full_pipeline`（compile-only）

## Full-Stack Workload Matrix

这也是 opt-in benchmark，用来冻结 CLI 从解析到执行的总墙钟时间：

```bash
STYIO_SOAK_EXECUTE_BENCH_ITERS=20 \
./build/bin/styio_soak_test \
  --gtest_filter=StyioSoakSingleThread.FullStackWorkloadMatrixReport
```

核心输出字段：

- `cli_wall_us`

[`tools/perf-route.sh`](../tools/perf-route.sh) 默认会一起执行；如果不传 `--execute-iters`，脚本会根据 `--phase-iters` 自动推导一个较小的执行迭代数。

## Compiler Error-Path Benchmarks

这是 opt-in benchmark，用来冻结失败路径的诊断成本，并验证错误类别和退出码保持稳定：

```bash
STYIO_SOAK_ERROR_BENCH_ITERS=50 \
./build/bin/styio_soak_test \
  --gtest_filter=StyioSoakSingleThread.CompilerErrorPathBenchmarksReport
```

当前第一批 error-path benchmark 覆盖：

- `lex.unterminated_block_comment`
- `parse.empty_match_cases`
- `type.final_then_flex_i64`
- `runtime.read_missing_file`

核心输出字段：

- `category`
- `error_us`
- `exit_code`
- `diagnostic_code`
- `avg_diag_bytes`

[`tools/perf-route.sh`](../tools/perf-route.sh) 默认也会跑这一组；如果不传 `--error-iters`，脚本会根据 `--phase-iters` 自动推导一个较小但稳定的失败路径迭代数。

## Compiler Micro Benchmarks

这是比 stage matrix 更窄的切面基准，用来盯住热点模块本身，而不是整条 workload 的阶段占比：

```bash
STYIO_SOAK_MICRO_BENCH_ITERS=5000 \
./build/bin/styio_soak_test \
  --gtest_filter=StyioSoakSingleThread.CompilerMicroBenchmarksReport
```

当前第一批 micro benchmark 覆盖：

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

核心输出字段：

- `focus`
- `focus_us`
- `avg_tokens`
- `avg_token_arena_kib`
- `avg_ast_arena_kib`
- `rss_growth_kib`

[`tools/perf-route.sh`](../tools/perf-route.sh) 默认也会跑这一组；如果不传 `--micro-iters`，它默认继承 `--phase-iters`。

## Native C++ Comparison

Styio 与手写原生 C++ 的对照基准由本仓库维护，不放回 Styio 主仓库：

```bash
native-cpp/run-native-cpp-bench.py \
  --styio-root /path/to/styio \
  --routes all \
  --line-count 100000 \
  --line-bytes 48 \
  --repeats 10 \
  --out-dir reports/native-cpp-stdin-echo
```

三条标准路线：

- `full-cli`
  - Styio CLI vs `-O3 -std=c++20` 原生 C++
  - Styio 侧包含 source read、parse、lowering、LLVM/JIT 和执行
- `cached-jit`
  - 预留给未来可复用编译/JIT 产物执行入口
  - 当前报告为 `unsupported`，不能用 full CLI 冒充
- `runtime-only`
  - 原生 C++ vs 直接调用 Styio 导出 C runtime ABI 的 harness
  - 当前用于隔离标准流 runtime helper 成本，不等同于生成后的 Styio 程序执行

[`tools/perf-route.sh`](../tools/perf-route.sh) 默认会把这三条路线作为 `native_cpp_comparison` section 跑入总报告目录，并把结果并入 `summary.md / benchmarks.csv / results.json`。

## 测试面

常驻 smoke / soak 回归：

- `StyioSoakSingleThread.TokenizerIngestionLoop`
  - 高频词法摄入循环（长输入 + 多轮 tokenization）
- `StyioSoakSingleThread.FileHandleLifecycleLoop`
  - 文件句柄长生命周期循环（open/read/rewind/close）
- `StyioSoakSingleThread.FileHandleMemoryGrowthBound`
  - 文件句柄循环的 RSS 增长阈值守卫（防回归泄漏）
- `StyioSoakSingleThread.ConcatMemoryGrowthBound`
  - 字符串拼接/释放循环的 RSS 增长阈值守卫（内存安全 Safety）
- `StyioSoakSingleThread.InvalidHandleDiagnosticsLoop`
  - 非零非法句柄高频误用诊断循环（rewind/read/write 置错，close 保持 no-op）
- `StyioSoakSingleThread.StreamProgramLoop`
  - M6 流式程序重复执行回归（`t02_running_max`）
- `StyioSoakSingleThread.StateInlineHelperProgramLoop`
  - 单参数 state helper（直返 `StateDecl`）在 pulse 体中调用的长跑回归（输出稳定 `1/3/6`）
- `StyioSoakSingleThread.StateInlineMatchCasesProgramLoop`
  - 单参数 state helper 使用 `?= { ... }` 更新表达式的长跑回归（输出稳定 `10/12/15`）
- `StyioSoakSingleThread.StateInlineInfiniteProgramLoop`
  - 单参数 state helper 使用 `[...]`（`InfiniteAST`）更新表达式的长跑回归（输出稳定 `0/0`）

Opt-in benchmark：

- `StyioSoakSingleThread.FrontendPhaseBreakdownReport`
  - 编译阶段 matrix benchmark，输出 `tokenize/parse/type/lower/llvm_ir` 单次耗时、阶段占比、arena 平均占用和 RSS 增长
- `StyioSoakSingleThread.CompilerMicroBenchmarksReport`
  - 编译器微基准 matrix，输出 `lexer/parser/type/lower/llvm` 热点切面的单次耗时与 arena/RSS 指标
- `StyioSoakSingleThread.FullStackWorkloadMatrixReport`
  - CLI end-to-end matrix benchmark，输出模块切面的单次墙钟耗时
- `StyioSoakSingleThread.CompilerErrorPathBenchmarksReport`
  - CLI 失败路径 matrix benchmark，输出 `lex/parse/type/runtime` 诊断的单次墙钟耗时、退出码与诊断码
- `async-runtime/run-async-bench.py`
  - 异步运行时横向基准，输出 Styio / C++ / Go / Rust 在 `sleep` 并发收敛与 `noop` fanout 开销上的 JSON、CSV 与 Markdown 报告

## C ABI 指针约定

- `styio_file_read_line` 返回借用指针（线程本地缓冲），不可 `styio_free_cstr`。
- `styio_strcat_ab` 返回堆内存，必须配对 `styio_free_cstr`。

## 运行

```bash
cmake -S . -B build
cmake --build build --target styio_soak_test -j8
ctest --test-dir build -L soak_smoke --output-on-failure
```

## 放大量（本地/夜间）

```bash
ctest --test-dir build -L soak_deep --output-on-failure
```

`soak_deep` 档位由 Styio 主仓库的 `benchmark/CMakeLists.txt` 注入放大量参数：

- `STYIO_SOAK_LEXER_ITERS=5000`
- `STYIO_SOAK_INGEST_LINES=4096`
- `STYIO_SOAK_FILE_ITERS=12000`
- `STYIO_SOAK_FILE_LINES=256`
- `STYIO_SOAK_MEM_ITERS=8000`
- `STYIO_SOAK_MEM_FILE_LINES=128`
- `STYIO_SOAK_RSS_GROWTH_LIMIT_KIB=98304`
- `STYIO_SOAK_CONCAT_ITERS=6000`
- `STYIO_SOAK_CONCAT_CHAIN=16`
- `STYIO_SOAK_CONCAT_SEG_BYTES=128`
- `STYIO_SOAK_CONCAT_RSS_GROWTH_LIMIT_KIB=98304`
- `STYIO_SOAK_INVALID_HANDLE_ITERS=120000`
- `STYIO_SOAK_STREAM_ITERS=1500`
- `STYIO_SOAK_STATE_INLINE_ITERS=1500`
- `STYIO_SOAK_STATE_MATCH_ITERS=1500`
- `STYIO_SOAK_STATE_INFINITE_ITERS=1500`

说明：PR/CI 默认跑 `soak_smoke`，nightly 跑 `soak_deep`。

## 失败最小化与回归模板（D.5）

当 `soak_deep` 失败时，先用二分脚本找最小失败阈值，再沉淀回归样本：

```bash
./tools/soak-minimize.sh \
  --test StyioSoakSingleThread.FileHandleMemoryGrowthBound \
  --var STYIO_SOAK_MEM_ITERS \
  --low 100 \
  --high 8000 \
  --extra-env "STYIO_SOAK_MEM_FILE_LINES=128;STYIO_SOAK_RSS_GROWTH_LIMIT_KIB=98304"
```

非法句柄诊断长跑回归可用：

```bash
./tools/soak-minimize.sh \
  --test StyioSoakSingleThread.InvalidHandleDiagnosticsLoop \
  --var STYIO_SOAK_INVALID_HANDLE_ITERS \
  --low 2000 \
  --high 120000
```

产物落在 `regressions/<timestamp>-<case>/`。
回归记录模板见：

- `docs/REGRESSION-TEMPLATE.md`
- `regressions/README.md`
