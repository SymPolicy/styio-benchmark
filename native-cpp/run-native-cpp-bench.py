#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
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
  parser.add_argument("--line-count", type=int, default=100_000)
  parser.add_argument("--line-bytes", type=int, default=48)
  parser.add_argument("--repeats", type=int, default=5)
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


def write_json(path: Path, payload: dict[str, Any]) -> None:
  with path.open("w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
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
  ]
  with path.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    for record in records:
      writer.writerow({field: record.get(field, "") for field in fields})


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
        "| Case | Implementation | Status | Median s | Throughput MiB/s | Relative | Note |",
        "| --- | --- | --- | ---: | ---: | ---: | --- |",
      ]
    )
    route_records = [record for record in records if record.get("route") == route]
    for record in sorted(route_records, key=lambda item: (item["case"], item["implementation"])):
      if record.get("status") == "pass":
        lines.append(
          "| {case} | {implementation} | pass | {median:.6f} | {throughput:.2f} | {relative:.2f}x |  |".format(
            case=record["case"],
            implementation=record["implementation"],
            median=record["median_s"],
            throughput=record["throughput_mib_s"],
            relative=record["relative_x"],
          )
        )
      else:
        lines.append(
          "| {case} | {implementation} | {status} |  |  |  | {reason} |".format(
            case=record["case"],
            implementation=record["implementation"],
            status=record.get("status", ""),
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
