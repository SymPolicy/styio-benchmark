#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: tools/perf-route.sh [options]

Options:
  --styio-root <dir>    Styio source checkout (default: STYIO_ROOT or ../styio)
  --build-dir <dir>     CMake build dir under styio-root unless absolute (default: build)
  --out-dir <dir>       Benchmark artifact dir (default: reports/<timestamp>)
  --label <name>        Optional run label appended to artifact dir
  --phase-iters <n>     Compiler stage benchmark iterations (default: 5000)
  --micro-iters <n>     Compiler micro benchmark iterations (default: phase-iters, 0 to skip)
  --execute-iters <n>   Full-stack benchmark iterations (default: derived from phase-iters, 0 to skip)
  --error-iters <n>     Error-path benchmark iterations (default: derived from phase-iters, 0 to skip)
  --native-cpp-lines <n>
                         Native C++ comparison input line count (default: 100000)
  --native-cpp-line-bytes <n>
                         Native C++ comparison generated line width (default: 48)
  --native-cpp-repeats <n>
                         Native C++ comparison measured repeats (default: 5)
  --native-cpp-warmups <n>
                         Native C++ comparison warmup runs (default: 1)
  --native-cpp-routes <routes>
                         Native C++ comparison routes (default: all)
  --skip-native-cpp     Skip Styio/native C++ comparison route
  --skip-build          Skip cmake configure/build
  --deep-soak           Run soak_deep in addition to soak_smoke
  --quick               Skip parser shadow gates and soak_deep
  -h, --help            Show help

This script runs the current parser/compiler performance route:
  1. Configure/build perf-related binaries when needed
  2. Compiler stage benchmark matrix
  3. Compiler micro benchmark matrix
  4. Full-stack workload matrix
  5. Styio/native C++ comparison routes (full-cli, cached-jit, runtime-only)
  6. Compiler error-path benchmark matrix
  7. Parser engine regression suite
  8. Pipeline guard rail
  9. Parser/security guard rail
  10. Parser shadow gates
  11. Soak smoke, and optionally soak_deep

Artifacts:
  - metadata.tsv        Run metadata and environment snapshot
  - sections.tsv        Section status / duration inventory
  - logs/*.log          Raw command logs per route section
  - results.json        Structured benchmark results
  - benchmarks.csv      Flattened benchmark metric table
  - summary.md          Markdown summary for the run
USAGE
}

BENCHMARK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STYIO_ROOT="${STYIO_ROOT:-${BENCHMARK_ROOT}/../styio}"
cd "$BENCHMARK_ROOT"

shell_join() {
  local out=""
  local arg
  for arg in "$@"; do
    printf -v out '%s%q ' "$out" "$arg"
  done
  printf '%s' "${out% }"
}

sanitize_slug() {
  local raw="$1"
  local slug
  slug="$(echo "$raw" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
  slug="${slug#-}"
  slug="${slug%-}"
  if [[ -z "$slug" ]]; then
    slug="run"
  fi
  printf '%s\n' "$slug"
}

resolve_path() {
  local raw="$1"
  if [[ "$raw" = /* ]]; then
    printf '%s\n' "$raw"
  else
    printf '%s/%s\n' "$BENCHMARK_ROOT" "$raw"
  fi
}

resolve_styio_path() {
  local raw="$1"
  if [[ "$raw" = /* ]]; then
    printf '%s\n' "$raw"
  else
    printf '%s/%s\n' "$STYIO_ROOT" "$raw"
  fi
}

detect_jobs() {
  local jobs
  jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
  if [[ -z "$jobs" ]]; then
    jobs="$(sysctl -n hw.ncpu 2>/dev/null || true)"
  fi
  if [[ -z "$jobs" ]]; then
    jobs="8"
  fi
  printf '%s\n' "$jobs"
}

detect_cpu_model() {
  local cpu_model
  cpu_model="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || true)"
  if [[ -z "$cpu_model" && -r /proc/cpuinfo ]]; then
    cpu_model="$(awk -F: '/model name/ {gsub(/^[ \t]+/, "", $2); print $2; exit}' /proc/cpuinfo)"
  fi
  printf '%s\n' "${cpu_model:-unknown}"
}

meta_row() {
  printf '%s\t%s\n' "$1" "$2" >> "$METADATA_FILE"
}

section_row() {
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6" >> "$SECTIONS_FILE"
}

section() {
  echo
  echo "[perf-route] $1"
}

FAIL_COUNT=0
FAILED_SECTION_IDS=()

run_logged_section() {
  local id="$1"
  local title="$2"
  shift 2

  local log_rel="logs/${id}.log"
  local log_file="${RUN_DIR}/${log_rel}"
  local started ended duration_s rc
  started="$(date +%s)"

  section "$title"
  echo "[perf-route] log=${log_file}"

  set +e
  {
    printf '[perf-route] cmd:'
    printf ' %q' "$@"
    printf '\n'
    "$@"
  } 2>&1 | tee "$log_file"
  rc=${PIPESTATUS[0]}
  set -e

  ended="$(date +%s)"
  duration_s=$((ended - started))

  if [[ "$rc" -eq 0 ]]; then
    section_row "$id" "$title" "pass" "$duration_s" "$log_rel" ""
    return 0
  fi

  section_row "$id" "$title" "fail" "$duration_s" "$log_rel" "exit_code=${rc}"
  FAIL_COUNT=$((FAIL_COUNT + 1))
  FAILED_SECTION_IDS+=("${id}:${rc}")
  return 0
}

record_skip_section() {
  local id="$1"
  local title="$2"
  local note="$3"
  section_row "$id" "$title" "skip" "0" "" "$note"
}

finalize() {
  local exit_code=$?
  trap - EXIT

  meta_row "finished_at_utc" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  meta_row "overall_status" "$([[ "$exit_code" -eq 0 ]] && echo success || echo failed)"
  meta_row "script_exit_code" "$exit_code"
  meta_row "failed_sections" "${FAILED_SECTION_IDS[*]:-}"

  if command -v python3 >/dev/null 2>&1 && [[ -f "${BENCHMARK_ROOT}/tools/perf-report.py" ]]; then
    python3 "${BENCHMARK_ROOT}/tools/perf-report.py" --run-dir "$RUN_DIR" >/dev/null
  fi

  echo
  echo "[perf-route] artifacts=${RUN_DIR}"
  if [[ -f "${RUN_DIR}/summary.md" ]]; then
    echo "[perf-route] summary=${RUN_DIR}/summary.md"
  fi

  exit "$exit_code"
}

ORIGINAL_ARGS=("$@")
ORIGINAL_ARGV="$(shell_join "${ORIGINAL_ARGS[@]}")"

BUILD_DIR="build"
OUT_DIR=""
LABEL=""
PHASE_ITERS="5000"
MICRO_ITERS=""
EXECUTE_ITERS=""
ERROR_ITERS=""
NATIVE_CPP_LINES="100000"
NATIVE_CPP_LINE_BYTES="48"
NATIVE_CPP_REPEATS="5"
NATIVE_CPP_WARMUPS="1"
NATIVE_CPP_ROUTES="all"
SKIP_NATIVE_CPP=0
SKIP_BUILD=0
RUN_DEEP_SOAK=0
QUICK_MODE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --styio-root)
      STYIO_ROOT="$2"
      shift 2
      ;;
    --build-dir)
      BUILD_DIR="$2"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --label)
      LABEL="$2"
      shift 2
      ;;
    --phase-iters)
      PHASE_ITERS="$2"
      shift 2
      ;;
    --micro-iters)
      MICRO_ITERS="$2"
      shift 2
      ;;
    --execute-iters)
      EXECUTE_ITERS="$2"
      shift 2
      ;;
    --error-iters)
      ERROR_ITERS="$2"
      shift 2
      ;;
    --native-cpp-lines)
      NATIVE_CPP_LINES="$2"
      shift 2
      ;;
    --native-cpp-line-bytes)
      NATIVE_CPP_LINE_BYTES="$2"
      shift 2
      ;;
    --native-cpp-repeats)
      NATIVE_CPP_REPEATS="$2"
      shift 2
      ;;
    --native-cpp-warmups)
      NATIVE_CPP_WARMUPS="$2"
      shift 2
      ;;
    --native-cpp-routes)
      NATIVE_CPP_ROUTES="$2"
      shift 2
      ;;
    --skip-native-cpp)
      SKIP_NATIVE_CPP=1
      shift
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --deep-soak)
      RUN_DEEP_SOAK=1
      shift
      ;;
    --quick)
      QUICK_MODE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "$PHASE_ITERS" =~ ^[0-9]+$ ]]; then
  echo "--phase-iters must be an integer" >&2
  exit 2
fi

if [[ -z "$MICRO_ITERS" ]]; then
  MICRO_ITERS="$PHASE_ITERS"
fi
if ! [[ "$MICRO_ITERS" =~ ^[0-9]+$ ]]; then
  echo "--micro-iters must be an integer" >&2
  exit 2
fi

if [[ -z "$EXECUTE_ITERS" ]]; then
  EXECUTE_ITERS=$(( PHASE_ITERS / 250 ))
  if (( EXECUTE_ITERS < 5 )); then
    EXECUTE_ITERS=5
  fi
  if (( EXECUTE_ITERS > 50 )); then
    EXECUTE_ITERS=50
  fi
fi
if ! [[ "$EXECUTE_ITERS" =~ ^[0-9]+$ ]]; then
  echo "--execute-iters must be an integer" >&2
  exit 2
fi

if [[ -z "$ERROR_ITERS" ]]; then
  ERROR_ITERS=$(( PHASE_ITERS / 100 ))
  if (( ERROR_ITERS < 20 )); then
    ERROR_ITERS=20
  fi
  if (( ERROR_ITERS > 200 )); then
    ERROR_ITERS=200
  fi
fi
if ! [[ "$ERROR_ITERS" =~ ^[0-9]+$ ]]; then
  echo "--error-iters must be an integer" >&2
  exit 2
fi
for native_numeric in NATIVE_CPP_LINES NATIVE_CPP_LINE_BYTES NATIVE_CPP_REPEATS NATIVE_CPP_WARMUPS; do
  if ! [[ "${!native_numeric}" =~ ^[0-9]+$ ]]; then
    echo "--$(echo "$native_numeric" | tr '[:upper:]_' '[:lower:]-') must be an integer" >&2
    exit 2
  fi
done

STYIO_ROOT="$(cd "$STYIO_ROOT" && pwd)"
if [[ ! -f "${STYIO_ROOT}/CMakeLists.txt" ]]; then
  echo "--styio-root does not look like a Styio source checkout: ${STYIO_ROOT}" >&2
  exit 2
fi

RUN_STAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
if [[ -n "$LABEL" ]]; then
  RUN_STAMP="${RUN_STAMP}-$(sanitize_slug "$LABEL")"
fi

if [[ -z "$OUT_DIR" ]]; then
  RUN_DIR="${BENCHMARK_ROOT}/reports/${RUN_STAMP}"
else
  RUN_DIR="$(resolve_path "$OUT_DIR")"
fi

mkdir -p "${RUN_DIR}/logs"

METADATA_FILE="${RUN_DIR}/metadata.tsv"
SECTIONS_FILE="${RUN_DIR}/sections.tsv"
JOBS="$(detect_jobs)"
CPU_MODEL="$(detect_cpu_model)"
BUILD_DIR_ABS="$(resolve_styio_path "$BUILD_DIR")"

printf 'key\tvalue\n' > "$METADATA_FILE"
printf 'id\ttitle\tstatus\tduration_s\tlog_path\tnote\n' > "$SECTIONS_FILE"

meta_row "run_dir" "$RUN_DIR"
meta_row "benchmark_root" "$BENCHMARK_ROOT"
meta_row "styio_root" "$STYIO_ROOT"
meta_row "build_dir" "$BUILD_DIR"
meta_row "build_dir_abs" "$BUILD_DIR_ABS"
meta_row "phase_iters" "$PHASE_ITERS"
meta_row "micro_iters" "$MICRO_ITERS"
meta_row "execute_iters" "$EXECUTE_ITERS"
meta_row "error_iters" "$ERROR_ITERS"
meta_row "native_cpp_lines" "$NATIVE_CPP_LINES"
meta_row "native_cpp_line_bytes" "$NATIVE_CPP_LINE_BYTES"
meta_row "native_cpp_repeats" "$NATIVE_CPP_REPEATS"
meta_row "native_cpp_warmups" "$NATIVE_CPP_WARMUPS"
meta_row "native_cpp_routes" "$NATIVE_CPP_ROUTES"
meta_row "skip_native_cpp" "$SKIP_NATIVE_CPP"
meta_row "skip_build" "$SKIP_BUILD"
meta_row "quick_mode" "$QUICK_MODE"
meta_row "deep_soak" "$RUN_DEEP_SOAK"
meta_row "label" "$LABEL"
meta_row "command" "./tools/perf-route.sh ${ORIGINAL_ARGV}"
meta_row "started_at_utc" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
meta_row "hostname" "$(hostname)"
meta_row "uname" "$(uname -srm)"
meta_row "cpu_model" "$CPU_MODEL"
meta_row "jobs" "$JOBS"
meta_row "benchmark_git_head" "$(git -C "$BENCHMARK_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
meta_row "benchmark_git_branch" "$(git -C "$BENCHMARK_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
meta_row "benchmark_git_dirty" "$([[ -n "$(git -C "$BENCHMARK_ROOT" status --short 2>/dev/null || true)" ]] && echo true || echo false)"
meta_row "styio_git_head" "$(git -C "$STYIO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
meta_row "styio_git_branch" "$(git -C "$STYIO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
meta_row "styio_git_dirty" "$([[ -n "$(git -C "$STYIO_ROOT" status --short 2>/dev/null || true)" ]] && echo true || echo false)"

trap finalize EXIT
cd "$STYIO_ROOT"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  if [[ ! -f "${BUILD_DIR_ABS}/CMakeCache.txt" ]]; then
    run_logged_section \
      "configure" \
      "configure (${BUILD_DIR})" \
      cmake -S "$STYIO_ROOT" -B "$BUILD_DIR_ABS"
  else
    record_skip_section "configure" "configure (${BUILD_DIR})" "existing_cmake_cache"
  fi

  run_logged_section \
    "build" \
    "build (${BUILD_DIR})" \
    cmake --build "$BUILD_DIR_ABS" --target styio styio_test styio_security_test styio_soak_test -j"$JOBS"
else
  record_skip_section "configure" "configure (${BUILD_DIR})" "skip_build=1"
  record_skip_section "build" "build (${BUILD_DIR})" "skip_build=1"
fi

  run_logged_section \
    "compiler_stage_benchmark" \
    "compiler stage benchmark" \
    env STYIO_SOAK_PHASE_BENCH_ITERS="$PHASE_ITERS" \
    "${BUILD_DIR_ABS}/bin/styio_soak_test" \
    --gtest_filter=StyioSoakSingleThread.FrontendPhaseBreakdownReport

if [[ "$MICRO_ITERS" -gt 0 ]]; then
  run_logged_section \
    "compiler_micro_benchmark" \
    "compiler micro benchmark" \
    env STYIO_SOAK_MICRO_BENCH_ITERS="$MICRO_ITERS" \
      "${BUILD_DIR_ABS}/bin/styio_soak_test" \
      --gtest_filter=StyioSoakSingleThread.CompilerMicroBenchmarksReport
else
  record_skip_section "compiler_micro_benchmark" "compiler micro benchmark" "MICRO_ITERS=0"
fi

if [[ "$EXECUTE_ITERS" -gt 0 ]]; then
  run_logged_section \
    "full_stack_workload_matrix" \
    "full-stack workload matrix" \
    env STYIO_SOAK_EXECUTE_BENCH_ITERS="$EXECUTE_ITERS" \
      "${BUILD_DIR_ABS}/bin/styio_soak_test" \
      --gtest_filter=StyioSoakSingleThread.FullStackWorkloadMatrixReport
else
  record_skip_section "full_stack_workload_matrix" "full-stack workload matrix" "EXECUTE_ITERS=0"
fi

if [[ "$SKIP_NATIVE_CPP" -eq 0 ]]; then
  run_logged_section \
    "native_cpp_comparison" \
    "Styio/native C++ comparison routes" \
    "${BENCHMARK_ROOT}/native-cpp/run-native-cpp-bench.py" \
      --styio-root "$STYIO_ROOT" \
      --build-dir "$BUILD_DIR_ABS" \
      --routes "$NATIVE_CPP_ROUTES" \
      --line-count "$NATIVE_CPP_LINES" \
      --line-bytes "$NATIVE_CPP_LINE_BYTES" \
      --repeats "$NATIVE_CPP_REPEATS" \
      --warmups "$NATIVE_CPP_WARMUPS" \
      --out-dir "$RUN_DIR/native-cpp"
else
  record_skip_section "native_cpp_comparison" "Styio/native C++ comparison routes" "SKIP_NATIVE_CPP=1"
fi

if [[ "$ERROR_ITERS" -gt 0 ]]; then
  run_logged_section \
    "compiler_error_path_benchmark" \
    "compiler error-path benchmark" \
    env STYIO_SOAK_ERROR_BENCH_ITERS="$ERROR_ITERS" \
      "${BUILD_DIR_ABS}/bin/styio_soak_test" \
      --gtest_filter=StyioSoakSingleThread.CompilerErrorPathBenchmarksReport
else
  record_skip_section "compiler_error_path_benchmark" "compiler error-path benchmark" "ERROR_ITERS=0"
fi

run_logged_section \
  "parser_engine_suite" \
  "parser engine suite" \
  ctest --test-dir "$BUILD_DIR_ABS" --output-on-failure -R '^StyioParserEngine\.'

run_logged_section \
  "pipeline_guard_rail" \
  "pipeline guard rail" \
  ctest --test-dir "$BUILD_DIR_ABS" --output-on-failure \
    -R '^StyioFiveLayerPipeline\.(P05_snapshot_accum|P09_full_pipeline|P13_stdin_transform|P14_stdin_pull|P15_stdin_mixed_output)$'

run_logged_section \
  "parser_security_guard_rail" \
  "parser/security guard rail" \
  ctest --test-dir "$BUILD_DIR_ABS" --output-on-failure \
    -R '^StyioSecurity(NightlyParserStmt|NightlyParserExpr|ParserContext)\.'

if [[ "$QUICK_MODE" -eq 0 ]]; then
  run_logged_section \
    "parser_shadow_gates" \
    "parser shadow gates" \
    ctest --test-dir "$BUILD_DIR_ABS" --output-on-failure \
      -R '^parser_shadow_gate_m(1|2)_zero_fallback_and_internal_bridges$|^parser_shadow_gate_m5_dual_zero_expected_nonzero$|^parser_shadow_gate_m7_zero_fallback$|^parser_shadow_gate_m7_zero_internal_bridges$'
else
  record_skip_section "parser_shadow_gates" "parser shadow gates" "QUICK_MODE=1"
fi

run_logged_section \
  "soak_smoke" \
  "soak smoke" \
  ctest --test-dir "$BUILD_DIR_ABS" --output-on-failure -L soak_smoke

if [[ "$RUN_DEEP_SOAK" -eq 1 && "$QUICK_MODE" -eq 0 ]]; then
  run_logged_section \
    "soak_deep" \
    "soak deep" \
    ctest --test-dir "$BUILD_DIR_ABS" --output-on-failure -L soak_deep
else
  local_note="RUN_DEEP_SOAK=${RUN_DEEP_SOAK};QUICK_MODE=${QUICK_MODE}"
  record_skip_section "soak_deep" "soak deep" "$local_note"
fi

section "done"

if [[ "$FAIL_COUNT" -ne 0 ]]; then
  exit 1
fi
