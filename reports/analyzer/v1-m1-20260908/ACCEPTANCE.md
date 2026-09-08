# Styio Analyzer v1 M1：Codex 独立验收

日期：2026-09-08。delivery-id：`v1-m1-20260908`。

**结论：不通过，修复后重新提交 M1 验收。** 发现 4 项 P1 阻断问题和 1 项
P2 测试清理项。阻断原因是报告可信性、可比条件和失败处理不满足规格。
真实测量出现噪声或性能退化，本身不影响本轮工具建设的验收。

依据为 [HANDOFF.md](HANDOFF.md)、[修改清单](SOURCE-CHANGES.md)、
[v1 行为规格](../../../docs/STYIO-ANALYZER-V1.md) 和
[实现计划及 AC 矩阵](../../../docs/STYIO-ANALYZER-IMPLEMENTATION-PLAN.md)。
本结论针对交接所列的未提交 M1 实现，不把研究语料、parity-v2、nightly
等其他任务的工作树差异计入本次缺陷。本次验收只新增本文，未修源码、重跑
真实性能实验、执行完整回归、创建提交或进入 M2。

## 已确认的有效工作

- 共享采样核心确实存在；`standard_parity_gate.py` 的适配器调用
  `measurement_core.measure_paired_cell`，未发现第二套采样器主体。
- Analyzer 两侧均编译 catalog 的 Styio 源程序，并分别检查独立 oracle。
  代码中存在真实配对采集、相同 batch count、计时外原生预构建、无 RSS
  观察器的时间采集，以及另行执行的 RSS 重放。
- [真实 A/A](aa/results.json) 有 3 个 cell，
  [真实 A/B](ab/results.json) 有 9 个 cell。每个 cell 的两侧均有
  11 个时间样本和 11 个 RSS 样本，三条路线都有实际数值。
- 使用独立的 Python 标准库计算，抽核上述 12 个 cell 的批次归一化、
  两侧中位数、CV、配对几何均值比，以及产物字节差和比值，未发现算术
  不一致。未另写 bootstrap 实现逐一证明所有区间端点。
- 原始报告的时间状态全部是 `inconclusive`；A/A 的 3 个 RSS 状态均为
  `no_detected_change`；A/B 的 RSS 为 7 个 `regressed`、2 个
  `no_detected_change`。这些是交付报告的观测记录，可比性限制见 F3。
- 对交接目录原有 10 个文本资产做本机路径和常见凭据模式的定向扫描，
  未命中。该扫描不等同于对任意输入的完整隐私证明。

## F1 — P1：verify 未核对报告中实际展示的统计与结论

位置：[tools/styio_analyzer.py](../../../tools/styio_analyzer.py)
`_recompute_cell`，主要为第 589–605、606–619、620–645 行；
`render_summary` 在第 362–371 行消费这些报告字段。

时间统计重算后只比较 `geomean_ratio`，没有核对中位数、CV 和区间等展示
字段。RSS 未核对比较状态；配对次序仅在字段是非空列表时检查；产物体积
差值没有复算对照。批次时长是否达标仍信任报告中的布尔标记。

独立复现使用真实 A/A JSON 的内存深拷贝，每个反例重新从原始报告开始，
不改磁盘上的性能证据。调用当前 `verify_report` 得到：

| 反例 | 修改 | 当前结果 | 必须达到的行为 |
|---|---|---|---|
| F1-a | 首个 cell 的 `time.candidate_median` 改为 `0.000001`，区间上下界改为 `0.00001`、`0.00002` | `pass`，无 reason code | 拒绝与原始样本不一致的展示统计 |
| F1-b | 首个 cell 的 `rss_comparison_status` 改为 `improved`，原因改为 `interval_below_one`，`artifact_bytes.delta` 改为 `-999999999` | `pass`，无 reason code | 拒绝错误的 RSS 结论和体积差值 |
| F1-c | 删除首个 cell 的 `pair_orders` | `pass`，无 reason code | 对声称完整采集的 cell 要求有效配对证据 |

影响：JSON 可展示错误的性能数字或方向，仍取得“报告一致性通过”的结论。
这违反 AC-05，也使 AC-04 的数学测试不足以证明端到端报告可靠。

最小修复：从原始数据生成同一份规范化派生结果，校验所有会展示或用于
决策的字段；从原始批次值重算时长条件，校验 RSS 状态、体积差值及必要
配对信息。复用现有统计实现，不再增加一套数学引擎或哈希体系。
定向测试必须覆盖上述反例，并确认如实缺失的指标仍可正常披露。

## F2 — P1：覆盖与完成状态能被虚增，合法失败报告反而被拒

位置：[tools/styio_analyzer.py](../../../tools/styio_analyzer.py)
第 678–693、716–726 行；缺失工具链的合法披露由第 410–445 行生成。

选择校验只要求请求 ID 是 catalog 预期 ID 的子集，没有要求所选程序族的
三条路线齐全，也未将 cell 的工作量、算法和 oracle 身份与 catalog
逐项对应。`collection_complete` 直接采用报告的值，已算出的失败集合
没有与未完成披露核对。

同样使用真实 A/A 的独立内存副本复现：

| 反例 | 修改或触发 | 当前结果 |
|---|---|---|
| F2-a | 保留所选 family/scale，清空 `cells` 及 requested/observed/incomplete ID 列表，仍声明完成 | `pass`，`cell_count=0`，`collection_complete=true` |
| F2-b | 只留首个 cell，并把 requested/observed ID 改成该 cell；family 不变 | `pass`，`cell_count=1`，`collection_complete=true` |
| F2-c | 首个 cell 的 candidate 正确性改为 false，仍声明完成且无 incomplete ID | `pass`，同时返回 `incomplete_cell_count=1` 和 `collection_complete=true` |
| F2-d | 首个 cell 的 `work_units=999999`、`algorithm_id=different-work`、`expected_output_digest` 改为 64 个零 | `pass`，无 reason code |
| F2-e | 实际调用 compare 的缺失工具链分支，生成包含 requested ID、逐项未执行原因且完成状态为 false 的报告 | `fail`，原因是 `incomplete_disclosure`、`selection_mismatch` |

影响：遗漏难测路线或改变 workload 身份不会使离线校验失败；反过来，如实
披露“尚未执行”的报告无法通过一致性校验。违反 AC-05、AC-06。

最小修复：以 catalog、family 和 scale 导出的完整 ID 集合作为选择依据；
每个请求 cell 必须有观测记录或明确的未执行记录，拒绝重复 ID 和身份
不一致。由这些记录统一推导完成状态及未完成项，再与报告核对。合法的
失败或部分报告应通过一致性校验，同时保持 `collection_complete=false`。
用 F2-a 至 F2-e 做行为测试，无需新增任务状态数据库。

## F3 — P1：实际 Clang 与运行时绑定未核实，就声明工具链可比

位置：[tools/styio_analyzer.py](../../../tools/styio_analyzer.py)
第 168–197、449–480 行，尤其第 452 行：
`comparable = clang.get("present") is True`。

Analyzer 记录的是 PATH 中的 Clang，优化、LTO、线程配置使用固定值。
但只读核对当前 `styio-nightly` 的 `src/main.cpp` 可见，实际原生 C++
编译器依次考虑 `STYIO_NATIVE_CXX`、内嵌 CMake 配置和工具链目录，之后
才使用 PATH（第 5073–5113 行）。运行时源码也优先使用内嵌配置，再从
可执行文件位置寻找（第 4218–4240 行）；设置两侧 cwd 不能证明它们分别
使用对应的运行时输入。

受控复现：在临时目录准备版本为 `99.0.0` 的 CXX 替身，仅在审查子进程
内通过 `STYIO_NATIVE_CXX` 指定它。`external_clang_identity()` 仍记录
`21.0.0` 并返回 `present=true`。替身只用于检查身份解析，没有运行真实性能
实验；临时文件已清理。结合 Styio 的实际选择代码，足以证明记录的外部
工具链可能并非测量使用的工具链。

交接已发现旧二进制内嵌运行时路径的问题，并通过隔离解包和构建处理那次
A/B。这个处理有价值，但 Analyzer 本身仍未验证绑定。**本发现不声称已
证明本次 A/B 实际混用了 Clang 或运行时；结论是现有实现和记录不足以
支持“已确认可比”。** Git root 的 HEAD 和编译器文件标识也不能单独证明
该编译器来自这个源码树。

影响：Clang、目标或构建配置差异可能被当作 Styio 版本差异；错误的运行时
绑定可能混淆两侧身份。违反 AC-01 和规格中的可比条件。

最小修复：在 Analyzer 适配层确认或固定两侧实际使用的外部编译器，并
记录真实公开配置；用现有构建配置、构建凭证或实际执行证据核实对应
运行时来源及目标。无法确认时明确标为不可比。无需修改 Styio 源码，
无需建立递归哈希或新的证明系统。增加环境覆盖、不同内嵌配置和无法确认
运行时绑定的受控案例，断言实际执行与报告一致。

## F4 — P1：RSS 后期失败会丢弃已经采完的时间样本

位置：[tools/measurement_core.py](../../../tools/measurement_core.py)
第 495–498、655–662、723–730 行；
[tools/styio_analyzer.py](../../../tools/styio_analyzer.py) 第 500–509 行。

时间样本只保存在采样函数的局部数组中，直到全部 RSS 重放完成才返回。
后期异常传到 compare 后，被替换成一条新建的 incomplete 记录，并把
两侧正确性都写为 false。

受控复现保留真实 compare 和共享核心的采样循环，只替换执行 helper 与
预检：两侧预检、oracle 和时间调用均成功，单批时间设为 1 秒；在第一次
RSS 重放抛出 `ReasonError("audit_rss_failure")`。观察到：

| 观察项 | 实际结果 |
|---|---|
| 时间调用 | 30 次：2 次校准、6 次预热、22 次保留采样 |
| 已完成的保留时间数据 | 11 对 |
| RSS 尝试 | 1 次，受控失败 |
| 最终报告中的时间样本 | 缺失 |
| 最终正确性 | baseline=false、candidate=false |
| 完成状态与原因 | false；`audit_rss_failure` |

这是失败处理测试夹具，不是真实性能数据。它证明昂贵的已完成测量会被
丢失，且采集失败被误写成两侧程序结果错误。违反规格“保留所有已采集
数值”，影响 AC-05、AC-06。

最小修复：让采样函数在失败时返回已有数据和具体失败阶段，保留已核实的
正确性及成功样本；仅把受影响指标标为不完整。若只有单侧样本，也应保留
并披露未成对，不能作为完整配对参与方向判断。定向验证至少覆盖 RSS
后期失败和保留采样中途失败。无需增加逐样本磁盘检查点或自动重跑机制。

## F5 — P2：替身测试存在空断言，迁移检查被固化为常驻测试

位置：[tests/test_styio_analyzer.py](../../../tests/test_styio_analyzer.py)
第 183–216、505–514 行。

第 216 行的摘要差异断言带有 `or True`，永远通过。该隔离测试的替身日志
也没有记录 cwd 或运行时绑定，因此测试名称与 AC-01 的证明范围不一致。
第 510–514 行永久检查旧源码字符串是否消失，与实现计划要求的“一次性
源码检索，不建立永久迁移检查门禁”冲突。

删除空断言，补充能证明实际调用绑定的行为断言；移除常驻源码字符串迁移
检查，把迁移核查留在一次性交接证据中。保留有效的 parity 行为测试，
并加入 F1–F4 的行为反例，不通过弱化规格或断言消除验收缺口。

## AC 复核与验证边界

| AC | 独立验收状态 | 依据与限制 |
|---|---|---|
| AC-01 | 不通过 | 分别解析两侧编译器已实现；外部工具链、运行时绑定和对应身份仍有 F3、F5 缺口 |
| AC-02 | 已有正向证据 | 两侧同一 Styio 语料和独立 oracle 的实现已审阅；沿用交接的定向测试记录；失败记录保真另见 F4 |
| AC-03 | 已有正向证据 | 三路线与计时/RSS 分离的代码及调用边界成立；不能据此推导 F3 的环境可比性 |
| AC-04 | 数学基础有证据，端到端未闭合 | 交接有固定数值测试，真实原始数据抽算一致；F1 允许展示统计与结论不一致 |
| AC-05 | 不通过 | F1、F2；F4 还造成原始证据丢失；已有隐私定向扫描未命中 |
| AC-06 | 不通过 | F2、F4 使覆盖、未完成和缺失指标披露不可靠 |
| AC-07 | 已有正向证据 | 共享核心调用已确认；沿用交接的既有 parity 绿色回归记录，未重复运行完整套件 |
| AC-08 | 真实证据已提供，可比条件待补 | A/A 3 cells、A/B 9 cells 及复算文件齐全；原始数字抽算一致；F3 限制性能归因 |
| AC-09 | 未完成验收 | 交接和回归记录存在；需修复 F1–F5 并提交与修复状态对应的新证据 |

[定向记录](targeted-verification.txt) 的 38 passed，以及
[最终回归记录](final-regression.txt) 的 47 passed、1 skipped，均为实现者
交付的历史证据。本次未将这些数字写成独立重跑结果；缺少
`build/perf-parity-baseline` 的 parser scaling 跳过仍不算通过。

本次独立执行的是原始 JSON 数值抽算、原始 verify 调用、内存报告变异、
缺失工具链报告验证、受控 RSS 失败和 CXX 身份覆盖反例；另做定向隐私扫描
与有关 tracked 文档/适配器的 diff 格式检查。原始 A/A、A/B 的 verify
虽都返回 pass，但 F1、F2 表明这个通过结果不足以完成 AC-05 验收。

## 给 Cursor 的 M1 修复范围

1. 修复 F1–F4，并完成 F5 的测试清理。复用现有共享核心和 catalog，
   修改限制在 Analyzer、必要共享核心/适配器、对应测试和文档。继续保持
   Styio 编译器源码只读，不进入 M2 或顺便优化被测程序。
2. 先用上述反例及正常案例做定向验证，再自审所有范围内变更。修复后的
   verify 必须拒绝矛盾证据，同时接受如实披露的失败和不可比报告。
3. 保留本次原始报告和本验收记录。用修复后的 verifier 重新检查已有
   A/A、A/B；如果历史报告缺少必要配置证据，如实标注，不能补写未经
   核验的事实。优先核实已有隔离构建的凭证，仅在证据确实无法补足或
   采集行为改变时补做相应的最小真实演示，不重跑整套 reference。
4. 全部修改、自审、范围内修复和定向验证完成后，再执行一次最终完整
   回归。若完整回归失败，先诊断并提交具体建议，后续修复和是否重跑由
   开发者决定；不得自动反复运行。修复源码后不能继续用旧的绿色回归
   记录证明新状态。
5. 在修复交接中逐项链接 F1–F5 的改动及新证据，停在“待 Codex 独立验收”。
   无需改写本次历史交接来隐藏原有结论。
