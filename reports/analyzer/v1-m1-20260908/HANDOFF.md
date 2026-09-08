# Styio Analyzer v1 M1 交接

日期：2026-09-08。delivery-id：`v1-m1-20260908`。

状态：Codex 已完成 R1–R4 修复；定向验证、新真实 A/A、A/B 和一次最终完整回归均通过。
本轮由 Codex 直接修复，以下验证属于实现者自验，不是另一位审查者作出的独立验收。
保持 M1 范围，未进入 M2，未提交或推送。

两次历史独立验收结论保留：[ACCEPTANCE.md](ACCEPTANCE.md)、
[ACCEPTANCE-RECHECK.md](ACCEPTANCE-RECHECK.md)。原 `aa/`、`ab/` 的测量
没有改写；本轮真实证据另存 `codex-aa/` 和 `codex-ab/`。

## 修复内容

| 阻断 | 当前实现 | 反例验证 |
|---|---|---|
| R1 目录回溯误认运行时 | 计时前观察一次实际 Styio 原生构建的运行时输入；只保存是否匹配和公开构建配置；拦截器在链接前退出。已删除目录推断实现 | 搬迁替身覆盖邻近 cache 和回退分支；另以搬迁的真实 Styio 复现原反例，原树仍存在时正确判 mismatch，见下方证据 |
| R2 丢失 clang++ 调用名 | 固定绝对调用路径时保留符号链接名称；文件内容身份与驱动模式分开记录，并比较目标和构建配置 | 显式环境、CMake、工具链目录和 PATH 四种解析后，均经实际 CXX 子进程链接含标准库的 C++ 程序成功 |
| R3 部分证据绕过复算 | 分开检查样本有效性和数量是否足够；校验已有配对、未配对 raw/归一化值与完成次序；不足时不输出整组统计，并拒绝矛盾残留 | 实际 compare 的 retained/RSS 失败路径保留已有证据；修改部分值、次序、派生统计及 RSS 均被拒绝 |
| R4 身份与覆盖声明矛盾 | 用同一 catalog 和 selection 核对 source/input digest、完整 ID 集、所选族、能力排除和全量声明 | 修改源/输入身份，或把单族伪装成全 catalog，verify 均拒绝 |

保留已修好的 F4：RSS 后期失败不会丢掉有效时间样本，也不会把预检正确性
改成失败。已删除剩余的常驻源码字符串迁移断言；一次性迁移检查不加入门禁。

R1 的[真实二进制搬迁复现](codex-relocated-runtime.json)：临时树有运行时
marker、没有 CMakeCache，原内嵌运行时仍存在；实际 probe 得到
`mismatch`，不再误认 `confirmed`。临时副本已清理，未链接、未计入性能样本。

采样和统计仍在 `tools/measurement_core.py`，Styio/C++ parity 继续调用
同一核心。修改清单见 [SOURCE-CHANGES.md](SOURCE-CHANGES.md)。
Styio 编译器、运行时和优化 pass 源码均未改动；其他任务的工作树保持原样。

## 工具链与测量条件

- 新 A/A 使用同一份干净 `ec6ba02` Release 构建。
- 新 A/B 使用干净 `4e423e9` 基线和 `ec6ba02` 候选，各自隔离构建。
- 公开 CMake 配置一致：Release、`-O3 -DNDEBUG`、coverage OFF；实际原生
  调用为 O3、LTO disabled、compiler-default target；两侧固定同一个
  Clang 21.0.0 C++ 驱动。完整产物身份保存在 JSON。
- 运行时绑定来自实际 native-build probe，而不是目录位置或存在 marker
  的推断。未知或自定义且不能安全公开的配置按不可比披露。
- 沿用 3 次预热、11 对保留样本、0.5 秒批次时长地板、独立 RSS 重放和
  计时外产物体积；未放宽阈值，未修改 oracle，未删除噪声样本。
- `smoke`、`development`，项目微内核子集；不是全 11 族 reference，
  也不是官方基准或受控性能排名。

## 新真实证据

| 运行 | 范围 | 采集与复算 |
|---|---|---|
| [codex-aa/results.json](codex-aa/results.json) | scalar-chain 三路线 | 3 cells 完整、comparable=true、verify=pass |
| [codex-ab/results.json](codex-ab/results.json) | scalar-chain / control-diamonds / dense-matmul 三族九路线 | 9 cells 完整、comparable=true、verify=pass |

A/A：[摘要](codex-aa/summary.md)、[verify](codex-aa/recompute.json)。
三个时间结论均为 `inconclusive`（noise_cv），三个 RSS 结论均为
`no_detected_change`，生成产物体积比均为 1。

A/B：[摘要](codex-ab/summary.md)、[verify](codex-ab/recompute.json)。
九个时间结论均为 `inconclusive`（noise_cv）；RSS 有 4 个 `regressed`、
5 个 `no_detected_change`。生成产物体积比为 1.51339–1.51348，约增大
51.34%。这是本次既有版本比较的观测，不是本轮修改带来的编译器性能变化。

两份报告另经一次 Python 标准库算术核对，不调用 Analyzer 或共享统计核心：
[A/A 算术记录](codex-aa/arithmetic-check.json)、
[A/B 算术记录](codex-ab/arithmetic-check.json)。12 个 cell 的归一化、
保留数量、批次时长地板、中位数、CV、配对比值和产物差值均一致。
bootstrap 区间及分类由 verify 和固定数值测试核验，没有重建第二套统计引擎。
新 JSON 通过公开报告隐私检查，摘要未检出实际本机路径。

历史测量和历史复算只作为历史记录；不能补写本次绑定事实来使旧报告通过。
`compare=0` 表示采集有效，`verify=0` 表示报告内部一致，均不表示候选
性能获准或已经优化了 Styio。

## 验证

- [定向验证](codex-targeted-verification.txt)：四个相关测试文件
  **60 passed**；最后增加配置隐私与选择形状检查后，相关定向子集
  **21 passed、13 deselected**。这些次数不相加充当最终回归数量。
- 已完成共享核心及适配器源码自审、编译检查和一次性迁移检查。
- 真实性能采样及以上证据检查结束后执行了
  [一次最终完整回归](codex-final-regression.txt)：catalog-check pass、
  cpp-strength pass，`pytest -q -rs` **72 passed、1 skipped**，用时 189.78 秒。
  五项 async-runtime 测试使用隔离 Styio checkout 实际执行并通过。
- 唯一跳过为 parser scaling 缺少独立的 `perf-parity-baseline` 编译器构建；
  跳过不算通过。本轮未改动它依赖的共享构建，也未扩大到该范围外工作。
  最终完整回归之后没有源码/测试修改或重复回归。

## 验收范围映射

| AC | 本轮证据 |
|---|---|
| AC-01 | 实际运行时输入观察、CXX 驱动保持、缺失工具链不回退；对应 R1/R2 |
| AC-02 | 独立 oracle；失败采集保留预检正确性 |
| AC-03 | 三条路线复用核心；timing/RSS 分离及计时外产物 |
| AC-04 | 固定数值方向、噪声和时长不足夹具；部分证据也校验 |
| AC-05 | 展示统计复算、矛盾篡改拒绝、隐私检查；对应 R3 |
| AC-06 | catalog 同源身份/覆盖、如实缺失报告；对应 R4 |
| AC-07 | parity 共用核心，catalog 与 C++ strength 定向验证 |
| AC-08 | 新真实 A/A 3 cells、A/B 9 cells 均完整且可比；verify 与独立算术核对通过 |
| AC-09 | 本交接、源码清单、分离的测试与真实测量证据；一次最终回归 72 passed、1 skipped |

## 使用

本轮隔离 checkout 保留在仓库的 `build/analyzer-m1-codex-20260908/` 下，
分别为 `baseline` 和 `candidate`，各自的构建目录为 `build`。
复跑 A/A 时两侧指定同一 candidate；复跑 A/B 时再增加
`--family llvm-control-diamonds --family llvm-dense-matmul`。

```bash
python3 tools/styio_analyzer.py compare \
  --baseline-root /path/to/baseline \
  --baseline-build-dir /path/to/baseline/build \
  --candidate-root /path/to/candidate \
  --candidate-build-dir /path/to/candidate/build \
  --family llvm-scalar-chain --scale smoke --run-class development \
  --out-dir reports/analyzer/example

python3 tools/styio_analyzer.py verify \
  --contract workloads/parity-v2/contract.json \
  --report reports/analyzer/example/results.json
```

上面为占位路径。实际本机路径、原始子进程输出和构建日志不写入公开报告。
