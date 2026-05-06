# Styio Benchmark Summary

- Status: `success`
- Started (UTC): `2026-04-29T03:29:53Z`
- Finished (UTC): `2026-04-29T03:30:51Z`
- Command: `tools/perf-route.sh --styio-root <styio-root> --build-dir build/native-extern-baseline --label native-extern-raw --phase-iters 5000 --micro-iters 5000 --execute-iters 20 --error-iters 50`
- Git: `5f4bdfd` on `nightly` (dirty=`true`)
- Host: `styio-dev` / `unknown` / `Linux 6.19.13-orbstack-gbd1dc07b8cf4 aarch64`
- Build dir: `<styio-root>/build/native-extern-baseline`
- Iterations: phase=`5000`, micro=`5000`, execute=`20`, error=`50`

## Route

| Section | Status | Duration (s) | Note | Log |
| --- | --- | --- | --- | --- |
| configure (build/native-extern-baseline) | skip | 0 | existing_cmake_cache |  |
| build (build/native-extern-baseline) | pass | 21 |  | logs/build.log |
| compiler stage benchmark | pass | 9 |  | logs/compiler_stage_benchmark.log |
| compiler micro benchmark | pass | 23 |  | logs/compiler_micro_benchmark.log |
| full-stack workload matrix | pass | 1 |  | logs/full_stack_workload_matrix.log |
| compiler error-path benchmark | pass | 1 |  | logs/compiler_error_path_benchmark.log |
| parser engine suite | pass | 1 |  | logs/parser_engine_suite.log |
| pipeline guard rail | pass | 0 |  | logs/pipeline_guard_rail.log |
| parser/security guard rail | pass | 0 |  | logs/parser_security_guard_rail.log |
| parser shadow gates | pass | 1 |  | logs/parser_shadow_gates.log |
| soak smoke | pass | 1 |  | logs/soak_smoke.log |
| soak deep | skip | 0 | RUN_DEEP_SOAK=0;QUICK_MODE=0 |  |

## Stage Matrix

| Module | Label | Total Compile us | Parse us | Lower us | LLVM IR us | RSS Growth KiB |
| --- | --- | --- | --- | --- | --- | --- |
| Collections | dict_heavy | 255.863 | 16.536 | 12.459 | 206.442 | 16320 |
| ControlFlow | control_match | 213.613 | 35.990 | 10.261 | 121.981 | 19088 |
| StateAndSeries | series_window_avg | 166.510 | 9.682 | 4.398 | 140.098 | 8256 |
| StateAndSeries | state_pulse_inline | 136.250 | 13.702 | 8.125 | 99.471 | 12060 |
| StateAndSeries | snapshot_state | 130.803 | 12.551 | 5.705 | 94.686 | 10456 |
| Streams | stream_zip_files | 116.802 | 6.817 | 4.395 | 93.223 | 4264 |
| Streams | stdin_transform | 105.523 | 8.977 | 4.125 | 80.349 | 6848 |
| Mixed | mixed_full_pipeline | 102.779 | 6.228 | 3.460 | 80.255 | 5748 |
| Scalar | scalar_core | 91.593 | 8.286 | 4.379 | 68.677 | 12464 |
| Functions | function_block_body | 91.477 | 8.795 | 3.527 | 70.154 | 7760 |
| Bindings | bindings_chain | 89.613 | 7.072 | 3.463 | 68.297 | 8400 |
| Resources | resource_file_io | 84.633 | 6.463 | 3.143 | 65.531 | 2512 |
| Topology | topology_ring | 76.026 | 4.054 | 1.640 | 65.414 | 2304 |

## Full-Stack Matrix

| Module | Label | CLI Wall us |
| --- | --- | --- |
| StateAndSeries | series_window_avg | 6683.600 |
| Collections | dict_heavy | 6400.110 |
| StateAndSeries | snapshot_state | 6021.480 |
| ControlFlow | control_match | 5698.610 |
| StateAndSeries | state_pulse_inline | 5620.530 |
| Scalar | scalar_core | 5286.460 |
| Streams | stream_zip_files | 5244.540 |
| Streams | stdin_transform | 5024.710 |
| Functions | function_block_body | 4799.160 |
| Resources | resource_file_io | 4654.230 |
| Topology | topology_ring | 4421.110 |
| Bindings | bindings_chain | 4405.360 |

## Micro Benchmarks

| Focus | Name | Focus us | Avg Tokens | RSS Growth KiB |
| --- | --- | --- | --- | --- |
| lexer | lexer.mixed_trivia | 1611.610 | 11393 | 668 |
| lexer | lexer.long_identifiers | 1071.530 | 3073 | 1424 |
| parser | parser.expr_scalar_chain | 576.541 | 3852 | 320 |
| llvm | llvm.state_ir | 99.048 | 77 | 12128 |
| llvm | llvm.scalar_ir | 67.675 | 36 | 10832 |
| llvm | llvm.resource_ir | 65.121 | 35 | 2576 |
| parser | parser.match_cases | 9.082 | 60 | 0 |
| type | type.dict_heavy | 8.210 | 75 | 0 |
| lower | lower.state_pulse | 8.025 | 77 | 11952 |
| parser | parser.function_block_body | 8.008 | 50 | 0 |
| type | type.snapshot_state | 6.227 | 73 | 16 |
| parser | parser.iterator_resource_postfix | 5.854 | 35 | 0 |
| lower | lower.stream_zip | 4.031 | 48 | 3880 |
| lower | lower.resource_io | 2.969 | 35 | 2592 |

## Error-Path Benchmarks

| Category | Name | Error us | Exit Code | Diagnostic Code |
| --- | --- | --- | --- | --- |
| lex | lex.unterminated_block_comment | 2792.010 | 2 | STYIO_LEX |
| parse | parse.empty_match_cases | 2937.520 | 3 | STYIO_PARSE |
| type | type.final_then_flex_i64 | 3168.220 | 4 | STYIO_TYPE |
| runtime | runtime.read_missing_file | 4593.700 | 5 | STYIO_RUNTIME |
