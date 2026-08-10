#!/usr/bin/env python3
"""
Compare two styio_core_bench JSON results and report regressions.

Usage:
  python3 tools/core-benchmark-compare.py baseline.json current.json [--threshold 5.0]
  python3 tools/core-benchmark-compare.py auto current.json [--baseline-dir reports/core]

Exit code is 0 when all benchmarks are within threshold.
Exit code is 1 when one or more benchmarks degrade beyond threshold.
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Optional

ROUTE_CACHE_FIELDS = (
    ("scan", "route_cache_scan_count"),
    ("miss", "route_cache_miss_count"),
    ("hit", "route_cache_hit_count"),
    ("disabled", "route_cache_disabled_count"),
)

IR_ALLOC_FIELDS = (
    ("arena", "ir_arena_allocations"),
    ("raw", "ir_raw_allocations"),
    ("bytes", "ir_bytes_allocated"),
    ("nodes", "ir_node_count"),
    ("peak", "ir_max_node_count"),
    ("dtors", "ir_destructor_calls"),
)

SCHEDULER_FIELDS = (
    ("queue_kind", "task_scheduler_queue_kind"),
    ("workers", "task_scheduler_worker_count"),
)


def load_results(path: str) -> dict:
    with open(path, "r") as f:
        data = json.load(f)

    # Support both v1 (samples) and v2 (benchmarks) schema
    bench_list = data.get("benchmarks", data.get("samples", []))
    samples = {}
    for s in bench_list:
        # v2: "name" = "phase/label"; v1: separate "phase"/"label"
        if "name" in s:
            key = s["name"]
        else:
            key = f"{s['phase']}/{s['label']}"
        is_route_cache = key.startswith("route_cache/")
        # Use median_ns if available, else duration_ns
        ns = s.get("median_ns", s.get("duration_ns", 0))
        samples[key] = {
            "median_ns": ns,
            "mean_ns": s.get("mean_ns", ns),
            "p95_ns": s.get("p95_ns", ns),
            "min_ns": s.get("min_ns", ns),
            "max_ns": s.get("max_ns", ns),
            "iterations": s.get("iterations", 0),
        }
        for _, source_key in ROUTE_CACHE_FIELDS:
            if source_key in s:
                samples[key][source_key] = s[source_key]
            elif is_route_cache:
                samples[key][source_key] = 0
        has_ir_alloc = any(source_key in s for _, source_key in IR_ALLOC_FIELDS)
        if has_ir_alloc:
            for _, source_key in IR_ALLOC_FIELDS:
                samples[key][source_key] = s.get(source_key, 0)
        has_scheduler = any(source_key in s for _, source_key in SCHEDULER_FIELDS)
        if has_scheduler:
            for _, source_key in SCHEDULER_FIELDS:
                samples[key][source_key] = s.get(source_key, 0)
    return {
        "git_sha": data.get("commit", data.get("git_sha", "unknown")),
        "build_type": data.get("build_type", "unknown"),
        "samples": samples,
    }


def _resolve_baseline_path(baseline_arg: str, current_path: Path,
                           baseline_dir: str = None) -> Optional[Path]:
    if baseline_arg.lower() != "auto":
        return Path(baseline_arg)

    search_dirs = []
    if baseline_dir:
        search_dirs.append(Path(baseline_dir))
    if current_path.parent not in search_dirs:
        search_dirs.append(current_path.parent)

    candidate_names = [
        "baseline.json",
        f"{current_path.stem}.baseline.json",
        f"{current_path.stem}-baseline.json",
        f"baseline-{current_path.stem}.json",
    ]
    if current_path.name.startswith("current"):
        candidate_names.append(current_path.name.replace("current", "baseline", 1))

    seen = set()
    for search_dir in search_dirs:
        for candidate_name in candidate_names:
            candidate = search_dir / candidate_name
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.exists():
                return candidate

    return None


def compare(baseline: dict, current: dict, threshold_pct: float,
            markdown_path: str = None, allow_missing_new: bool = False,
            require_improvement: dict = None,
            show_route_cache: bool = False,
            show_ir_alloc: bool = False,
            show_scheduler: bool = False) -> int:
    baseline_samples = baseline["samples"]
    current_samples = current["samples"]

    all_keys = sorted(set(baseline_samples.keys()) | set(current_samples.keys()))

    lines = []
    md_lines = []

    def emit(s: str):
        lines.append(s)

    def md(s: str):
        md_lines.append(s)

    emit(f"Baseline: {baseline['git_sha']} ({baseline['build_type']})")
    emit(f"Current:  {current['git_sha']} ({current['build_type']})")
    emit(f"Threshold: {threshold_pct}%")
    emit("")
    emit(f"{'Benchmark':<45} {'Baseline':>12} {'Current':>12} {'Delta %':>10} {'Status':>12}")
    emit("-" * 93)

    md("| Benchmark | Baseline | Current | Delta % | Status |")
    md("|-----------|----------|---------|---------|--------|")

    regressions = 0
    improvements = 0
    missing = 0
    new_benchmarks = 0

    for key in all_keys:
        base = baseline_samples.get(key)
        curr = current_samples.get(key)

        if base is None:
            msg = f"{key:<45} {'N/A':>12} {_fmt_ns(curr['median_ns']):>12} {'N/A':>10} {'NEW':>12}"
            emit(msg)
            md(f"| {key} | N/A | {_fmt_ns(curr['median_ns'])} | N/A | NEW |")
            new_benchmarks += 1
            if not allow_missing_new:
                missing += 1
            continue

        if curr is None:
            msg = f"{key:<45} {_fmt_ns(base['median_ns']):>12} {'N/A':>12} {'N/A':>10} {'MISSING':>12}"
            emit(msg)
            md(f"| {key} | {_fmt_ns(base['median_ns'])} | N/A | N/A | MISSING |")
            missing += 1
            continue

        base_ns = base["median_ns"]
        curr_ns = curr["median_ns"]

        if base_ns == 0:
            delta_pct = 0.0
        else:
            delta_pct = ((curr_ns - base_ns) / base_ns) * 100.0

        if delta_pct > threshold_pct:
            status = "REGRESSION"
            regressions += 1
        elif delta_pct < -threshold_pct:
            status = "improved"
            improvements += 1
        else:
            status = "stable"

        emit(
            f"{key:<45} {_fmt_ns(base_ns):>12} {_fmt_ns(curr_ns):>12} "
            f"{delta_pct:>+9.1f}% {status:>12}"
        )
        md(f"| {key} | {_fmt_ns(base_ns)} | {_fmt_ns(curr_ns)} | {delta_pct:+.1f}% | {status} |")

    emit("")
    emit(f"Summary: {regressions} regression(s), {improvements} improvement(s), "
         f"{new_benchmarks} new, {missing} missing")
    md("")
    md(f"**Summary:** {regressions} regression(s), {improvements} improvement(s), "
       f"{new_benchmarks} new, {missing} missing")

    if show_route_cache:
        emit("")
        emit("Route cache counters:")
        emit(f"{'Benchmark':<45} {'Metric':<10} {'Baseline':>12} {'Current':>12} {'Delta':>12}")
        emit("-" * 93)
        md_lines.append("| Benchmark | Metric | Baseline | Current | Delta |")
        md_lines.append("|----------|--------|----------|---------|-------|")
        for key in all_keys:
            base_sample = baseline_samples.get(key)
            curr_sample = current_samples.get(key)
            if not (_has_route_cache_fields(base_sample) or _has_route_cache_fields(curr_sample)):
                continue
            for metric_name, source_key in ROUTE_CACHE_FIELDS:
                if base_sample is None or source_key not in base_sample:
                    base_val = None
                    base_display = "N/A"
                else:
                    base_val = base_sample[source_key]
                    base_display = str(base_val)

                if curr_sample is None or source_key not in curr_sample:
                    curr_val = None
                    curr_display = "N/A"
                else:
                    curr_val = curr_sample[source_key]
                    curr_display = str(curr_val)

                if base_val is None or curr_val is None:
                    delta_display = "N/A"
                else:
                    delta = curr_val - base_val
                    delta_display = f"{delta:+d}"

                emit(
                    f"{key:<45} {metric_name:<10} "
                    f"{base_display:>12} {curr_display:>12} {delta_display:>12}"
                )
                md_lines.append(
                    f"| {key} | {metric_name} | {base_display} | {curr_display} | {delta_display} |"
                )

    if show_ir_alloc:
        emit("")
        emit("IR allocation stats:")
        emit(f"{'Benchmark':<45} {'Metric':<10} {'Baseline':>12} {'Current':>12} {'Delta':>12}")
        emit("-" * 93)
        md_lines.append("| Benchmark | Metric | Baseline | Current | Delta |")
        md_lines.append("|----------|--------|----------|---------|-------|")
        for key in all_keys:
            base_sample = baseline_samples.get(key)
            curr_sample = current_samples.get(key)
            if not (_has_ir_alloc_fields(base_sample) or _has_ir_alloc_fields(curr_sample)):
                continue
            for metric_name, source_key in IR_ALLOC_FIELDS:
                if base_sample is None or source_key not in base_sample:
                    base_val = None
                    base_display = "N/A"
                else:
                    base_val = base_sample[source_key]
                    base_display = str(base_val)

                if curr_sample is None or source_key not in curr_sample:
                    curr_val = None
                    curr_display = "N/A"
                else:
                    curr_val = curr_sample[source_key]
                    curr_display = str(curr_val)

                if base_val is None or curr_val is None:
                    delta_display = "N/A"
                else:
                    delta = curr_val - base_val
                    delta_display = f"{delta:+d}"

                emit(
                    f"{key:<45} {metric_name:<10} "
                    f"{base_display:>12} {curr_display:>12} {delta_display:>12}"
                )
                md_lines.append(
                    f"| {key} | {metric_name} | {base_display} | {curr_display} | {delta_display} |"
                )

    if show_scheduler:
        emit("")
        emit("Scheduler metadata:")
        emit(f"{'Benchmark':<45} {'Metric':<12} {'Baseline':>12} {'Current':>12} {'Delta':>12}")
        emit("-" * 95)
        md_lines.append("| Benchmark | Metric | Baseline | Current | Delta |")
        md_lines.append("|----------|--------|----------|---------|-------|")
        for key in all_keys:
            base_sample = baseline_samples.get(key)
            curr_sample = current_samples.get(key)
            if not (_has_scheduler_fields(base_sample) or _has_scheduler_fields(curr_sample)):
                continue
            for metric_name, source_key in SCHEDULER_FIELDS:
                if base_sample is None or source_key not in base_sample:
                    base_val = None
                    base_display = "N/A"
                else:
                    base_val = base_sample[source_key]
                    base_display = str(base_val)

                if curr_sample is None or source_key not in curr_sample:
                    curr_val = None
                    curr_display = "N/A"
                else:
                    curr_val = curr_sample[source_key]
                    curr_display = str(curr_val)

                if base_val is None or curr_val is None:
                    delta_display = "N/A"
                else:
                    delta = curr_val - base_val
                    delta_display = f"{delta:+d}"

                emit(
                    f"{key:<45} {metric_name:<12} "
                    f"{base_display:>12} {curr_display:>12} {delta_display:>12}"
                )
                md_lines.append(
                    f"| {key} | {metric_name} | {base_display} | {curr_display} | {delta_display} |"
                )

    # Check required improvements
    req_failures = []
    if require_improvement:
        for bench_name, required_pct in require_improvement.items():
            if bench_name in baseline_samples and bench_name in current_samples:
                base_ns = baseline_samples[bench_name]["median_ns"]
                curr_ns = current_samples[bench_name]["median_ns"]
                if base_ns > 0:
                    actual_pct = -((base_ns - curr_ns) / base_ns) * 100.0
                    if actual_pct < required_pct:
                        req_failures.append(
                            f"{bench_name}: required {required_pct}% improvement, "
                            f"got {actual_pct:+.1f}%"
                        )
            else:
                req_failures.append(f"{bench_name}: missing from baseline or current")

    # Write markdown if requested
    if markdown_path:
        with open(markdown_path, "w") as f:
            f.write("\n".join(md_lines) + "\n")

    # Print output
    print("\n".join(lines))

    # Print requirement failures
    if req_failures:
        print(f"\nFAIL: {len(req_failures)} required improvement(s) not met:")
        for f in req_failures:
            print(f"  - {f}")

    if regressions > 0:
        print(f"\nFAIL: {regressions} benchmark(s) degraded beyond {threshold_pct}% threshold.")
        return 1
    if req_failures:
        return 1
    print(f"\nPASS: all benchmarks within {threshold_pct}% threshold.")
    return 0


def _has_route_cache_fields(sample) -> bool:
    if sample is None:
        return False
    return any(field in sample for _, field in ROUTE_CACHE_FIELDS)


def _has_ir_alloc_fields(sample) -> bool:
    if sample is None:
        return False
    return any(field in sample for _, field in IR_ALLOC_FIELDS)


def _has_scheduler_fields(sample) -> bool:
    if sample is None:
        return False
    return any(field in sample for _, field in SCHEDULER_FIELDS)


def _fmt_ns(ns: int) -> str:
    if ns >= 1_000_000_000:
        return f"{ns / 1_000_000_000:.2f} s"
    elif ns >= 1_000_000:
        return f"{ns / 1_000_000:.2f} ms"
    elif ns >= 1_000:
        return f"{ns / 1_000:.2f} us"
    else:
        return f"{ns} ns"


def main():
    parser = argparse.ArgumentParser(
        description="Compare styio_core_bench JSON results"
    )
    parser.add_argument(
        "baseline",
        help="Path to baseline JSON file, or 'auto' to search for a sibling baseline.json"
    )
    parser.add_argument("current", help="Path to current JSON file")
    parser.add_argument(
        "--baseline-dir", type=str, default=None,
        help="Directory to search when baseline is set to 'auto'"
    )
    parser.add_argument(
        "--threshold", type=float, default=5.0,
        help="Regression threshold in percent (default: 5.0)"
    )
    parser.add_argument(
        "--markdown", type=str, default=None,
        help="Write markdown summary to this path"
    )
    parser.add_argument(
        "--allow-missing-new", action="store_true", default=False,
        help="Allow new benchmarks in current that are missing from baseline"
    )
    parser.add_argument(
        "--require-improvement", type=str, default=None,
        help="Comma-separated bench:min_pct entries that must improve, e.g. 'parse/many_stmts_1k:10'"
    )
    parser.add_argument(
        "--route-cache", action="store_true", default=False,
        help="Include route cache counter summary in the report."
    )
    parser.add_argument(
        "--ir-alloc", action="store_true", default=False,
        help="Include IR allocation counter summary in the report."
    )
    parser.add_argument(
        "--scheduler", action="store_true", default=False,
        help="Include task scheduler queue metadata in the report."
    )
    args = parser.parse_args()

    current_path = Path(args.current)
    if not current_path.exists():
        print(f"ERROR: current file not found: {args.current}")
        sys.exit(2)

    baseline_path = _resolve_baseline_path(args.baseline, current_path, args.baseline_dir)
    if baseline_path is None:
        if args.baseline.lower() == "auto":
            search_dirs = []
            if args.baseline_dir:
                search_dirs.append(str(Path(args.baseline_dir)))
            search_dirs.append(str(current_path.parent))
            print(
                "ERROR: could not auto-detect baseline file for "
                f"{args.current}. Looked in: {', '.join(search_dirs)}"
            )
        else:
            print(f"ERROR: baseline file not found: {args.baseline}")
        sys.exit(2)
    if args.baseline.lower() != "auto" and not baseline_path.exists():
        print(f"ERROR: baseline file not found: {args.baseline}")
        sys.exit(2)

    # Parse required improvements
    req_improve = {}
    if args.require_improvement:
        for item in args.require_improvement.split(","):
            item = item.strip()
            if ":" in item:
                name, pct = item.rsplit(":", 1)
                req_improve[name.strip()] = float(pct)

    baseline = load_results(str(baseline_path))
    current = load_results(str(current_path))

    return compare(baseline, current, args.threshold,
                   markdown_path=args.markdown,
                   allow_missing_new=args.allow_missing_new,
                   require_improvement=req_improve if req_improve else None,
                   show_route_cache=args.route_cache,
                   show_ir_alloc=args.ir_alloc,
                   show_scheduler=args.scheduler)


if __name__ == "__main__":
    sys.exit(main())
