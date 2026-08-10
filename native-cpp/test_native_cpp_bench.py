from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
RUNNER = BENCHMARK_ROOT / "native-cpp" / "run-native-cpp-bench.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("native_cpp_bench", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["native_cpp_bench"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner():
    return load_runner()


def make_record(runner, *, case="stdin_echo", route="full-cli", implementation="styio", language="Styio", samples_s, input_bytes=100000 * 48):
    return runner.result_record(
        case=case,
        route=route,
        implementation=implementation,
        language=language,
        command=["synthetic"],
        samples_s=samples_s,
        input_bytes=input_bytes,
        line_count=100000,
        line_bytes=48,
        repeats=len(samples_s),
        warmups=1,
    )


# --- scales and defaults ---


def test_scale_presets(runner):
    assert runner.SCALE_LINE_COUNTS == {"small": 10000, "medium": 100000, "large": 1000000}


def test_parse_args_defaults(runner, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run-native-cpp-bench.py", "--styio-root", "/nonexistent"])
    args = runner.parse_args()
    assert args.scale == "medium"
    assert args.repeats == 10
    assert args.line_count is None
    resolved = args.line_count if args.line_count is not None else runner.SCALE_LINE_COUNTS[args.scale]
    assert resolved == 100_000


def test_scale_resolution_all_sizes(runner, monkeypatch):
    for scale_name, expected in (("small", 10000), ("medium", 100000), ("large", 1000000)):
        monkeypatch.setattr(sys, "argv", ["run-native-cpp-bench.py", "--styio-root", "/nonexistent", "--scale", scale_name])
        args = runner.parse_args()
        resolved = args.line_count if args.line_count is not None else runner.SCALE_LINE_COUNTS[args.scale]
        assert resolved == expected


def test_explicit_line_count_overrides_scale(runner, monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["run-native-cpp-bench.py", "--styio-root", "/nonexistent", "--scale", "large", "--line-count", "12345"]
    )
    args = runner.parse_args()
    resolved = args.line_count if args.line_count is not None else runner.SCALE_LINE_COUNTS[args.scale]
    assert resolved == 12345


# --- statistics ---


def test_statistic_object_schema_and_determinism(runner):
    values = [0.10, 0.11, 0.12, 0.09, 0.105]
    stat = runner.statistic_object(values)
    assert set(stat.keys()) == {"sample_count", "median", "ci95_low", "ci95_high", "cv", "quality"}
    assert stat["sample_count"] == 5
    assert stat["quality"] == "ok"
    assert runner.statistic_object(values) == stat
    assert min(values) <= stat["ci95_low"] <= stat["ci95_high"] <= max(values)
    json.dumps(stat, allow_nan=False)


def test_statistic_one_sample_degenerate(runner):
    stat = runner.statistic_object([3.5])
    assert stat["median"] == 3.5
    assert stat["ci95_low"] == stat["ci95_high"] == 3.5
    assert stat["cv"] is None
    assert stat["quality"] == "insufficient_sample"


def test_cv_zero_mean(runner):
    assert runner.coefficient_of_variation([0.0, 0.0]) is None
    assert runner.coefficient_of_variation([1.0]) is None


def test_throughput_samples(runner):
    samples = runner.throughput_samples([0.5, 1.0], input_bytes=1024 * 1024)
    assert samples == pytest.approx([2.0, 1.0])


# --- Mann-Whitney U ---


def test_mwu_fully_separated_known_anchor(runner):
    result = runner.mann_whitney_u([4.0, 5.0, 6.0], [1.0, 2.0, 3.0])
    assert result["status"] == "ok"
    assert result["u"] == 0.0
    assert 0.05 < result["p_value"] < 0.15


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
    assert result["u"] == 4.5
    assert result["p_value"] == 1.0


def test_mwu_constant_against_varying_is_valid(runner):
    result = runner.mann_whitney_u([5.0, 5.0, 5.0, 5.0], [1.0, 2.0, 3.0, 4.0])
    assert result["status"] == "ok"
    assert result["u"] == 0.0


def test_mwu_all_tied_values_are_insufficient(runner):
    result = runner.mann_whitney_u([5.0] * 4, [5.0] * 4)
    assert result["status"] == "insufficient_sample"
    assert result["u"] is None


def test_comparison_direction_elapsed(runner):
    assert runner.comparison_direction(0.5, 1.0, lower_is_better=True) == "better"
    assert runner.comparison_direction(1.0, 0.5, lower_is_better=True) == "worse"


# --- records and comparisons ---


def test_result_record_preserves_legacy_fields(runner):
    record = make_record(runner, samples_s=[0.5, 0.6, 0.55])
    for field in ("case", "route", "implementation", "mode", "status", "command", "input_bytes", "input_mib", "line_count", "line_bytes", "repeats", "warmups", "samples_s", "median_s", "min_s", "p95_s", "throughput_mib_s"):
        assert field in record
    assert record["status"] == "pass"
    assert record["median_s"] == 0.55
    statistics_payload = record["statistics"]
    assert set(statistics_payload.keys()) == {"elapsed_s", "throughput_mib_s"}


def test_comparisons_ok_and_baseline_marker(runner):
    native = make_record(runner, implementation="native_cpp", language="C++20", samples_s=[0.5 + i * 0.01 for i in range(10)])
    styio = make_record(runner, implementation="styio", samples_s=[0.7 + i * 0.01 for i in range(10)])
    runner.add_native_comparisons([native, styio])
    assert native["comparison"] == {
        "baseline_identity": "native_cpp",
        "u": None,
        "p_value": None,
        "alpha": 0.05,
        "significant": None,
        "direction": None,
        "status": "baseline",
    }
    comparison = styio["comparison"]
    assert comparison["status"] == "ok"
    assert comparison["significant"] is True
    assert comparison["direction"] == "worse"  # slower elapsed, lower better
    assert comparison["baseline_identity"] == "native_cpp"
    json.dumps(comparison)


def test_comparison_missing_baseline(runner):
    styio = make_record(runner, implementation="styio", samples_s=[0.5 + i * 0.01 for i in range(10)])
    runner.add_native_comparisons([styio])
    assert styio["comparison"]["status"] == "missing_baseline"
    assert styio["comparison"]["u"] is None


def test_unsupported_record_semantics(runner):
    record = runner.unsupported_record("stdin_echo", "cached-jit", "styio", "no reusable compiled entrypoint")
    assert record["status"] == "unsupported"
    assert record["reason"]
    assert record["relative_x"] == ""
    runner.add_native_comparisons([record])
    assert record["comparison"] == {
        "baseline_identity": "native_cpp",
        "u": None,
        "p_value": None,
        "alpha": 0.05,
        "significant": None,
        "direction": None,
        "status": "not_comparable",
    }
    assert record["relative_x"] == ""


# --- serialization ---


def test_csv_columns_additive(runner, tmp_path):
    native = make_record(runner, implementation="native_cpp", samples_s=[0.5 + i * 0.01 for i in range(10)])
    styio = make_record(runner, implementation="styio", samples_s=[0.7 + i * 0.01 for i in range(10)])
    runner.add_native_comparisons([native, styio])
    path = tmp_path / "benchmarks.csv"
    runner.write_csv(path, [native, styio])
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["median_s"] != ""
    assert rows[0]["elapsed_ci95_low_s"] != ""
    assert rows[0]["throughput_cv"] != ""
    assert rows[1]["significance_u"] != ""
    assert rows[1]["significance_status"] == "ok"
    assert rows[0]["significance_status"] == "baseline"
    assert rows[0]["significance_alpha"] == "0.05"
    assert rows[0]["significance_baseline_identity"] == "native_cpp"
    assert list(rows[0]).index("command") < list(rows[0]).index("elapsed_ci95_low_s")


def test_summary_columns_additive(runner, tmp_path):
    native = make_record(runner, implementation="native_cpp", samples_s=[0.5 + i * 0.01 for i in range(10)])
    styio = make_record(runner, implementation="styio", samples_s=[0.7 + i * 0.01 for i in range(10)])
    unsupported = runner.unsupported_record("stdin_echo", "cached-jit", "styio", "no reusable compiled entrypoint")
    records = [native, styio, unsupported]
    runner.add_native_comparisons(records)
    runner.normalize_relative(records)
    metadata = {
        "started_at_utc": "2026-08-11T00:00:00Z",
        "hostname": "synthetic",
        "uname": "synthetic",
        "styio_exe": "styio",
        "cxx": "clang++",
        "routes": ["full-cli", "cached-jit"],
        "scale": "medium",
    }
    path = tmp_path / "summary.md"
    runner.write_summary(path, metadata, records)
    text = path.read_text(encoding="utf-8")
    assert "95% CI s" in text
    assert "CV" in text
    assert "Sig" in text
    assert "Direction" in text
    assert "baseline" in text
    assert "not_comparable" in text
    assert "higher throughput" in text.lower()
