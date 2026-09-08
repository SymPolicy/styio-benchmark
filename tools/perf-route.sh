#!/usr/bin/env bash
set -euo pipefail

# Thin canonical entrypoint. All timing, statistics, and decisions live in the
# standard parity gate; this wrapper intentionally defines no second model.

BENCHMARK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARITY_GATE="${BENCHMARK_ROOT}/tools/standard_parity_gate.py"
CONTRACT="${BENCHMARK_ROOT}/workloads/parity-v2/contract.json"
STYIO_ROOT="${STYIO_ROOT:-${BENCHMARK_ROOT}/../styio-nightly}"
BUILD_DIR=""
OUT_DIR=""
FAMILY=""
SCALE="smoke"
RUN_CLASS="development"
WARMUPS=3
REPETITIONS=11
VERIFY=0
PRIVACY="strict"
REQUIRE_ALL=0
MAX_CV=""
MAX_GEOMEAN=""
MAX_CASE=""
MAX_MEMORY_GEOMEAN=""
MAX_MEMORY_CASE=""
TIMEOUT_S=300

usage() {
  cat <<'USAGE'
Usage: tools/perf-route.sh --family <id> --build-dir <dir> --out-dir <dir> [options]

Delegates one deterministic shard to tools/standard_parity_gate.py.

Options:
  --contract <path>                   Frozen parity-v2 catalog
  --family <id>                       Workload family or compiler-phase
  --scale <label>                     smoke, development, reference, or all
  --run-class <class>                 development or controlled
  --styio-root <dir>                  Styio source checkout
  --build-dir <dir>                   Release build directory
  --out-dir <dir>                     Evidence report directory
  --warmups <n>                       Warm-up pairs (default: 3)
  --repetitions <n>                   Retained pairs (default: 11)
  --verify                            Verify the resulting shard report
  --privacy <mode>                    strict (default) or off
  --require-all                       Require the complete reference matrix
  --max-cv-pct <n>                    Per-implementation CV threshold
  --max-geomean-ratio <n>             Route time threshold
  --max-case-ratio <n>                Per-cell time threshold
  --max-memory-geomean-ratio <n>      Route peak-RSS threshold
  --max-memory-case-ratio <n>         Per-cell peak-RSS threshold
  --timeout-s <n>                     Per-batch wall-clock budget
  -h, --help                          Show help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --contract) CONTRACT="$2"; shift 2 ;;
    --family) FAMILY="$2"; shift 2 ;;
    --scale) SCALE="$2"; shift 2 ;;
    --run-class) RUN_CLASS="$2"; shift 2 ;;
    --styio-root) STYIO_ROOT="$2"; shift 2 ;;
    --build-dir) BUILD_DIR="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --warmups) WARMUPS="$2"; shift 2 ;;
    --repetitions) REPETITIONS="$2"; shift 2 ;;
    --verify) VERIFY=1; shift ;;
    --privacy) PRIVACY="$2"; shift 2 ;;
    --require-all) REQUIRE_ALL=1; shift ;;
    --max-cv-pct) MAX_CV="$2"; shift 2 ;;
    --max-geomean-ratio) MAX_GEOMEAN="$2"; shift 2 ;;
    --max-case-ratio) MAX_CASE="$2"; shift 2 ;;
    --max-memory-geomean-ratio) MAX_MEMORY_GEOMEAN="$2"; shift 2 ;;
    --max-memory-case-ratio) MAX_MEMORY_CASE="$2"; shift 2 ;;
    --timeout-s) TIMEOUT_S="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$FAMILY" || -z "$BUILD_DIR" || -z "$OUT_DIR" ]]; then
  echo "--family, --build-dir, and --out-dir are required" >&2
  exit 2
fi

python3 "$PARITY_GATE" run \
  --contract "$CONTRACT" \
  --family "$FAMILY" \
  --scale "$SCALE" \
  --run-class "$RUN_CLASS" \
  --styio-root "$STYIO_ROOT" \
  --build-dir "$BUILD_DIR" \
  --out-dir "$OUT_DIR" \
  --warmups "$WARMUPS" \
  --repetitions "$REPETITIONS" \
  --timeout-s "$TIMEOUT_S"

if [[ "$VERIFY" -eq 1 ]]; then
  verify_args=(
    verify
    --contract "$CONTRACT"
    --report "${OUT_DIR%/}/results.json"
    --privacy "$PRIVACY"
  )
  if [[ "$REQUIRE_ALL" -eq 1 ]]; then verify_args+=(--require-all); fi
  if [[ -n "$MAX_CV" ]]; then verify_args+=(--max-cv-pct "$MAX_CV"); fi
  if [[ -n "$MAX_GEOMEAN" ]]; then verify_args+=(--max-geomean-ratio "$MAX_GEOMEAN"); fi
  if [[ -n "$MAX_CASE" ]]; then verify_args+=(--max-case-ratio "$MAX_CASE"); fi
  if [[ -n "$MAX_MEMORY_GEOMEAN" ]]; then verify_args+=(--max-memory-geomean-ratio "$MAX_MEMORY_GEOMEAN"); fi
  if [[ -n "$MAX_MEMORY_CASE" ]]; then verify_args+=(--max-memory-case-ratio "$MAX_MEMORY_CASE"); fi
  python3 "$PARITY_GATE" "${verify_args[@]}"
fi
