#!/usr/bin/env bash
# Binary-search a monotonic soak failure threshold and emit a regression artifact pack.
# Example:
#   ./tools/soak-minimize.sh \
#     --test StyioSoakSingleThread.FileHandleMemoryGrowthBound \
#     --var STYIO_SOAK_MEM_ITERS \
#     --low 100 \
#     --high 8000 \
#     --extra-env "STYIO_SOAK_MEM_FILE_LINES=128;STYIO_SOAK_RSS_GROWTH_LIMIT_KIB=98304"
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: tools/soak-minimize.sh --test <gtest-filter> --var <env-var> --high <n> [options]

Required:
  --test <name>       GTest filter value (single soak test name)
  --var <env-var>     Iteration environment variable to bisect
  --high <n>          Known failing upper bound

Optional:
  --low <n>           Lower bound (default: 1)
  --styio-root <dir>  Styio source checkout (default: STYIO_ROOT or ../styio)
  --build-dir <path>  Build directory under styio-root unless absolute (default: build)
  --bin <path>        Soak binary path (default: <build-dir>/bin/styio_soak_test)
  --extra-env <kv>    Extra env pairs, ';' separated (e.g. A=1;B=2)
  --out-dir <path>    Artifact output dir (default: regressions)
  -h, --help          Show help

Notes:
  - This assumes monotonic behavior for the chosen variable.
  - The script writes repro artifacts under regressions/<timestamp>-<test-slug>/.
USAGE
}

BENCHMARK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STYIO_ROOT="${STYIO_ROOT:-${BENCHMARK_ROOT}/../styio}"
cd "$BENCHMARK_ROOT"

TEST_NAME=""
VAR_NAME=""
LOW=1
HIGH=""
BUILD_DIR="build"
BIN_PATH=""
EXTRA_ENV_RAW=""
OUT_DIR="$BENCHMARK_ROOT/regressions"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --styio-root)
      STYIO_ROOT="$2"
      shift 2
      ;;
    --test)
      TEST_NAME="$2"
      shift 2
      ;;
    --var)
      VAR_NAME="$2"
      shift 2
      ;;
    --low)
      LOW="$2"
      shift 2
      ;;
    --high)
      HIGH="$2"
      shift 2
      ;;
    --build-dir)
      BUILD_DIR="$2"
      shift 2
      ;;
    --bin)
      BIN_PATH="$2"
      shift 2
      ;;
    --extra-env)
      EXTRA_ENV_RAW="$2"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$TEST_NAME" || -z "$VAR_NAME" || -z "$HIGH" ]]; then
  echo "Missing required arguments." >&2
  usage >&2
  exit 1
fi

STYIO_ROOT="$(cd "$STYIO_ROOT" && pwd)"
if [[ ! -f "${STYIO_ROOT}/CMakeLists.txt" ]]; then
  echo "--styio-root does not look like a Styio source checkout: ${STYIO_ROOT}" >&2
  exit 2
fi

if [[ "$BUILD_DIR" != /* ]]; then
  BUILD_DIR="${STYIO_ROOT}/${BUILD_DIR}"
fi

if [[ -z "$BIN_PATH" ]]; then
  BIN_PATH="$BUILD_DIR/bin/styio_soak_test"
fi
if [[ "$BIN_PATH" != /* ]]; then
  BIN_PATH="${STYIO_ROOT}/${BIN_PATH}"
fi
if [[ "$OUT_DIR" != /* ]]; then
  OUT_DIR="${BENCHMARK_ROOT}/${OUT_DIR}"
fi

if ! [[ "$LOW" =~ ^[0-9]+$ && "$HIGH" =~ ^[0-9]+$ ]]; then
  echo "--low and --high must be integers" >&2
  exit 1
fi
if (( LOW < 1 || HIGH < 1 || LOW > HIGH )); then
  echo "Invalid bounds: low=$LOW high=$HIGH" >&2
  exit 1
fi
if [[ ! -x "$BIN_PATH" ]]; then
  echo "Binary not found or not executable: $BIN_PATH" >&2
  echo "Try: cmake --build ${BUILD_DIR} --target styio_soak_test" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

declare -a EXTRA_ENV=()
if [[ -n "$EXTRA_ENV_RAW" ]]; then
  IFS=';' read -r -a EXTRA_ENV <<< "$EXTRA_ENV_RAW"
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/styio-soak-min-XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

sanitize_slug() {
  local s
  s="$(echo "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
  s="${s#-}"
  s="${s%-}"
  if [[ -z "$s" ]]; then
    s="case"
  fi
  echo "$s"
}

run_once() {
  local value="$1"
  local logfile="$2"
  (
    export "${VAR_NAME}=${value}"
    for kv in "${EXTRA_ENV[@]}"; do
      if [[ -n "$kv" ]]; then
        export "$kv"
      fi
    done
    "$BIN_PATH" --gtest_filter="$TEST_NAME"
  ) >"$logfile" 2>&1
}

echo "[check] ensure upper bound fails: ${VAR_NAME}=${HIGH}" >&2
if run_once "$HIGH" "$TMP_DIR/high.log"; then
  echo "Upper bound passed; choose a larger --high or different test." >&2
  exit 2
fi

echo "[check] probe lower bound: ${VAR_NAME}=${LOW}" >&2
if ! run_once "$LOW" "$TMP_DIR/low.log"; then
  echo "Lower bound already fails; minimization starts from low=${LOW}." >&2
fi

left="$LOW"
right="$HIGH"
while (( left < right )); do
  mid=$(( (left + right) / 2 ))
  log="$TMP_DIR/run-${mid}.log"
  echo "[probe] ${VAR_NAME}=${mid}" >&2
  if run_once "$mid" "$log"; then
    left=$((mid + 1))
  else
    right="$mid"
    cp "$log" "$TMP_DIR/latest_fail.log"
  fi
done

min_fail="$left"
if run_once "$min_fail" "$TMP_DIR/min_fail.log"; then
  echo "Unexpected pass at computed threshold=${min_fail}; rerun with wider bounds." >&2
  exit 3
fi

ts="$(date +"%Y%m%d-%H%M%S")"
slug="$(sanitize_slug "$TEST_NAME")"
case_dir="$OUT_DIR/${ts}-${slug}"
mkdir -p "$case_dir"

# Preserve logs.
cp "$TMP_DIR/min_fail.log" "$case_dir/min_fail.log"
cp "$TMP_DIR/high.log" "$case_dir/high.log"
if [[ -f "$TMP_DIR/latest_fail.log" ]]; then
  cp "$TMP_DIR/latest_fail.log" "$case_dir/latest_fail.log"
fi

# Write env snapshot.
{
  echo "${VAR_NAME}=${min_fail}"
  for kv in "${EXTRA_ENV[@]}"; do
    [[ -n "$kv" ]] && echo "$kv"
  done
} > "$case_dir/env.txt"

# Repro script.
cat > "$case_dir/repro.sh" <<EOF_REPRO
#!/usr/bin/env bash
set -euo pipefail
STYIO_ROOT="${STYIO_ROOT}"
cd "\$STYIO_ROOT"
export ${VAR_NAME}=${min_fail}
EOF_REPRO
for kv in "${EXTRA_ENV[@]}"; do
  if [[ -n "$kv" ]]; then
    echo "export ${kv}" >> "$case_dir/repro.sh"
  fi
done
cat >> "$case_dir/repro.sh" <<EOF_REPRO2
"$BIN_PATH" --gtest_filter="$TEST_NAME"
EOF_REPRO2
chmod +x "$case_dir/repro.sh"

# Case note from template.
cat > "$case_dir/CASE.md" <<EOF_CASE
# Soak Regression Case

- Test: $TEST_NAME
- Variable: $VAR_NAME
- Min failing value: ${min_fail}
- Generated at: $(date +"%Y-%m-%d %H:%M:%S %z")

## Repro

./repro.sh

## Environment Snapshot

env.txt

## Logs

- min_fail.log
- high.log
- latest_fail.log (if available)

## Next Actions

1. Reduce additional variables one by one (if set in env.txt).
2. Convert the minimized case into a stable regression test.
3. Link the fix commit and CI run URL in this file.
EOF_CASE

echo "Minimized threshold: ${VAR_NAME}=${min_fail}" >&2
echo "Artifacts: ${case_dir}" >&2
