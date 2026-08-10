| Benchmark | Baseline | Current | Delta % | Status |
|-----------|----------|---------|---------|--------|
| diag/many_errors_100 | 561.30 us | 561.30 us | +0.0% | stable |
| ir_alloc/factory_smoke | 0 ns | 0 ns | +0.0% | stable |
| lex/large_file_10k | 15.21 ms | 15.21 ms | +0.0% | stable |
| parse/deep_expr_200 | 974.30 us | 974.30 us | +0.0% | stable |
| parse/many_stmts_1k | 44.65 ms | 44.65 ms | +0.0% | stable |
| route_cache/deep_expr_200 | 0 ns | 0 ns | +0.0% | stable |
| route_cache/many_stmts_1k | 0 ns | 0 ns | +0.0% | stable |
| route_cache/name_resolution_5k | 0 ns | 0 ns | +0.0% | stable |
| route_cache/resource_dag_200 | 0 ns | 0 ns | +0.0% | stable |
| route_cache/typed_bindings_1k | 0 ns | 0 ns | +0.0% | stable |
| runtime/task_spawn_1k_nop | 700 ns | 700 ns | +0.0% | stable |
| scheduler/task_queue_mode | 0 ns | 0 ns | +0.0% | stable |
| sema/name_resolution_5k | 6.38 s | 6.38 s | +0.0% | stable |
| topology/resource_dag_200 | 60.65 ms | 60.65 ms | +0.0% | stable |
| type/typed_bindings_1k | 133.66 ms | 133.66 ms | +0.0% | stable |

**Summary:** 0 regression(s), 0 improvement(s), 0 new, 0 missing
| Benchmark | Metric | Baseline | Current | Delta |
|----------|--------|----------|---------|-------|
| route_cache/deep_expr_200 | scan | 1 | 1 | +0 |
| route_cache/deep_expr_200 | miss | 1 | 1 | +0 |
| route_cache/deep_expr_200 | hit | 0 | 0 | +0 |
| route_cache/deep_expr_200 | disabled | 0 | 0 | +0 |
| route_cache/many_stmts_1k | scan | 1000 | 1000 | +0 |
| route_cache/many_stmts_1k | miss | 1000 | 1000 | +0 |
| route_cache/many_stmts_1k | hit | 0 | 0 | +0 |
| route_cache/many_stmts_1k | disabled | 0 | 0 | +0 |
| route_cache/name_resolution_5k | scan | 10001 | 10001 | +0 |
| route_cache/name_resolution_5k | miss | 10001 | 10001 | +0 |
| route_cache/name_resolution_5k | hit | 0 | 0 | +0 |
| route_cache/name_resolution_5k | disabled | 0 | 0 | +0 |
| route_cache/resource_dag_200 | scan | 600 | 600 | +0 |
| route_cache/resource_dag_200 | miss | 600 | 600 | +0 |
| route_cache/resource_dag_200 | hit | 0 | 0 | +0 |
| route_cache/resource_dag_200 | disabled | 0 | 0 | +0 |
| route_cache/typed_bindings_1k | scan | 1200 | 1200 | +0 |
| route_cache/typed_bindings_1k | miss | 1200 | 1200 | +0 |
| route_cache/typed_bindings_1k | hit | 0 | 0 | +0 |
| route_cache/typed_bindings_1k | disabled | 0 | 0 | +0 |
| Benchmark | Metric | Baseline | Current | Delta |
|----------|--------|----------|---------|-------|
| ir_alloc/factory_smoke | arena | 0 | 0 | +0 |
| ir_alloc/factory_smoke | raw | 1 | 1 | +0 |
| ir_alloc/factory_smoke | bytes | 8 | 8 | +0 |
| ir_alloc/factory_smoke | nodes | 1 | 1 | +0 |
| ir_alloc/factory_smoke | peak | 1 | 1 | +0 |
| ir_alloc/factory_smoke | dtors | 1 | 1 | +0 |
| Benchmark | Metric | Baseline | Current | Delta |
|----------|--------|----------|---------|-------|
| scheduler/task_queue_mode | queue_kind | 0 | 0 | +0 |
| scheduler/task_queue_mode | workers | 0 | 0 | +0 |
