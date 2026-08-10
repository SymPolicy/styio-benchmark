from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = BENCHMARK_ROOT / "tests" / "fixtures" / "benchmark-capabilities"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def compare():
    return _load("benchmark_compare", BENCHMARK_ROOT / "tools" / "benchmark-compare.py")


@pytest.fixture(scope="module")
def env_check():
    return _load("benchmark_env_check", BENCHMARK_ROOT / "tools" / "benchmark-env-check.py")


def _walk(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    else:
        yield path, value


def _assert_privacy_safe(text: str) -> None:
    forbidden = ("/Users/", "/home/", "STYIO_ROOT", "hostname")
    for item in forbidden:
        assert item not in text, f"output leaked {item!r}"


# --- statistics contract ---


def test_mann_whitney_anchors(compare):
    small = compare.mann_whitney_u([4, 5, 6], [1, 2, 3], 0.05)
    assert small["u"] == 0.0
    assert small["status"] == "ok"
    assert 0.05 < small["p_value"] < 0.15
    assert small["significant"] is False
    mixed = compare.mann_whitney_u([5, 5, 6, 6], [1, 2, 3, 4], 0.05)
    assert mixed["u"] == 0.0
    assert mixed["status"] == "ok"
    assert 0.02 < mixed["p_value"] < 0.04
    assert mixed["significant"] is True
    overlapping = compare.mann_whitney_u([1, 3, 5], [2, 4, 6], 0.05)
    assert overlapping["u"] == 3.0
    assert 0.65 < overlapping["p_value"] < 0.67
    equal = compare.mann_whitney_u([1, 2, 3], [1, 2, 3], 0.05)
    assert equal["u"] == 4.5
    assert equal["p_value"] == 1.0


def test_mann_whitney_insufficient_sample(compare):
    result = compare.mann_whitney_u([1.0], [1.0, 2.0, 3.0], 0.05)
    assert result["status"] == "insufficient_sample"
    assert result["u"] is None
    constant_side = compare.mann_whitney_u([5.0] * 5, [1.0, 2.0, 3.0, 4.0, 6.0], 0.05)
    assert constant_side["status"] == "ok"
    all_tied = compare.mann_whitney_u([5.0] * 5, [5.0] * 5, 0.05)
    assert all_tied["status"] == "insufficient_sample"


def test_regression_percent_direction_aware(compare):
    assert compare.regression_percent(110.0, 100.0, True) == pytest.approx(10.0)
    assert compare.regression_percent(90.0, 100.0, True) == pytest.approx(-10.0)
    assert compare.regression_percent(90.0, 100.0, False) == pytest.approx(10.0)
    assert compare.regression_percent(110.0, 100.0, False) == pytest.approx(-10.0)
    assert compare.regression_percent(100.0, 0.0, True) is None


def test_compare_pair_classification(compare):
    worse = compare.compare_pair(("k", "a", "b"), "elapsed_s", [100 + i for i in range(10)], [120 + i for i in range(10)], alpha=0.05, threshold=5.0, lower_is_better=True)
    assert worse["status"] == "regression"
    assert worse["regression_percent"] > 5.0
    assert worse["significant"] is True
    better = compare.compare_pair(("k", "a", "b"), "elapsed_s", [120 + i for i in range(10)], [100 + i for i in range(10)], alpha=0.05, threshold=5.0, lower_is_better=True)
    assert better["status"] == "improvement"
    assert better["regression_percent"] < -5.0
    tiny = compare.compare_pair(("k", "a", "b"), "elapsed_s", [100 + i for i in range(10)], [101 + i for i in range(10)], alpha=0.05, threshold=5.0, lower_is_better=True)
    assert tiny["status"] == "inconclusive"
    few = compare.compare_pair(("k", "a", "b"), "elapsed_s", [1.0], [2.0, 3.0, 4.0, 5.0], alpha=0.05, threshold=5.0, lower_is_better=True)
    assert few["status"] == "insufficient_sample"
    zero_baseline = compare.compare_pair(
        ("k", "a", "b"),
        "elapsed_s",
        [0.0, 0.0, 0.0],
        [1.0, 2.0, 3.0],
        alpha=0.05,
        threshold=5.0,
        lower_is_better=True,
    )
    assert zero_baseline["status"] == "incompatible_input"


def test_fixture_comparison_evidence_matches_raw_samples(compare):
    lower_by_async_metric = {
        f"{workload}.{metric}": lower_is_better
        for workload, metric, lower_is_better in compare.ASYNC_METRICS
    }
    for name in ("async-baseline.json", "async-candidate.json"):
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        cpp = next(result for result in payload["results"] if result.get("runtime_key") == "cpp")
        for result in payload["results"]:
            if result.get("runtime_key") == "cpp":
                continue
            for key, embedded in result["comparisons"].items():
                workload, metric = key.split(".", 1)
                candidate = compare.async_sample_values(result, workload, metric)
                baseline = compare.async_sample_values(cpp, workload, metric)
                expected = compare.mann_whitney_u(candidate, baseline, 0.05)
                assert embedded["status"] == expected["status"]
                assert embedded["u"] == expected["u"]
                if expected["p_value"] is None:
                    assert embedded["p_value"] is None
                else:
                    assert embedded["p_value"] == pytest.approx(expected["p_value"])
                assert embedded["significant"] == expected["significant"]
                if expected["status"] == "ok":
                    lower_is_better = lower_by_async_metric[key]
                    candidate_median = compare.median(candidate)
                    baseline_median = compare.median(baseline)
                    if candidate_median == baseline_median:
                        direction = "similar"
                    elif (candidate_median < baseline_median) == lower_is_better:
                        direction = "better"
                    else:
                        direction = "worse"
                    assert embedded["direction"] == direction
                else:
                    assert embedded["direction"] is None

    for name in ("native-baseline.json", "native-candidate.json"):
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        groups: dict[tuple[str, str], list[dict]] = {}
        for result in payload["results"]:
            groups.setdefault((result["case"], result["route"]), []).append(result)
        for group in groups.values():
            native = next(
                (
                    result
                    for result in group
                    if result.get("status") == "pass" and result.get("implementation") == "native_cpp"
                ),
                None,
            )
            if native is None:
                continue
            for result in group:
                if result.get("status") != "pass" or result.get("implementation") == "native_cpp":
                    continue
                expected = compare.mann_whitney_u(result["samples_s"], native["samples_s"], 0.05)
                embedded = result["comparison"]
                assert embedded["status"] == expected["status"]
                assert embedded["u"] == expected["u"]
                assert embedded["p_value"] == pytest.approx(expected["p_value"])
                assert embedded["significant"] == expected["significant"]


# --- comparator CLI ---


def test_comparator_async_fixtures_classify(compare):
    proc = subprocess.run(
        [sys.executable, str(BENCHMARK_ROOT / "tools" / "benchmark-compare.py"),
         "--baseline", str(FIXTURES / "async-baseline.json"),
         "--candidate", str(FIXTURES / "async-candidate.json")],
        cwd=BENCHMARK_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["shape"] == "async"
    assert payload["summary"]["regression"] >= 1
    assert payload["summary"]["improvement"] >= 1
    statuses = {item["status"] for item in payload["comparisons"]}
    assert {"regression", "improvement"} <= statuses
    for item in payload["comparisons"]:
        assert isinstance(item["key"], list) and len(item["key"]) == 3
        for key in item["key"]:
            assert isinstance(key, str) and key
    _assert_privacy_safe(proc.stdout)


def test_comparator_native_fixtures_classify(compare):
    proc = subprocess.run(
        [sys.executable, str(BENCHMARK_ROOT / "tools" / "benchmark-compare.py"),
         "--baseline", str(FIXTURES / "native-baseline.json"),
         "--candidate", str(FIXTURES / "native-candidate.json")],
        cwd=BENCHMARK_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["shape"] == "native"
    assert payload["summary"]["regression"] >= 1
    assert payload["summary"]["improvement"] >= 1
    _assert_privacy_safe(proc.stdout)


def test_comparator_fail_on_regression_exit_code():
    proc = subprocess.run(
        [sys.executable, str(BENCHMARK_ROOT / "tools" / "benchmark-compare.py"),
         "--baseline", str(FIXTURES / "async-baseline.json"),
         "--candidate", str(FIXTURES / "async-candidate.json"),
         "--fail-on-regression"],
        cwd=BENCHMARK_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert proc.returncode == 1
    json.loads(proc.stdout)


def test_comparator_invalid_input_exit_codes(tmp_path):
    missing_path = tmp_path / "nope.json"
    missing = subprocess.run(
        [sys.executable, str(BENCHMARK_ROOT / "tools" / "benchmark-compare.py"),
         "--baseline", str(missing_path),
         "--candidate", str(FIXTURES / "async-baseline.json")],
        cwd=BENCHMARK_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert missing.returncode == 2
    assert str(missing_path) not in missing.stderr
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    malformed = subprocess.run(
        [sys.executable, str(BENCHMARK_ROOT / "tools" / "benchmark-compare.py"),
         "--baseline", str(bad),
         "--candidate", str(FIXTURES / "async-baseline.json")],
        cwd=BENCHMARK_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert malformed.returncode == 2
    alpha = subprocess.run(
        [sys.executable, str(BENCHMARK_ROOT / "tools" / "benchmark-compare.py"),
         "--baseline", str(FIXTURES / "async-baseline.json"),
         "--candidate", str(FIXTURES / "async-candidate.json"),
         "--alpha", "1.5"],
        cwd=BENCHMARK_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert alpha.returncode == 2
    threshold = subprocess.run(
        [sys.executable, str(BENCHMARK_ROOT / "tools" / "benchmark-compare.py"),
         "--baseline", str(FIXTURES / "async-baseline.json"),
         "--candidate", str(FIXTURES / "async-candidate.json"),
         "--practical-threshold", "nan"],
        cwd=BENCHMARK_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert threshold.returncode == 2


def test_comparator_rejects_incompatible_report_shapes():
    proc = subprocess.run(
        [sys.executable, str(BENCHMARK_ROOT / "tools" / "benchmark-compare.py"),
         "--baseline", str(FIXTURES / "async-baseline.json"),
         "--candidate", str(FIXTURES / "native-candidate.json")],
        cwd=BENCHMARK_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert proc.returncode == 2
    assert not proc.stdout
    _assert_privacy_safe(proc.stderr)


def test_comparator_optional_outputs(tmp_path):
    out_json = tmp_path / "report.json"
    out_md = tmp_path / "report.md"
    proc = subprocess.run(
        [sys.executable, str(BENCHMARK_ROOT / "tools" / "benchmark-compare.py"),
         "--baseline", str(FIXTURES / "async-baseline.json"),
         "--candidate", str(FIXTURES / "async-candidate.json"),
         "--out-json", str(out_json), "--out-md", str(out_md)],
        cwd=BENCHMARK_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert proc.returncode == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))["shape"] == "async"
    assert "Benchmark comparison" in out_md.read_text(encoding="utf-8")


def test_comparator_refuses_to_overwrite_an_input(tmp_path):
    baseline = tmp_path / "baseline.json"
    original = (FIXTURES / "async-baseline.json").read_text(encoding="utf-8")
    baseline.write_text(original, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(BENCHMARK_ROOT / "tools" / "benchmark-compare.py"),
         "--baseline", str(baseline),
         "--candidate", str(FIXTURES / "async-candidate.json"),
         "--out-json", str(baseline)],
        cwd=BENCHMARK_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert proc.returncode == 2
    assert baseline.read_text(encoding="utf-8") == original
    assert str(baseline) not in proc.stderr


def test_comparator_report_is_deterministic(compare):
    baseline = json.loads((FIXTURES / "async-baseline.json").read_text(encoding="utf-8"))
    candidate = json.loads((FIXTURES / "async-candidate.json").read_text(encoding="utf-8"))
    kwargs = {
        "shape": "async",
        "baseline_label": "baseline",
        "candidate_label": "candidate",
        "alpha": 0.05,
        "threshold": 5.0,
    }
    first = compare.build_report(baseline, candidate, **kwargs)
    second = compare.build_report(baseline, candidate, **kwargs)
    assert "generated_at" not in first
    assert first == second


def test_comparator_markdown_escapes_user_labels_and_metric_keys(compare):
    baseline = json.loads((FIXTURES / "async-baseline.json").read_text(encoding="utf-8"))
    report = compare.build_report(
        baseline,
        baseline,
        shape="async",
        baseline_label="base|line",
        candidate_label="candidate`label\ncontinued",
        alpha=0.05,
        threshold=5.0,
    )
    report["comparisons"][0]["key"][0] = "runtime|key"
    markdown = compare.render_markdown(report)
    assert "base\\|line" in markdown
    assert "candidate\\`label continued" in markdown
    assert "runtime\\|key" in markdown


def test_comparator_missing_sides_reported(compare, tmp_path):
    baseline = json.loads((FIXTURES / "async-baseline.json").read_text(encoding="utf-8"))
    trimmed = {"metadata": baseline["metadata"], "results": [result for result in baseline["results"] if result.get("runtime_key") != "goroutine"]}
    path = tmp_path / "trimmed.json"
    path.write_text(json.dumps(trimmed), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(BENCHMARK_ROOT / "tools" / "benchmark-compare.py"),
         "--baseline", str(path),
         "--candidate", str(FIXTURES / "async-candidate.json")],
        cwd=BENCHMARK_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert any("goroutine" in key for key in payload["missing"]["candidate_only"])
    assert payload["summary"]["incompatible_input"] > 0
    assert any(item["status"] == "incompatible_input" for item in payload["comparisons"])


# --- environment check ---


def test_env_check_json_schema(env_check, capsys):
    payload = env_check.build_payload(env_check.run_checks())
    assert payload["version"] == "1"
    assert payload["generated_at"].endswith("Z")
    assert set(payload["summary"]) == {"pass", "warn", "unavailable"}
    ids = [check["id"] for check in payload["checks"]]
    assert set(ids) == {"platform_supported", "logical_cpus", "cpu_affinity", "background_load", "cpu_governor", "turbo_boost", "power_source"}
    for check in payload["checks"]:
        assert check["status"] in ("pass", "warn", "unavailable")
        assert isinstance(check["observed"], str)
        assert check["recommendation"]
    assert env_check.main(["--format", "json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["version"] == "1"
    _assert_privacy_safe(json.dumps(printed))


def test_env_check_fake_probes_strict_exit(env_check, capsys, monkeypatch):
    fake = {
        "alpha": {"label": "Alpha", "probe": lambda: ("pass", "ok", "fine")},
        "beta": {"label": "Beta", "probe": lambda: ("warn", "busy", "stop other work")},
        "gamma": {"label": "Gamma", "probe": lambda: ("unavailable", "unknown", "cannot tell")},
    }
    monkeypatch.setattr(env_check, "CHECK_PROBES", fake)
    assert env_check.main(["--format", "json"]) == 0
    advisory = json.loads(capsys.readouterr().out)
    assert advisory["summary"] == {"pass": 1, "warn": 1, "unavailable": 1}
    assert env_check.main(["--format", "json", "--strict"]) == 1
    strict = json.loads(capsys.readouterr().out)
    assert strict["summary"]["warn"] == 1
    text_result = env_check.main(["--format", "text"])
    assert text_result == 0
    text = capsys.readouterr().out
    assert "Alpha" in text and "Beta" in text


def test_env_check_probe_failure_degrades(env_check):
    def broken():
        raise RuntimeError("boom")

    checks = env_check.run_checks({"p": {"label": "P", "probe": broken}})
    assert checks[0]["status"] == "unavailable"
    assert checks[0]["observed"] == "probe failed"


def test_env_check_accepts_any_online_mains_supply(env_check, monkeypatch):
    monkeypatch.setattr(env_check.sys, "platform", "linux")
    monkeypatch.setattr(env_check.os.path, "isdir", lambda path: path == "/sys/class/power_supply")
    monkeypatch.setattr(env_check.os, "listdir", lambda path: ["AC0", "AC1"])
    values = {
        "/sys/class/power_supply/AC0/type": "Mains",
        "/sys/class/power_supply/AC0/online": "0",
        "/sys/class/power_supply/AC1/type": "Mains",
        "/sys/class/power_supply/AC1/online": "1",
    }
    monkeypatch.setattr(env_check, "_read_text_limited", values.get)
    assert env_check.probe_power_source()[0:2] == ("pass", "AC power")


def test_env_check_invalid_argument_exit(env_check):
    with pytest.raises(SystemExit) as exc:
        env_check.main(["--format", "nope"])
    assert exc.value.code == 2
