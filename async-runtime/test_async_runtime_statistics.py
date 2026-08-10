from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
RUNNER = BENCHMARK_ROOT / "async-runtime" / "run-async-bench.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("async_run_bench", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["async_run_bench"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner():
    return load_runner()


def build_result(runner, *, runtime_key: str, runtime: str, seq: float, par: float, total: float, per: float, repeats: int = 10) -> dict:
    samples = []
    for index in range(repeats):
        noise = (index % 3) * 0.5
        samples.append(
            {
                "status": "ok",
                "sleep": {
                    "tasks": 4,
                    "sleep_ms": 160,
                    "sequential_ms": seq + noise,
                    "parallel_ms": par + noise,
                    "speedup": (seq + noise) / (par + noise),
                },
                "noop": {
                    "tasks": 100000,
                    "total_us": total + noise * 100.0,
                    "per_task_us": per + noise * 0.001,
                },
            }
        )
    result = runner.summarize_ok_samples(samples)
    result["runtime_key"] = runtime_key
    result["runtime"] = runtime
    result["language"] = runtime
    result["toolchain"] = "synthetic"
    result["workers"] = 4
    return result


# --- median ---


def test_median_odd(runner):
    assert runner.median([3, 1, 2]) == 2.0


def test_median_even(runner):
    assert runner.median([1, 2, 3, 4]) == 2.5


# --- bootstrap ---


def test_bootstrap_is_deterministic(runner):
    values = [10.0, 11.0, 12.0, 9.0, 10.5]
    assert runner.bootstrap_ci(values) == runner.bootstrap_ci(values)


def test_bootstrap_bounds_within_sample_range(runner):
    values = [10.0, 11.0, 12.0, 9.0, 10.5]
    low, high = runner.bootstrap_ci(values)
    assert min(values) <= low <= high <= max(values)


def test_bootstrap_one_sample_degenerate(runner):
    low, high = runner.bootstrap_ci([3.5])
    assert low == high == 3.5


# --- CV ---


def test_cv_positive(runner):
    cv = runner.coefficient_of_variation([10.0, 12.0, 11.0])
    assert cv is not None and cv > 0


def test_cv_zero_mean_is_none(runner):
    assert runner.coefficient_of_variation([0.0, 0.0]) is None


def test_cv_one_sample_is_none(runner):
    assert runner.coefficient_of_variation([3.0]) is None


# --- statistic object ---


def test_statistic_object_schema(runner):
    stat = runner.statistic_object([10.0, 11.0, 12.0])
    assert set(stat.keys()) == {"sample_count", "median", "ci95_low", "ci95_high", "cv", "quality"}
    assert stat["sample_count"] == 3
    assert stat["median"] == 11.0
    assert stat["quality"] == "ok"
    json.dumps(stat, allow_nan=False)


def test_statistic_object_one_sample_quality(runner):
    stat = runner.statistic_object([3.5])
    assert stat["median"] == 3.5
    assert stat["ci95_low"] == stat["ci95_high"] == 3.5
    assert stat["cv"] is None
    assert stat["quality"] == "insufficient_sample"


# --- Mann-Whitney U ---


def test_mwu_fully_separated_known_anchor(runner):
    result = runner.mann_whitney_u([4.0, 5.0, 6.0], [1.0, 2.0, 3.0])
    assert result["status"] == "ok"
    assert result["u"] == 0.0
    assert 0.05 < result["p_value"] < 0.15
    assert result["significant"] is False


def test_mwu_tied_ranks_known_anchor(runner):
    result = runner.mann_whitney_u([5.0, 5.0, 6.0, 6.0], [1.0, 2.0, 3.0, 4.0])
    assert result["status"] == "ok"
    assert result["u"] == 0.0
    assert 0.02 < result["p_value"] < 0.04


def test_mwu_overlapping_samples_use_combined_ranks(runner):
    result = runner.mann_whitney_u([1.0, 3.0, 5.0], [2.0, 4.0, 6.0])
    assert result["status"] == "ok"
    assert result["u"] == 3.0
    assert 0.65 < result["p_value"] < 0.67


def test_mwu_equal_distributions_have_unit_p_value(runner):
    result = runner.mann_whitney_u([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert result["status"] == "ok"
    assert result["u"] == 4.5
    assert result["p_value"] == 1.0


def test_mwu_constant_sample_against_varying_sample_is_valid(runner):
    result = runner.mann_whitney_u([5.0, 5.0, 5.0, 5.0], [1.0, 2.0, 3.0, 4.0])
    assert result["status"] == "ok"
    assert result["u"] == 0.0
    assert result["p_value"] is not None


def test_mwu_all_tied_values_are_insufficient(runner):
    result = runner.mann_whitney_u([5.0] * 4, [5.0] * 4)
    assert result["status"] == "insufficient_sample"
    assert result["u"] is None
    assert result["p_value"] is None


def test_mwu_too_few_samples_insufficient(runner):
    result = runner.mann_whitney_u([4.0], [1.0, 2.0, 3.0])
    assert result["status"] == "insufficient_sample"


def test_mwu_direction_lower_is_better(runner):
    result = runner.mann_whitney_u([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
    assert result["status"] == "ok"
    direction = runner.comparison_direction(2.0, 5.0, lower_is_better=True)
    assert direction == "better"
    direction_worse = runner.comparison_direction(5.0, 2.0, lower_is_better=True)
    assert direction_worse == "worse"
    direction_speedup = runner.comparison_direction(5.0, 2.0, lower_is_better=False)
    assert direction_speedup == "better"


# --- comparisons wiring ---


def test_cpp_comparisons_missing_baseline(runner):
    results = [build_result(runner, runtime_key="styio", runtime="styio_task_scheduler", seq=640.0, par=160.0, total=50000.0, per=0.5)]
    runner.add_cpp_comparisons(results)
    comparisons = results[0]["comparisons"]
    assert set(comparisons.keys()) == {
        "sleep.sequential_ms",
        "sleep.parallel_ms",
        "sleep.speedup",
        "noop.total_us",
        "noop.per_task_us",
    }
    for comparison in comparisons.values():
        assert comparison["status"] == "missing_baseline"
        assert comparison["u"] is None
        assert comparison["baseline_identity"] == "cpp_stackless_coroutine"


def test_cpp_result_marked_baseline(runner):
    cpp = build_result(runner, runtime_key="cpp", runtime="cpp_stackless_coroutine", seq=640.0, par=160.0, total=50000.0, per=0.5)
    runner.add_cpp_comparisons([cpp])
    assert cpp["comparison"] == {
        "baseline_identity": "cpp_stackless_coroutine",
        "u": None,
        "p_value": None,
        "alpha": 0.05,
        "significant": None,
        "direction": None,
        "status": "baseline",
    }
    assert "comparisons" not in cpp


def test_cpp_comparisons_ok(runner):
    cpp = build_result(runner, runtime_key="cpp", runtime="cpp_stackless_coroutine", seq=640.0, par=160.0, total=50000.0, per=0.5)
    styio = build_result(runner, runtime_key="styio", runtime="styio_task_scheduler", seq=700.0, par=200.0, total=60000.0, per=0.6)
    runner.add_cpp_comparisons([cpp, styio])
    comparisons = styio["comparisons"]
    assert comparisons["sleep.parallel_ms"]["status"] == "ok"
    assert comparisons["sleep.parallel_ms"]["significant"] is True
    assert comparisons["sleep.parallel_ms"]["direction"] == "worse"  # 200 vs 160, lower better
    assert comparisons["sleep.speedup"]["direction"] == "worse"  # lower speedup is worse
    for comparison in comparisons.values():
        json.dumps(comparison)


# --- scales and repeats ---


def test_scale_resolution(runner):
    for scale_name, expected in (
        ("small", (2, 20, 1000, 2)),
        ("medium", (4, 160, 100000, 4)),
        ("large", (8, 160, 200000, 8)),
    ):
        args = runner.argparse.Namespace(
            styio_root=str(BENCHMARK_ROOT.parent / "styio"),
            case="baseline",
            scale=scale_name,
            tasks=None,
            sleep_ms=None,
            noop_tasks=None,
            workers=None,
            repeats=None,
            runtimes=None,
            required_runtimes=[],
        )
        runner.resolve_benchmark_args(args)
        assert (args.tasks, args.sleep_ms, args.noop_tasks, args.workers) == expected
        assert args.scale_name == scale_name


def test_explicit_flags_override_scale(runner):
    args = runner.argparse.Namespace(
        styio_root=str(BENCHMARK_ROOT.parent / "styio"),
        case="baseline",
        scale="small",
        tasks=9,
        sleep_ms=30,
        noop_tasks=None,
        workers=None,
        repeats=3,
        runtimes=None,
        required_runtimes=[],
    )
    runner.resolve_benchmark_args(args)
    assert args.tasks == 9
    assert args.sleep_ms == 30
    assert args.noop_tasks == 1000
    assert args.workers == 2
    assert args.repeats == 3


def test_baseline_default_repeats_ten(runner):
    args = runner.argparse.Namespace(
        styio_root=str(BENCHMARK_ROOT.parent / "styio"),
        case="baseline",
        scale=None,
        tasks=None,
        sleep_ms=None,
        noop_tasks=None,
        workers=None,
        repeats=None,
        runtimes=None,
        required_runtimes=[],
    )
    runner.resolve_benchmark_args(args)
    assert args.repeats == 10
    assert args.scale_name == "medium"


def test_smoke_defaults_preserved(runner):
    args = runner.argparse.Namespace(
        styio_root=str(BENCHMARK_ROOT.parent / "styio"),
        case="smoke",
        scale=None,
        tasks=None,
        sleep_ms=None,
        noop_tasks=None,
        workers=None,
        repeats=None,
        runtimes=None,
        required_runtimes=[],
    )
    runner.resolve_benchmark_args(args)
    assert args.repeats == 1
    assert (args.tasks, args.sleep_ms, args.noop_tasks, args.workers) == (2, 20, 1000, 2)
    assert args.scale_name is None


# --- report serialization ---


def test_csv_has_statistic_and_significance_rows(runner, tmp_path):
    cpp = build_result(runner, runtime_key="cpp", runtime="cpp_stackless_coroutine", seq=640.0, par=160.0, total=50000.0, per=0.5)
    styio = build_result(runner, runtime_key="styio", runtime="styio_task_scheduler", seq=700.0, par=200.0, total=60000.0, per=0.6)
    results = [cpp, styio]
    metadata = {
        "run_id": "test",
        "host": "synthetic",
        "case": "baseline",
        "case_description": "test",
        "framework": "pytest-compatible black-box runner",
        "interface": "subprocess",
        "runtimes": ["styio", "cpp"],
        "required_runtimes": [],
        "tasks": 4,
        "sleep_ms": 160,
        "noop_tasks": 100000,
        "workers": 4,
        "repeats": 10,
        "scale": "medium",
        "bootstrap_toolchains": False,
    }
    runner.write_report(tmp_path, metadata, results)
    rows = list(__import__("csv").DictReader((tmp_path / "benchmarks.csv").open(encoding="utf-8")))
    metric_names = {row["metric"] for row in rows}
    assert "statistics.parallel_ms.median" in metric_names
    assert "significance.parallel_ms.p_value" in metric_names
    cpp_significance = [
        row
        for row in rows
        if row["runtime"] == "cpp_stackless_coroutine"
        and row["metric"] == "significance.parallel_ms.status"
    ]
    assert cpp_significance and cpp_significance[0]["value"] == "baseline"
    legacy = [row for row in rows if row["workload"] == "sleep" and row["metric"] == "parallel_ms"]
    assert legacy, "legacy CSV rows must remain"


def test_markdown_has_statistics_section(runner, tmp_path):
    cpp = build_result(runner, runtime_key="cpp", runtime="cpp_stackless_coroutine", seq=640.0, par=160.0, total=50000.0, per=0.5)
    styio = build_result(runner, runtime_key="styio", runtime="styio_task_scheduler", seq=700.0, par=200.0, total=60000.0, per=0.6)
    results = [cpp, styio]
    metadata = {
        "run_id": "test",
        "host": "synthetic",
        "case": "baseline",
        "case_description": "test",
        "framework": "pytest-compatible black-box runner",
        "interface": "subprocess",
        "runtimes": ["styio", "cpp"],
        "required_runtimes": [],
        "tasks": 4,
        "sleep_ms": 160,
        "noop_tasks": 100000,
        "workers": 4,
        "repeats": 10,
        "scale": "medium",
        "bootstrap_toolchains": False,
    }
    runner.write_report(tmp_path, metadata, results)
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "## Statistics and C++ Significance" in summary
    assert "Sleep perf" in summary
    assert "Noop perf" in summary
    assert "C++ Stackless Parity" in summary
