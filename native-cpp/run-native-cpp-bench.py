#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import shlex
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STYIO_ROOT = (BENCHMARK_ROOT / "../styio").resolve()
BOOTSTRAP_SEED = 0x5EED
BOOTSTRAP_RESAMPLES = 10000
ALPHA = 0.05

SCALE_LINE_COUNTS = {
    "small": 10_000,
    "medium": 100_000,
    "large": 1_000_000,
}


STYIO_STDIN_ECHO = """@stdin >> #(line) => {
  line -> @stdout
}
"""


CPP_STDIN_ECHO = r"""#include <iostream>
#include <string>

int main() {
  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);

  std::string line;
  while (std::getline(std::cin, line)) {
    std::cout << line << '\n';
  }
  return std::cout.good() ? 0 : 1;
}
"""


STYIO_RUNTIME_STDIN_ECHO = r"""extern "C" const char* styio_stdin_read_line();
extern "C" void styio_stdout_write_cstr(const char* s);
extern "C" int styio_runtime_has_error();

int main() {
  while (const char* line = styio_stdin_read_line()) {
    styio_stdout_write_cstr(line);
  }
  return styio_runtime_has_error() ? 1 : 0;
}
"""


@dataclass(frozen=True)
class Workload:
  name: str
  description: str
  styio_source: str
  cpp_source: str
  styio_runtime_source: str


WORKLOADS = {
  "stdin_echo": Workload(
    name="stdin_echo",
    description="stdin line iterator echo to stdout",
    styio_source=STYIO_STDIN_ECHO,
    cpp_source=CPP_STDIN_ECHO,
    styio_runtime_source=STYIO_RUNTIME_STDIN_ECHO,
  ),
}


ROUTE_IDS = ("full-cli", "cached-jit", "runtime-only")


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description="Compare Styio against native C++ across standard performance routes."
  )
  parser.add_argument("--case", choices=sorted(WORKLOADS) + ["all"], default="all")
  parser.add_argument(
    "--routes",
    default="all",
    help="Comma-separated route list: full-cli,cached-jit,runtime-only, or all (default: all)",
  )
  parser.add_argument(
    "--styio-root",
    default=os.environ.get("STYIO_ROOT", str(DEFAULT_STYIO_ROOT)),
    help="Styio source checkout (default: STYIO_ROOT or ../styio)",
  )
  parser.add_argument("--build-dir", default="build/default", help="CMake build directory containing bin/styio")
  parser.add_argument("--styio-exe", default="", help="Explicit styio executable path")
  parser.add_argument("--cxx", default="", help="C++ compiler for native baseline (default: CXX, clang++, c++)")
  parser.add_argument("--out-dir", default="", help="Artifact directory (default: reports/<timestamp>-native-cpp)")
  parser.add_argument("--scale", choices=sorted(SCALE_LINE_COUNTS), default="medium", help="Input size preset (line count); explicit --line-count overrides it")
  parser.add_argument("--line-count", type=int, default=None)
  parser.add_argument("--line-bytes", type=int, default=48)
  parser.add_argument("--repeats", type=int, default=10)
  parser.add_argument("--warmups", type=int, default=1)
  parser.add_argument("--keep-outputs", action="store_true", help="Keep validation outputs instead of deleting them")
  return parser.parse_args()


def utc_stamp() -> str:
  return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_repo_path(raw: str) -> Path:
  path = Path(raw)
  if path.is_absolute():
    return path
  return BENCHMARK_ROOT / path


def resolve_styio_path(styio_root: Path, raw: str) -> Path:
  path = Path(raw)
  if path.is_absolute():
    return path
  return styio_root / path


def find_executable(candidates: list[str]) -> str:
  for candidate in candidates:
    if not candidate:
      continue
    if "/" in candidate:
      path = Path(candidate)
      if path.exists() and os.access(path, os.X_OK):
        return str(path)
      continue
    found = shutil.which(candidate)
    if found:
      return found
  return ""


def find_cxx(explicit: str) -> str:
  found = find_executable([explicit, os.environ.get("CXX", ""), "/usr/lib/llvm-18/bin/clang++", "clang++", "c++"])
  if not found:
    raise RuntimeError("no C++ compiler found; pass --cxx or set CXX")
  return found


def parse_routes(raw: str) -> list[str]:
  if raw == "all":
    return list(ROUTE_IDS)
  routes = [item.strip() for item in raw.split(",") if item.strip()]
  if not routes:
    raise ValueError("--routes must not be empty")
  invalid = [route for route in routes if route not in ROUTE_IDS]
  if invalid:
    raise ValueError(f"unsupported --routes value(s): {', '.join(invalid)}")
  return routes


def sha256_file(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


def write_input(path: Path, line_count: int, line_bytes: int) -> None:
  if line_count <= 0:
    raise ValueError("--line-count must be positive")
  if line_bytes < 12:
    raise ValueError("--line-bytes must be at least 12 so the generated lines stay distinguishable")

  pad_len = line_bytes - 10
  with path.open("w", encoding="utf-8", newline="\n") as handle:
    for i in range(line_count):
      handle.write(f"{i:08d}-")
      handle.write(("abcdefghijklmnopqrstuvwxyz" * ((pad_len // 26) + 1))[:pad_len])
      handle.write("\n")


def run_checked(cmd: list[str], cwd: Path) -> None:
  proc = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
  if proc.returncode != 0:
    raise RuntimeError(
      f"command failed ({proc.returncode}): {shlex.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def validate_echo(name: str, cmd: list[str], input_path: Path, output_path: Path, expected_hash: str) -> None:
  with input_path.open("rb") as stdin, output_path.open("wb") as stdout:
    proc = subprocess.run(cmd, cwd=str(BENCHMARK_ROOT), stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, check=False)
  if proc.returncode != 0:
    raise RuntimeError(
      f"{name} validation failed ({proc.returncode}): {shlex.join(cmd)}\nstderr:\n"
      f"{proc.stderr.decode('utf-8', errors='replace')}"
    )
  actual_hash = sha256_file(output_path)
  if actual_hash != expected_hash:
    raise RuntimeError(f"{name} validation hash mismatch: expected {expected_hash}, got {actual_hash}")


def timed_run(cmd: list[str], input_path: Path) -> float:
  with input_path.open("rb") as stdin, open(os.devnull, "wb") as stdout:
    started = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(BENCHMARK_ROOT), stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, check=False)
    elapsed = time.perf_counter() - started
  if proc.returncode != 0:
    raise RuntimeError(
      f"timed command failed ({proc.returncode}): {shlex.join(cmd)}\nstderr:\n"
      f"{proc.stderr.decode('utf-8', errors='replace')}"
    )
  return elapsed


def percentile95(samples: list[float]) -> float:
  ordered = sorted(samples)
  index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95))))
  return ordered[index]


def percentile(values: list[float], percent: float) -> float:
  ordered = sorted(values)
  if not ordered:
    return 0.0
  if len(ordered) == 1:
    return ordered[0]
  rank = (percent / 100.0) * (len(ordered) - 1)
  lower = int(math.floor(rank))
  upper = int(math.ceil(rank))
  if lower == upper:
    return ordered[lower]
  fraction = rank - lower
  return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def bootstrap_ci(values: list[float]) -> tuple[float, float]:
  if len(values) == 1:
    return values[0], values[0]
  rng = random.Random(BOOTSTRAP_SEED)
  resampled_medians = []
  for _ in range(BOOTSTRAP_RESAMPLES):
    resampled = [values[rng.randrange(len(values))] for _ in values]
    resampled_medians.append(statistics.median(resampled))
  return percentile(resampled_medians, 2.5), percentile(resampled_medians, 97.5)


def coefficient_of_variation(values: list[float]) -> float | None:
  if len(values) < 2:
    return None
  if any(value < 0 for value in values):
    return None
  mean_value = statistics.mean(values)
  if mean_value <= 0:
    return None
  return statistics.stdev(values) / mean_value


def statistic_object(values: list[float]) -> dict[str, Any]:
  low, high = bootstrap_ci(values)
  cv = coefficient_of_variation(values)
  return {
    "sample_count": len(values),
    "median": statistics.median(values),
    "ci95_low": low,
    "ci95_high": high,
    "cv": cv,
    "quality": "ok" if cv is not None else "insufficient_sample",
  }


def average_ranks(values: list[float]) -> list[float]:
  order = sorted(range(len(values)), key=lambda index: values[index])
  ranks = [0.0] * len(values)
  index = 0
  while index < len(order):
    end = index
    while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
      end += 1
    average = (index + end) / 2.0 + 1.0
    for position in range(index, end + 1):
      ranks[order[position]] = average
    index = end + 1
  return ranks


def mann_whitney_u(candidate: list[float], baseline: list[float], alpha: float = ALPHA) -> dict[str, Any]:
  n = len(candidate)
  m = len(baseline)
  if n < 2 or m < 2:
    return {"u": None, "p_value": None, "alpha": alpha, "significant": None, "direction": None, "status": "insufficient_sample"}
  combined = candidate + baseline
  combined_ranks = average_ranks(combined)
  u_a = sum(combined_ranks[:n]) - n * (n + 1) / 2.0
  u_b = n * m - u_a
  u = min(u_a, u_b)
  mu = n * m / 2.0
  ordered = sorted(combined)
  tie_correction = 0.0
  index = 0
  while index < len(ordered):
    end = index
    while end + 1 < len(ordered) and ordered[end + 1] == ordered[index]:
      end += 1
    size = end - index + 1
    if size > 1:
      tie_correction += size**3 - size
    index = end + 1
  total = n + m
  variance = n * m / 12.0 * ((total + 1) - tie_correction / (total * (total - 1)))
  if variance <= 0:
    return {"u": None, "p_value": None, "alpha": alpha, "significant": None, "direction": None, "status": "insufficient_sample"}
  z = max(0.0, mu - u - 0.5) / math.sqrt(variance)
  p_value = math.erfc(z / math.sqrt(2.0))
  return {"u": u, "p_value": p_value, "alpha": alpha, "significant": p_value < alpha, "direction": None, "status": "ok"}


def comparison_direction(candidate_median: float, baseline_median: float, lower_is_better: bool) -> str:
  if candidate_median < baseline_median:
    return "better" if lower_is_better else "worse"
  if candidate_median > baseline_median:
    return "worse" if lower_is_better else "better"
  return "similar"


def throughput_samples(samples_s: list[float], input_bytes: int) -> list[float]:
  return [(input_bytes / (1024.0 * 1024.0)) / value for value in samples_s if value > 0]


def result_record(
  *,
  case: str,
  route: str,
  implementation: str,
  language: str,
  command: list[str],
  samples_s: list[float],
  input_bytes: int,
  line_count: int,
  line_bytes: int,
  repeats: int,
  warmups: int,
) -> dict[str, Any]:
  median_s = statistics.median(samples_s)
  throughput_mib_s = (input_bytes / (1024.0 * 1024.0)) / median_s if median_s > 0 else 0.0
  return {
    "case": case,
    "route": route,
    "implementation": implementation,
    "language": language,
    "mode": route,
    "status": "pass",
    "command": shlex.join(command),
    "input_bytes": input_bytes,
    "input_mib": input_bytes / (1024.0 * 1024.0),
    "line_count": line_count,
    "line_bytes": line_bytes,
    "repeats": repeats,
    "warmups": warmups,
    "samples_s": samples_s,
    "median_s": median_s,
    "min_s": min(samples_s),
    "p95_s": percentile95(samples_s),
    "throughput_mib_s": throughput_mib_s,
    "statistics": {
      "elapsed_s": statistic_object(samples_s),
      "throughput_mib_s": statistic_object(throughput_samples(samples_s, input_bytes)),
    },
  }


def unsupported_record(case: str, route: str, implementation: str, reason: str) -> dict[str, Any]:
  return {
    "case": case,
    "route": route,
    "implementation": implementation,
    "language": "Styio",
    "mode": route,
    "status": "unsupported",
    "reason": reason,
    "relative_x": "",
  }


def add_native_comparisons(records: list[dict[str, Any]]) -> None:
  groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
  for record in records:
    groups.setdefault((str(record["case"]), str(record["route"])), []).append(record)
  for group in groups.values():
    native = next(
      (record for record in group if record.get("status") == "pass" and record.get("implementation") == "native_cpp"),
      None,
    )
    native_samples = native.get("samples_s", []) if native else []
    for record in group:
      if record.get("status") == "unsupported":
        record["comparison"] = {
          "baseline_identity": "native_cpp",
          "u": None,
          "p_value": None,
          "alpha": ALPHA,
          "significant": None,
          "direction": None,
          "status": "not_comparable",
        }
        continue
      if record.get("status") != "pass":
        continue
      if record.get("implementation") == "native_cpp":
        record["comparison"] = {
          "baseline_identity": "native_cpp",
          "u": None,
          "p_value": None,
          "alpha": ALPHA,
          "significant": None,
          "direction": None,
          "status": "baseline",
        }
        continue
      if native is None:
        record["comparison"] = {
          "baseline_identity": "native_cpp",
          "u": None,
          "p_value": None,
          "alpha": ALPHA,
          "significant": None,
          "direction": None,
          "status": "missing_baseline",
        }
        continue
      comparison = mann_whitney_u(record.get("samples_s", []), native_samples)
      comparison["baseline_identity"] = "native_cpp"
      if comparison["status"] == "ok":
        comparison["direction"] = comparison_direction(
          statistics.median(record["samples_s"]), statistics.median(native_samples), lower_is_better=True
        )
      record["comparison"] = comparison


def write_json(path: Path, payload: dict[str, Any]) -> None:
  with path.open("w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
    handle.write("\n")


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
  fields = [
    "case",
    "route",
    "implementation",
    "language",
    "mode",
    "status",
    "reason",
    "line_count",
    "line_bytes",
    "input_mib",
    "repeats",
    "warmups",
    "median_s",
    "min_s",
    "p95_s",
    "throughput_mib_s",
    "relative_x",
    "command",
    "elapsed_ci95_low_s",
    "elapsed_ci95_high_s",
    "elapsed_cv",
    "throughput_ci95_low_mib_s",
    "throughput_ci95_high_mib_s",
    "throughput_cv",
    "significance_u",
    "significance_p_value",
    "significance_alpha",
    "significance_significant",
    "significance_direction",
    "significance_status",
    "significance_baseline_identity",
  ]
  with path.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    for record in records:
      row = {field: record.get(field, "") for field in fields}
      statistics_payload = record.get("statistics", {})
      elapsed = statistics_payload.get("elapsed_s", {})
      throughput = statistics_payload.get("throughput_mib_s", {})
      row["elapsed_ci95_low_s"] = elapsed.get("ci95_low", "")
      row["elapsed_ci95_high_s"] = elapsed.get("ci95_high", "")
      row["elapsed_cv"] = elapsed.get("cv", "")
      row["throughput_ci95_low_mib_s"] = throughput.get("ci95_low", "")
      row["throughput_ci95_high_mib_s"] = throughput.get("ci95_high", "")
      row["throughput_cv"] = throughput.get("cv", "")
      comparison = record.get("comparison", {})
      row["significance_u"] = comparison.get("u", "")
      row["significance_p_value"] = comparison.get("p_value", "")
      row["significance_alpha"] = comparison.get("alpha", "")
      row["significance_significant"] = comparison.get("significant", "")
      row["significance_direction"] = comparison.get("direction", "")
      row["significance_status"] = comparison.get("status", "")
      row["significance_baseline_identity"] = comparison.get("baseline_identity", "")
      writer.writerow(row)


def write_summary(path: Path, metadata: dict[str, Any], records: list[dict[str, Any]]) -> None:
  lines = [
    "# Styio vs Native C++ Benchmark",
    "",
    f"- Started UTC: `{metadata['started_at_utc']}`",
    f"- Host: `{metadata['hostname']}` / `{metadata['uname']}`",
    f"- Styio executable: `{metadata['styio_exe']}`",
    f"- C++ compiler: `{metadata['cxx']}`",
    f"- Routes: `{','.join(metadata['routes'])}`",
    "",
  ]

  for route in metadata["routes"]:
    lines.extend(
      [
        f"## {route}",
        "",
        "| Case | Implementation | Status | Median s | 95% CI s | CV | Throughput MiB/s | Relative | Sig | Direction | Note |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
      ]
    )
    route_records = [record for record in records if record.get("route") == route]
    for record in sorted(route_records, key=lambda item: (item["case"], item["implementation"])):
      if record.get("status") == "pass":
        elapsed = record.get("statistics", {}).get("elapsed_s", {})
        comparison = record.get("comparison", {})
        if comparison.get("status") == "baseline":
          sig = "-"
          direction = "baseline"
        elif comparison.get("status") in ("missing_baseline", "insufficient_sample"):
          sig = "-"
          direction = comparison.get("status", "-")
        else:
          sig = str(comparison.get("significant", "-"))
          direction = comparison.get("direction", "-")
        ci_low = elapsed.get("ci95_low")
        ci_high = elapsed.get("ci95_high")
        ci_text = f"{ci_low:.6f}..{ci_high:.6f}" if ci_low is not None and ci_high is not None else "-"
        cv = elapsed.get("cv")
        cv_text = f"{cv:.3f}" if cv is not None else "-"
        lines.append(
          "| {case} | {implementation} | pass | {median:.6f} | {ci} | {cv} | {throughput:.2f} | {relative:.2f}x | {sig} | {direction} |  |".format(
            case=record["case"],
            implementation=record["implementation"],
            median=record["median_s"],
            ci=ci_text,
            cv=cv_text,
            throughput=record["throughput_mib_s"],
            relative=record["relative_x"],
            sig=sig,
            direction=direction,
          )
        )
      else:
        lines.append(
          "| {case} | {implementation} | {status} |  |  |  |  |  |  | {direction} | {reason} |".format(
            case=record["case"],
            implementation=record["implementation"],
            status=record.get("status", ""),
            direction=record.get("comparison", {}).get("status", ""),
            reason=record.get("reason", ""),
          )
        )
    lines.append("")

  lines.extend(
    [
      "Higher throughput is better. `Relative` normalizes each workload and route so the fastest measured implementation is `1.00x`.",
      "`full-cli` measures Styio source read, parse, lowering, LLVM/JIT work, and execution.",
      "`cached-jit` is reserved for a future reusable compiled/JIT artifact execution path and is reported as unsupported until that CLI/runtime contract exists.",
      "`runtime-only` currently compares native C++ against a C++ harness that calls Styio runtime helpers directly; it isolates helper overhead from frontend/JIT cost, but is not a substitute for generated Styio code execution.",
      "",
    ]
  )
  path.write_text("\n".join(lines), encoding="utf-8")


def compile_native_cpp(cxx: str, cpp_path: Path, native_bin: Path) -> list[str]:
  cmd = [cxx, "-O3", "-DNDEBUG", "-std=c++20", str(cpp_path), "-o", str(native_bin)]
  run_checked(cmd, BENCHMARK_ROOT)
  return cmd


def compile_styio_runtime(
  *,
  cxx: str,
  source_path: Path,
  output_path: Path,
  styio_root: Path,
  build_dir_abs: Path,
) -> list[str]:
  runtime_lib = build_dir_abs / "lib" / "libstyio_runtime_core.a"
  if not runtime_lib.exists():
    raise RuntimeError(f"Styio runtime library not found: {runtime_lib}; build styio_runtime_core first")
  cmd = [
    cxx,
    "-O3",
    "-DNDEBUG",
    "-std=c++20",
    str(source_path),
    str(runtime_lib),
    "-I",
    str(styio_root / "src"),
    "-I",
    str(build_dir_abs / "generated"),
    "-pthread",
    "-o",
    str(output_path),
  ]
  run_checked(cmd, BENCHMARK_ROOT)
  return cmd


def benchmark_command(
  *,
  case_name: str,
  route: str,
  implementation: str,
  language: str,
  cmd: list[str],
  input_path: Path,
  outputs_dir: Path,
  input_hash: str,
  input_bytes: int,
  line_count: int,
  line_bytes: int,
  repeats: int,
  warmups: int,
  keep_outputs: bool,
) -> dict[str, Any]:
  output_path = outputs_dir / f"{case_name}.{route}.{implementation}.txt"
  validate_echo(f"{route}:{implementation}", cmd, input_path, output_path, input_hash)
  if not keep_outputs:
    output_path.unlink(missing_ok=True)

  for _ in range(warmups):
    timed_run(cmd, input_path)

  samples = [timed_run(cmd, input_path) for _ in range(repeats)]
  return result_record(
    case=case_name,
    route=route,
    implementation=implementation,
    language=language,
    command=cmd,
    samples_s=samples,
    input_bytes=input_bytes,
    line_count=line_count,
    line_bytes=line_bytes,
    repeats=repeats,
    warmups=warmups,
  )


def normalize_relative(records: list[dict[str, Any]]) -> None:
  groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
  for record in records:
    if record.get("status") != "pass":
      continue
    groups.setdefault((str(record["case"]), str(record["route"])), []).append(record)

  for group_records in groups.values():
    best = max(float(record["throughput_mib_s"]) for record in group_records)
    for record in group_records:
      record["relative_x"] = float(record["throughput_mib_s"]) / best if best > 0 else 0.0


def add_native_baseline(
  *,
  case_records: list[dict[str, Any]],
  case_name: str,
  route: str,
  native_cmd: list[str],
  input_path: Path,
  outputs_dir: Path,
  input_hash: str,
  input_bytes: int,
  args: argparse.Namespace,
) -> None:
  case_records.append(
    benchmark_command(
      case_name=case_name,
      route=route,
      implementation="native_cpp",
      language="C++20",
      cmd=native_cmd,
      input_path=input_path,
      outputs_dir=outputs_dir,
      input_hash=input_hash,
      input_bytes=input_bytes,
      line_count=args.line_count,
      line_bytes=args.line_bytes,
      repeats=args.repeats,
      warmups=args.warmups,
      keep_outputs=args.keep_outputs,
    )
  )


def run_case(
  *,
  args: argparse.Namespace,
  routes: list[str],
  cxx: str,
  styio_exe: Path,
  styio_root: Path,
  build_dir_abs: Path,
  workload: Workload,
  programs_dir: Path,
  bin_dir: Path,
  inputs_dir: Path,
  outputs_dir: Path,
) -> list[dict[str, Any]]:
  case_name = workload.name
  input_path = inputs_dir / f"{case_name}.txt"
  write_input(input_path, args.line_count, args.line_bytes)
  input_bytes = input_path.stat().st_size
  input_hash = sha256_file(input_path)

  styio_path = programs_dir / f"{case_name}.styio"
  cpp_path = programs_dir / f"{case_name}.cpp"
  styio_runtime_path = programs_dir / f"{case_name}.styio-runtime.cpp"
  native_bin = bin_dir / f"{case_name}_native_cpp"
  styio_runtime_bin = bin_dir / f"{case_name}_styio_runtime_helpers"
  styio_path.write_text(workload.styio_source, encoding="utf-8")
  cpp_path.write_text(workload.cpp_source, encoding="utf-8")
  styio_runtime_path.write_text(workload.styio_runtime_source, encoding="utf-8")

  native_cmd = [str(native_bin)]
  compile_native_cpp(cxx, cpp_path, native_bin)

  runtime_cmd: list[str] | None = None
  if "runtime-only" in routes:
    compile_styio_runtime(
      cxx=cxx,
      source_path=styio_runtime_path,
      output_path=styio_runtime_bin,
      styio_root=styio_root,
      build_dir_abs=build_dir_abs,
    )
    runtime_cmd = [str(styio_runtime_bin)]

  case_records: list[dict[str, Any]] = []
  if "full-cli" in routes:
    add_native_baseline(
      case_records=case_records,
      case_name=case_name,
      route="full-cli",
      native_cmd=native_cmd,
      input_path=input_path,
      outputs_dir=outputs_dir,
      input_hash=input_hash,
      input_bytes=input_bytes,
      args=args,
    )
    case_records.append(
      benchmark_command(
        case_name=case_name,
        route="full-cli",
        implementation="styio",
        language="Styio",
        cmd=[str(styio_exe), "--parser-engine=nightly", "--file", str(styio_path)],
        input_path=input_path,
        outputs_dir=outputs_dir,
        input_hash=input_hash,
        input_bytes=input_bytes,
        line_count=args.line_count,
        line_bytes=args.line_bytes,
        repeats=args.repeats,
        warmups=args.warmups,
        keep_outputs=args.keep_outputs,
      )
    )

  if "cached-jit" in routes:
    add_native_baseline(
      case_records=case_records,
      case_name=case_name,
      route="cached-jit",
      native_cmd=native_cmd,
      input_path=input_path,
      outputs_dir=outputs_dir,
      input_hash=input_hash,
      input_bytes=input_bytes,
      args=args,
    )
    case_records.append(
      unsupported_record(
        case_name,
        "cached-jit",
        "styio",
        "styio CLI/runtime has no reusable compiled or JIT artifact execution entrypoint yet",
      )
    )

  if "runtime-only" in routes:
    add_native_baseline(
      case_records=case_records,
      case_name=case_name,
      route="runtime-only",
      native_cmd=native_cmd,
      input_path=input_path,
      outputs_dir=outputs_dir,
      input_hash=input_hash,
      input_bytes=input_bytes,
      args=args,
    )
    if runtime_cmd is None:
      case_records.append(
        unsupported_record(case_name, "runtime-only", "styio_runtime_helpers", "styio runtime helper harness was not built")
      )
    else:
      case_records.append(
        benchmark_command(
          case_name=case_name,
          route="runtime-only",
          implementation="styio_runtime_helpers",
          language="Styio runtime helpers",
          cmd=runtime_cmd,
          input_path=input_path,
          outputs_dir=outputs_dir,
          input_hash=input_hash,
          input_bytes=input_bytes,
          line_count=args.line_count,
          line_bytes=args.line_bytes,
          repeats=args.repeats,
          warmups=args.warmups,
          keep_outputs=args.keep_outputs,
        )
      )

  normalize_relative(case_records)
  add_native_comparisons(case_records)
  return case_records


def main() -> int:
  args = parse_args()
  routes = parse_routes(args.routes)
  styio_root = resolve_repo_path(args.styio_root)
  build_dir_abs = resolve_styio_path(styio_root, args.build_dir)
  styio_exe = resolve_styio_path(styio_root, args.styio_exe) if args.styio_exe else build_dir_abs / "bin/styio"
  if not styio_exe.exists():
    raise RuntimeError(f"styio executable not found: {styio_exe}; build styio first or pass --styio-exe")
  cxx = find_cxx(args.cxx)

  if args.repeats <= 0:
    raise ValueError("--repeats must be positive")
  if args.warmups < 0:
    raise ValueError("--warmups must be non-negative")
  args.line_count = args.line_count if args.line_count is not None else SCALE_LINE_COUNTS[args.scale]

  out_dir = resolve_repo_path(args.out_dir) if args.out_dir else BENCHMARK_ROOT / "reports" / f"{utc_stamp()}-native-cpp"
  programs_dir = out_dir / "programs"
  bin_dir = out_dir / "bin"
  inputs_dir = out_dir / "inputs"
  outputs_dir = out_dir / "outputs"
  for path in (programs_dir, bin_dir, inputs_dir, outputs_dir):
    path.mkdir(parents=True, exist_ok=True)

  cases = sorted(WORKLOADS) if args.case == "all" else [args.case]
  records: list[dict[str, Any]] = []
  metadata: dict[str, Any] = {
    "started_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "benchmark_root": str(BENCHMARK_ROOT),
    "styio_root": str(styio_root),
    "build_dir": str(build_dir_abs),
    "out_dir": str(out_dir),
    "hostname": subprocess.check_output(["hostname"], text=True).strip(),
    "uname": subprocess.check_output(["uname", "-srm"], text=True).strip(),
    "styio_exe": str(styio_exe),
    "cxx": cxx,
    "scale": args.scale,
    "line_count": args.line_count,
    "line_bytes": args.line_bytes,
    "repeats": args.repeats,
    "warmups": args.warmups,
    "routes": routes,
  }

  for case_name in cases:
    workload = WORKLOADS[case_name]
    records.extend(
      run_case(
        args=args,
        routes=routes,
        cxx=cxx,
        styio_exe=styio_exe,
        styio_root=styio_root,
        build_dir_abs=build_dir_abs,
        workload=workload,
        programs_dir=programs_dir,
        bin_dir=bin_dir,
        inputs_dir=inputs_dir,
        outputs_dir=outputs_dir,
      )
    )

  payload = {"metadata": metadata, "results": records}
  write_json(out_dir / "results.json", payload)
  write_csv(out_dir / "benchmarks.csv", records)
  write_summary(out_dir / "summary.md", metadata, records)

  print(f"[native-cpp-bench] artifacts={out_dir}")
  print((out_dir / "summary.md").read_text(encoding="utf-8"), end="")
  return 0


if __name__ == "__main__":
  try:
    raise SystemExit(main())
  except Exception as exc:  # noqa: BLE001 - benchmark entrypoint should print actionable failures.
    print(f"[native-cpp-bench] error: {exc}", file=sys.stderr)
    raise SystemExit(1)
