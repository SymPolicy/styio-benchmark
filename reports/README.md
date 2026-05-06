# Benchmark Reports

`tools/perf-route.sh` 会把每次运行的本地产物写到这个目录下。

约定：

- 每次运行使用独立时间戳目录
- 目录内容默认不纳入版本库
- 关键产物包括：
  - `metadata.tsv`
  - `sections.tsv`
  - `logs/*.log`
  - `results.json`
  - `benchmarks.csv`
  - `summary.md`

如果需要把某次基准结论固化到仓库，请把摘要写入：

- `docs/history/<date>.md`
- 或其他正式文档，而不是直接提交整批原始日志
