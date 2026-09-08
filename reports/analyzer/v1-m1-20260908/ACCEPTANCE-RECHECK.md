# Styio Analyzer v1 M1：修复后的独立复验

日期：2026-09-08。delivery-id：`v1-m1-20260908`。

**结论：仍不通过。** F4 的晚期失败保留已经验证有效，上次 F1/F2 的具体
反例也已修复；但 F1/F2/F3 尚有未闭合分支，F3 的编译器固定还引入一项
实际链接回归。下文列出 4 项 P1 修复要求，以及下一次交付需要的最小真实
证据。继续保持 M1 范围。

依据：[更新后的交接](HANDOFF.md)、[第一次验收](ACCEPTANCE.md)、
[v1 规格](../../../docs/STYIO-ANALYZER-V1.md) 和
[实施及验收计划](../../../docs/STYIO-ANALYZER-IMPLEMENTATION-PLAN.md)。
本文是独立复验记录，不覆盖原始 A/A、A/B 或第一次验收结论。本次只新增
本文；审查中的脚本、替身、二进制副本和链接产物均放在临时目录并已清理。
未改 Analyzer 或 Styio 源码，未重跑完整 pytest 或真实性能采样。

## 已确认修复的行为

以下结果来自本次独立执行的内存反例和受控执行，不是复述绿色测试数量。
合成报告使用现有固定数值夹具作为正常对照，再独立改变待检查字段。

| 原问题或对照 | 本次结果 |
|---|---|
| 正常的三路线固定数值报告 | `verify=pass`，采集完整 |
| F1：错误中位数、区间 | 拒绝；`ratio_mismatch`、`interval_mismatch` |
| F1：错误 RSS 状态和体积差值 | 拒绝；`rss_status_mismatch`、`artifact_delta_mismatch` |
| F1：完整报告删除配对次序 | 拒绝；`sample_schedule` |
| F2：空 cells 或删去两条路线仍声称完整 | 拒绝，并推导出未完成数量及 `collection_complete=false` |
| F2：正确性失败仍声称完整 | 拒绝，推导为未完成 |
| F2：改工作量、算法及 oracle digest | 拒绝；`cell_identity_mismatch` |
| F2：compare 实际生成缺失编译器披露 | `verify=pass`，0 个观测、3 个未完成、采集未完成 |
| F4：首个 cell 的第一次 RSS 重放失败 | 保留 11 对时间值与两侧预检正确性；整份报告 `verify=pass`，采集未完成 |
| F4：首个 cell 第三对 retained 的第二侧失败 | 保留 2 个完整时间对及 1 个未配对值，保留预检正确性；整份报告 `verify=pass`，采集未完成 |

F4 复现保留真实 compare 和共享核心循环，预检执行 Styio 替身，采样 helper
使用固定 1 秒及固定 RSS，并只在指定调用注入失败。RSS 失败案例的三路线
共发生 90 次时间调用；retained 失败案例共发生 74 次。它们验证执行与
保存行为，不作为性能成绩。

F5 的 `or True` 和旧函数体消失检查已删除，替身也增加了 cwd、环境覆盖
和 marker 日志。仍在的采样入口源码字符串断言本身不能证明调用行为；
本次通过源码审阅确认适配器确实使用共享核心，不以该断言作为独立证据。

## R1 — P1：缺少 CMake 凭证不代表内嵌运行时路径失效

对应原 F3。位置：[tools/styio_analyzer.py](../../../tools/styio_analyzer.py)
第 314–330 行，特别是第 323–330 行。

`runtime_binding_status` 在没有可读 CMake 源目录时直接从可执行文件向上
寻找 `ExternLib.cpp`，找到后就返回 `runtime_binding=confirmed`。但 Styio
的真正优先级是：先使用仍有效的内嵌 `STYIO_SOURCE_DIR`，再考虑从 exe
上溯。旁边没有 CMakeCache，无法证明二进制中的内嵌目录不存在或失效。

本次用真实 `styio-nightly` 的 `build/default/bin/styio` 做了受控复现：

1. 复制该可执行文件到临时的另一棵树中；该树具有运行时 marker，没有
   CMakeCache。原源码树保持存在且只读。
2. 以临时树作为 intended root 调用 Analyzer 的绑定及可比性判断。
3. 用搬迁后的真实 Styio 处理 scalar-chain 源程序，通过临时 CXX 拦截器
   读取实际原生构建入参；拦截器主动停止链接，不产生性能测量。

| 检查 | 观察结果 |
|---|---|
| Analyzer 的绑定 | `confirmed`，依据 `walk_from_compiler` |
| Analyzer 的可比性 | `true`，无不可比原因 |
| 实际 CXX 调用 | 成功截获 |
| 实际调用包含原源码树的 `ExternLib.cpp` | true |
| 实际调用包含 intended root 的 `ExternLib.cpp` | false |

因此这是实际绑定不一致，不只是缺少字段。对应源码依据为
`styio-nightly:src/main.cpp` 第 4218–4248、5570–5579、5689–5733 行。
拦截链接所产生的非零退出码是预期测试行为，不是新增的 Styio 缺陷。

修复要求：只有可核对的构建凭证或实际调用证据才能确认内嵌/回溯选择；
不能从“旁边没有配置文件”推导出内嵌路径无效。无法确认时返回 unconfirmed
和不可比。保留搬迁二进制、原运行时仍存在的行为案例；不要靠扩大目录
搜索或补写 marker 字段解决，也不需要新的递归哈希体系。

## R2 — P1：固定 CXX 时去掉符号链接名称，改变 Clang 驱动语义

这是 F3 修复引入的新回归。位置：
[tools/styio_analyzer.py](../../../tools/styio_analyzer.py)
第 224–227、249–260、280–285 行；解析结果在第 393–396 行写入子进程
的 `STYIO_NATIVE_CXX`。

几条解析分支都返回 `Path.resolve()`。`clang++` 常作为指向同一个 Clang
可执行文件的调用名称，但调用名称参与驱动模式选择；转换成名为 `clang`
的物理路径会丢掉 C++ 驱动模式。

本次在临时目录建立 `clang++` 指向真实 `clang` 的符号链接，作为显式
`STYIO_NATIVE_CXX` 输入；随后对同一份使用 `std::cout` 的最小 C++ 程序
分别执行原调用路径和 Analyzer 解析后的路径，参数相同：

| 调用 | 驱动计划含 C++ 运行库 | 实际链接退出码 | 产物 |
|---|---|---|---|
| 用户指定的 `clang++` 链接名称 | true | 0 | 已生成 |
| Analyzer 固定后的 `clang` 物理路径 | false | 1 | 未生成 |

这验证了真实编译器的行为；不涉及 Styio 性能采样。当前替身只检查环境变量
是否存在，并不执行相应 CXX，因而无法发现这类回归。

修复要求：保留用于执行的绝对调用路径及名称。可以另行读取物理目标来
识别文件，不能把物理目标无条件代替调用路径。增加经过解析、子进程传递
及实际 C++ 链接的最小案例；同一文件内容也不能单独证明驱动模式相同。

## R3 — P1：不完整样本绕过归一化、配对和展示统计检查

对应原 F1，并影响 F4 保存下来的证据。位置：
[tools/styio_analyzer.py](../../../tools/styio_analyzer.py)
第 867–901、946–949 行；
[tools/measurement_core.py](../../../tools/measurement_core.py)
第 280–282 行。

`positive_samples` 只有在数组长度等于目标 repetitions 时才返回值。
因此目标 11 对而实际只采完 2 对的合法失败报告，会把已有样本视为 None，
跳过归一化与配对检查，也跳过展示统计的复算。允许“不完整”不应等同于
放弃检查已经保存的数据。

独立复现包括真实 compare 循环产生的 retained 失败报告，以及固定数值
的合法部分报告；每次从各自正常对照深拷贝，磁盘原始证据不变：

| 反例 | 当前结果 |
|---|---|
| 2 对 raw 值均为 1 秒、batch count 为 1，只把一个 candidate 归一化值改成 1,000,000 秒 | `verify=pass`，无 reason code；实际 compare 输出的副本也可复现 |
| 删除部分报告的 `pair_orders` | `verify=pass` |
| 给部分报告加入错误的 time 中位数、比值 `0.000001` 及相应错误区间 | `verify=pass`；摘要仍读取这些展示字段 |

完成状态仍是 false，这一点已经修复；但“报告一致性通过”依然错误。

修复要求：把“样本是否足够形成性能结论”与“已采样本是否有效、一致”分开。
对已有的完整配对及未配对值检查类型、归一化和实际次序，按完成前缀核对
配对信息。保留数据不足的披露；出现派生统计时复算，或者明确不输出该
统计并拒绝残留的矛盾字段。无需另建统计引擎或强迫缺失样本补齐。

## R4 — P1：覆盖与 workload 身份仍有互相矛盾却通过的字段

对应原 F2。位置：[tools/styio_analyzer.py](../../../tools/styio_analyzer.py)
第 1086–1100 行；摘要在第 544–549 行显示范围。

三路线的 ID 集合现在会与 catalog 对齐，但 cell 身份校验遗漏
`input_digest`、`source_digests`；能力披露也未与实际 selection 同源校验。

在正常的 scalar-chain 三路线合成报告上分别复现：

| 修改 | 当前结果 | 矛盾 |
|---|---|---|
| 将首个 cell 的 input digest 和 Styio/C++ source digests 改为 64 个零 | `verify=pass` | cell 的源与输入身份不匹配已有 catalog |
| 仅把 `capabilities.selected_is_full_catalog` 改为 true，把 `capabilities.selected_family_ids` 改为全部 11 族；实际 selection 与 3 个 cell 不变 | `verify=pass`，采集完整 | 摘要声称 `Full catalog selected: True`，实际仅 1 族 |

修复要求：用同一份由 catalog 和 selection 导出的预期值核对 workload
身份、能力排除和覆盖披露，包含现有源/输入 digest 字段。无需增加新的
hash，只需比较报告已经保存的身份与唯一 catalog。为这两个跨字段矛盾
补充行为测试，避免只覆盖单个被修改字段的上一版反例。

## 真实证据与回归记录

本次独立重新调用 verifier 检查
[原 A/A](aa/results.json) 和 [原 A/B](ab/results.json)，结果与交付的
[A/A 修后复算](aa/recompute-after-fix.json)、
[A/B 修后复算](ab/recompute-after-fix.json) 一致：

- 分别有 3 和 9 个 cell，`collection_complete=true`，未完成数量为 0。
- 唯一拒绝原因是 `toolchain_binding_unconfirmed`。
- 没有出现新增的统计、归一化或覆盖 mismatch；本轮沿用第一次验收的独立
  数值抽算，不另写第二套 bootstrap。

不编造历史绑定事实的处理正确。不过，本次改变了实际 CXX 的选择和调用，
且交接明确历史绑定凭证已不可取得；现有历史 JSON 不能证明修复后适配器
成功执行了配置可核实的真实 A/A、A/B。AC-08 仍需要在 R1/R2 修复之后
补交计划规定的最小 smoke 演示：A/A 为 scalar-chain 三路线；A/B 为
scalar-chain、control-diamonds、dense-matmul 三族九路线。保留旧报告，
将新证据写到新的明确目录；无需运行全 11 族 reference。

[定向 45 passed](targeted-verification.txt) 和
[最终 49 passed、6 skipped](final-regression.txt) 是实现者提供的记录，
本次没有重复执行完整套件。parser scaling 与 5 个 async-runtime 的跳过
仍不算通过；下一次最终回归按原计划指定可用的 Styio checkout，并记录
仍存在的条件性跳过。此处不将环境跳过说成已证实的源码缺陷。

本次独立验证还包括：正常/变异报告对照、缺失工具链的真实 compare
分支、RSS/retained 受控失败、搬迁真实二进制的 CXX 入参拦截、Clang
符号链接驱动计划与最小实际链接，以及有关 tracked 文件的 diff 格式检查。
这些证据与性能测量明确分开，不把替身或最小 C++ 链接计入真实 A/A、A/B。

## 下一次 M1 交付要求

1. 完成 R1–R4，保留已修好的 F4 和正常/失败报告对照。改动仍限制在
   Analyzer、必要共享核心/适配器、测试和文档，Styio 源码保持只读。
2. 先完成定向行为验证和源码自审，再补齐上述最小真实证据。不能通过
   删除样本、降低阈值或把不可比事实改成 confirmed 获得通过。
3. 所有修改和定向验证结束后，只做一次最终完整回归。若完整回归失败，
   先诊断并提交建议，后续修复和是否重跑由开发者决定。
4. 在交接中逐项链接本次 R1–R4 的改动、反例验证和新证据，继续停在
   “待 Codex 独立验收”，不进入 M2。保留两次验收记录。
