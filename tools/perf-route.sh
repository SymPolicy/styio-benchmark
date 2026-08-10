#!/usr/bin/env bash
set -euo pipefail

# One canonical benchmark entrypoint.  All measurements and decisions live in
# parity_gate.py; this wrapper intentionally has no second timing model.

BENCHMARK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARITY_GATE="${BENCHMARK_ROOT}/tools/parity_gate.py"
CONTRACT="${BENCHMARK_ROOT}/workloads/parity-v1/contract.json"
STYIO_ROOT="${STYIO_ROOT:-${BENCHMARK_ROOT}/../styio}"
BUILD_DIR="build/default"
OUT_DIR=""
SIZES="small,medium,large"
ROUTES="all"
FOCUS=""
WARMUPS=3
REPETITIONS=11
VERIFY=0
MODE="final"
PRIVACY="strict"
REQUIRE_ALL=0
MAX_CV=""
MAX_GEOMEAN=""
MAX_CASE=""
TIMEOUT_S=300

usage() {
  cat <<'USAGE'
Usage: tools/perf-route.sh [options]

Delegates all measurements to tools/parity_gate.py.

Options:
  --contract <path>       Frozen parity catalog
  --styio-root <dir>      Styio source checkout
  --build-dir <dir>       Release CMake build directory
  --out-dir <dir>         Evidence report directory (required)
  --sizes <tiers>         Comma-separated small,medium,large
  --routes <routes>       all or comma-separated supported routes
  --focus <owner>         Focus owner or compiler-pipeline
  --warmups <n>           Warmup executions (default: 3)
  --repetitions <n>       Balanced measured pairs (default: 11)
  --verify                Verify the resulting report
  --mode <mode>           smoke, baseline, focused-budget, or final
  --privacy <mode>        strict (default) or off
  --require-all           Require every catalog cell when verifying
  --max-cv-pct <n>        CV threshold
  --max-geomean-ratio <n> Equal-weight geometric-mean threshold
  --max-case-ratio <n>    Per-cell ratio threshold
  --timeout-s <n>         Per-child wall-clock timeout (default: 300)
  -h, --help              Show help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --contract) CONTRACT="$2"; shift 2 ;;
    --styio-root) STYIO_ROOT="$2"; shift 2 ;;
    --build-dir) BUILD_DIR="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --sizes) SIZES="$2"; shift 2 ;;
    --routes) ROUTES="$2"; shift 2 ;;
    --focus) FOCUS="$2"; shift 2 ;;
    --warmups) WARMUPS="$2"; shift 2 ;;
    --repetitions) REPETITIONS="$2"; shift 2 ;;
    --verify) VERIFY=1; shift ;;
    --mode) MODE="$2"; shift 2 ;;
    --privacy) PRIVACY="$2"; shift 2 ;;
    --require-all) REQUIRE_ALL=1; shift ;;
    --max-cv-pct) MAX_CV="$2"; shift 2 ;;
    --max-geomean-ratio) MAX_GEOMEAN="$2"; shift 2 ;;
    --max-case-ratio) MAX_CASE="$2"; shift 2 ;;
    --timeout-s) TIMEOUT_S="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$OUT_DIR" ]]; then
  echo "--out-dir is required" >&2
  exit 2
fi

run_args=(
  run
  --contract "$CONTRACT"
  --styio-root "$STYIO_ROOT"
  --build-dir "$BUILD_DIR"
  --out-dir "$OUT_DIR"
  --sizes "$SIZES"
  --routes "$ROUTES"
  --warmups "$WARMUPS"
  --repetitions "$REPETITIONS"
  --timeout-s "$TIMEOUT_S"
)
if [[ -n "$FOCUS" ]]; then run_args+=(--focus "$FOCUS"); fi
python3 "$PARITY_GATE" "${run_args[@]}"

if [[ "$VERIFY" -eq 1 ]]; then
  verify_args=(
    verify
    --contract "$CONTRACT"
    --report "${OUT_DIR%/}/results.json"
    --mode "$MODE"
    --privacy "$PRIVACY"
  )
  if [[ "$REQUIRE_ALL" -eq 1 ]]; then verify_args+=(--require-all); fi
  if [[ -n "$FOCUS" ]]; then verify_args+=(--focus "$FOCUS"); fi
  if [[ -n "$MAX_CV" ]]; then verify_args+=(--max-cv-pct "$MAX_CV"); fi
  if [[ -n "$MAX_GEOMEAN" ]]; then verify_args+=(--max-geomean-ratio "$MAX_GEOMEAN"); fi
  if [[ -n "$MAX_CASE" ]]; then verify_args+=(--max-case-ratio "$MAX_CASE"); fi
  python3 "$PARITY_GATE" "${verify_args[@]}"
fi
