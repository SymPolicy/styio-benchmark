#!/usr/bin/env python3
"""Compare two benchmark report files and classify per-key regressions.

Accepts the report payloads produced by the async and native runners. The
report is deterministic, stdlib-only, and privacy-safe: input paths are never
echoed, and no hostname or environment is read.

Exit codes:
  0  report produced (with or without regressions)
  1  --fail-on-regression and at least one classified regression
  2  invalid input (missing file, bad JSON, unknown shape, bad arguments)
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

VERSION = "1"
ALPHA = 0.05
PRACTICAL_THRESHOLD = 5.0

ASYNC_METRICS: tuple[tuple[str, str, bool], ...] = (
    ("sleep", "sequential_ms", True),
    ("sleep", "parallel_ms", True),
    ("sleep", "speedup", False),
    ("noop", "total_us", True),
    ("noop", "per_task_us", True),
)


def median(values: list[float]) -> float:
    return statistics.median(values)


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


def mann_whitney_u(candidate: list[float], baseline: list[float], alpha: float) -> dict[str, Any]:
    n = len(candidate)
    m = len(baseline)
    if n < 2 or m < 2:
        return {
            "u": None,
            "p_value": None,
            "alpha": alpha,
            "significant": None,
            "status": "insufficient_sample",
        }
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
        return {
            "u": None,
            "p_value": None,
            "alpha": alpha,
            "significant": None,
            "status": "insufficient_sample",
        }
    z = max(0.0, mu - u - 0.5) / math.sqrt(variance)
    p_value = math.erfc(z / math.sqrt(2.0))
    return {
        "u": u,
        "p_value": p_value,
        "alpha": alpha,
        "significant": p_value < alpha,
        "status": "ok",
    }


def load_payload(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError:
        raise ValueError("cannot read input file") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"input is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("input must contain a JSON object with a results list")
    return payload


def detect_shape(payload: dict[str, Any]) -> str | None:
    shapes: set[str] = set()
    for result in payload["results"]:
        if not isinstance(result, dict):
            continue
        if all(field in result for field in ("case", "route", "implementation")):
            shapes.add("native")
        if "runtime_key" in result:
            shapes.add("async")
        samples = result.get("samples")
        if isinstance(samples, list) and samples and isinstance(samples[0], dict):
            if "sleep" in samples[0] or "noop" in samples[0]:
                shapes.add("async")
    return next(iter(shapes)) if len(shapes) == 1 else None


def finite_sample(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def async_sample_values(result: dict[str, Any], workload: str, metric: str) -> list[float]:
    values: list[float] = []
    samples = result.get("samples")
    if not isinstance(samples, list):
        return values
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        workload_payload = sample.get(workload)
        if sample.get("status") != "ok" or not isinstance(workload_payload, dict):
            continue
        value = finite_sample(workload_payload.get(metric))
        if value is not None:
            values.append(value)
    return values


def async_extract(payload: dict[str, Any]) -> dict[tuple[str, str, str], list[float]]:
    extracted: dict[tuple[str, str, str], list[float]] = {}
    for result in payload["results"]:
        if not isinstance(result, dict):
            continue
        runtime_key = result.get("runtime_key")
        if not isinstance(runtime_key, str) or not runtime_key:
            continue
        for workload, metric, _ in ASYNC_METRICS:
            values = async_sample_values(result, workload, metric)
            key = (runtime_key, workload, metric)
            if key in extracted:
                raise ValueError("input contains a duplicate async metric identity")
            extracted[key] = values
    return extracted


def native_extract(payload: dict[str, Any]) -> dict[tuple[str, str, str], list[float]]:
    extracted: dict[tuple[str, str, str], list[float]] = {}
    for result in payload["results"]:
        if not isinstance(result, dict) or result.get("status") != "pass":
            continue
        identity = (result.get("case"), result.get("route"), result.get("implementation"))
        if not all(isinstance(part, str) and part for part in identity):
            continue
        case, route, implementation = identity
        samples = result.get("samples_s")
        values = (
            []
            if not isinstance(samples, list)
            else [value for sample in samples if (value := finite_sample(sample)) is not None]
        )
        key = (case, route, implementation)
        if key in extracted:
            raise ValueError("input contains a duplicate native metric identity")
        extracted[key] = values
    return extracted


def regression_percent(candidate_median: float, baseline_median: float, lower_is_better: bool) -> float | None:
    if baseline_median == 0:
        return None
    if lower_is_better:
        return (candidate_median - baseline_median) / baseline_median * 100.0
    return (baseline_median - candidate_median) / baseline_median * 100.0


def compare_pair(
    key: tuple[str, str, str],
    metric: str,
    baseline_values: list[float],
    candidate_values: list[float],
    *,
    alpha: float,
    threshold: float,
    lower_is_better: bool,
) -> dict[str, Any]:
    baseline_median = median(baseline_values) if baseline_values else None
    candidate_median = median(candidate_values) if candidate_values else None
    test = mann_whitney_u(candidate_values, baseline_values, alpha)
    percent = (
        regression_percent(candidate_median, baseline_median, lower_is_better)
        if candidate_median is not None and baseline_median is not None
        else None
    )
    if test["status"] == "insufficient_sample":
        status = "insufficient_sample"
    elif percent is None:
        status = "incompatible_input"
    elif test["significant"] and percent is not None and percent >= threshold:
        status = "regression"
    elif test["significant"] and percent is not None and percent <= -threshold:
        status = "improvement"
    else:
        status = "inconclusive"
    return {
        "key": list(key),
        "metric": metric,
        "baseline_median": baseline_median,
        "candidate_median": candidate_median,
        "regression_percent": percent,
        "u": test["u"],
        "p_value": test["p_value"],
        "alpha": test["alpha"],
        "significant": test["significant"],
        "status": status,
    }


def build_report(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    shape: str,
    baseline_label: str,
    candidate_label: str,
    alpha: float,
    threshold: float,
) -> dict[str, Any]:
    if shape == "async":
        baseline_data = async_extract(baseline)
        candidate_data = async_extract(candidate)
        lower_by_metric: dict[str, bool] = {f"{workload}.{metric}": lower for workload, metric, lower in ASYNC_METRICS}
    else:
        baseline_data = native_extract(baseline)
        candidate_data = native_extract(candidate)
        lower_by_metric = {"elapsed_s": True}

    all_keys = sorted(set(baseline_data) | set(candidate_data))
    if not all_keys:
        raise ValueError("input reports contain no benchmark metric identities")
    comparisons: list[dict[str, Any]] = []
    for key in all_keys:
        metric = f"{key[1]}.{key[2]}" if shape == "async" else "elapsed_s"
        lower_is_better = lower_by_metric.get(metric, True)
        baseline_values = baseline_data.get(key, [])
        candidate_values = candidate_data.get(key, [])
        if key not in baseline_data or key not in candidate_data:
            comparisons.append(
                {
                    "key": list(key),
                    "metric": metric,
                    "baseline_median": median(baseline_values) if baseline_values else None,
                    "candidate_median": median(candidate_values) if candidate_values else None,
                    "regression_percent": None,
                    "u": None,
                    "p_value": None,
                    "alpha": alpha,
                    "significant": None,
                    "status": "incompatible_input",
                }
            )
            continue
        comparisons.append(
            compare_pair(key, metric, baseline_values, candidate_values, alpha=alpha, threshold=threshold, lower_is_better=lower_is_better)
        )

    summary = {
        "total": len(comparisons),
        "regression": 0,
        "improvement": 0,
        "inconclusive": 0,
        "insufficient_sample": 0,
        "incompatible_input": 0,
    }
    for item in comparisons:
        summary[item["status"]] += 1
    summary["has_regressions"] = summary["regression"] > 0

    missing = {
        "baseline_only": sorted(f"{'/'.join(key)}" for key in all_keys if key not in candidate_data),
        "candidate_only": sorted(f"{'/'.join(key)}" for key in all_keys if key not in baseline_data),
    }

    return {
        "version": VERSION,
        "shape": shape,
        "labels": {"baseline": baseline_label, "candidate": candidate_label},
        "settings": {"alpha": alpha, "practical_threshold": threshold},
        "summary": summary,
        "missing": missing,
        "comparisons": comparisons,
    }


def escape_markdown(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    return "".join(f"\\{character}" if character in "\\`*_[]<>|" else character for character in text)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Benchmark comparison",
        "",
        f"- Baseline: {escape_markdown(report['labels']['baseline'])}",
        f"- Candidate: {escape_markdown(report['labels']['candidate'])}",
        f"- Shape: `{report['shape']}`; alpha `{report['settings']['alpha']}`; practical threshold `{report['settings']['practical_threshold']}%`",
        "",
        "| Key | Metric | Baseline median | Candidate median | Change % | p-value | Significant | Status |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for item in report["comparisons"]:
        key = escape_markdown("/".join(str(part) for part in item["key"]))
        metric = escape_markdown(item["metric"])
        percent = "-" if item["regression_percent"] is None else f"{item['regression_percent']:.2f}"
        p_value = "-" if item["p_value"] is None else f"{item['p_value']:.4f}"
        significant = "-" if item["significant"] is None else str(item["significant"])
        baseline_median = "-" if item["baseline_median"] is None else f"{item['baseline_median']:.6f}"
        candidate_median = "-" if item["candidate_median"] is None else f"{item['candidate_median']:.6f}"
        lines.append(
            f"| {key} | {metric} | {baseline_median} | {candidate_median} | {percent} | {p_value} | {significant} | {item['status']} |"
        )
    lines.append("")
    lines.append("Positive `Change %` means the candidate is worse; negative means better.")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare two benchmark report files and classify regressions.")
    parser.add_argument("--baseline", required=True, help="baseline report JSON path")
    parser.add_argument("--candidate", required=True, help="candidate report JSON path")
    parser.add_argument("--out-json", default="", help="optional JSON report output path")
    parser.add_argument("--out-md", default="", help="optional Markdown report output path")
    parser.add_argument("--baseline-label", default="baseline", help="baseline display label")
    parser.add_argument("--label", default="candidate", help="candidate display label")
    parser.add_argument("--alpha", type=float, default=ALPHA, help="significance level (default 0.05)")
    parser.add_argument("--practical-threshold", type=float, default=PRACTICAL_THRESHOLD, help="minimum change percent for a regression (default 5.0)")
    parser.add_argument("--fail-on-regression", action="store_true", help="exit 1 when any regression is classified")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if not math.isfinite(args.alpha) or not (0.0 < args.alpha < 1.0):
        print("benchmark-compare: --alpha must be between 0 and 1", file=sys.stderr)
        return 2
    if not math.isfinite(args.practical_threshold) or args.practical_threshold <= 0:
        print("benchmark-compare: --practical-threshold must be positive", file=sys.stderr)
        return 2
    try:
        input_paths = {Path(args.baseline).resolve(), Path(args.candidate).resolve()}
        output_paths = [Path(raw).resolve() for raw in (args.out_json, args.out_md) if raw]
    except (OSError, RuntimeError):
        print("benchmark-compare: cannot resolve input or output file", file=sys.stderr)
        return 2
    if any(path in input_paths for path in output_paths) or len(set(output_paths)) != len(output_paths):
        print("benchmark-compare: output files must be distinct from each other and from input files", file=sys.stderr)
        return 2
    try:
        baseline = load_payload(Path(args.baseline))
        candidate = load_payload(Path(args.candidate))
    except ValueError as exc:
        print(f"benchmark-compare: {exc}", file=sys.stderr)
        return 2
    baseline_shape = detect_shape(baseline)
    candidate_shape = detect_shape(candidate)
    if baseline_shape is None or candidate_shape is None:
        print("benchmark-compare: unrecognized report shape (expected async samples or native samples_s)", file=sys.stderr)
        return 2
    if baseline_shape != candidate_shape:
        print("benchmark-compare: baseline and candidate report schemas are incompatible", file=sys.stderr)
        return 2
    try:
        report = build_report(
            baseline,
            candidate,
            shape=baseline_shape,
            baseline_label=args.baseline_label,
            candidate_label=args.label,
            alpha=args.alpha,
            threshold=args.practical_threshold,
        )
        serialized = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
        if args.out_json:
            Path(args.out_json).write_text(serialized, encoding="utf-8")
        if args.out_md:
            Path(args.out_md).write_text(render_markdown(report), encoding="utf-8")
    except (OSError, ValueError):
        print("benchmark-compare: cannot produce comparison report", file=sys.stderr)
        return 2
    print(serialized)
    if args.fail_on_regression and report["summary"]["has_regressions"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
