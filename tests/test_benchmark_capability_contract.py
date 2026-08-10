from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "benchmark_golden_gate", BENCHMARK_ROOT / "scripts" / "benchmark-golden-gate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    return _load_gate()


def _gate_check(check, gate) -> None:
    try:
        check()
    except SystemExit as exc:
        pytest.fail(f"golden gate check failed with exit {exc.code}")


def test_production_dependencies_remain_empty() -> None:
    text = (BENCHMARK_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in text
    assert "pytest" not in text.split("[project.optional-dependencies]", 1)[0]


def test_test_discovery_covers_all_areas() -> None:
    text = (BENCHMARK_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for directory in ("async-runtime", "native-cpp", "tools", "tests"):
        assert f'"{directory}"' in text


def test_required_capability_files_exist() -> None:
    required = [
        "tools/benchmark-env-check.py",
        "tools/benchmark-compare.py",
        "tools/test_benchmark_tools.py",
        "async-runtime/test_async_runtime_statistics.py",
        "native-cpp/test_native_cpp_bench.py",
        "native-cpp/README.md",
        "docs/specs/GOLDEN-STANDARD-TEST-SUITE.md",
        "tests/test_benchmark_capability_contract.py",
    ]
    for relative in required:
        path = BENCHMARK_ROOT / relative
        assert path.is_file(), f"missing required file {relative}"
        assert path.stat().st_size > 0, f"empty required file {relative}"


@pytest.mark.parametrize("relative", ["tools/benchmark-env-check.py", "tools/benchmark-compare.py"])
def test_new_cli_tools_are_executable(relative: str) -> None:
    assert os.access(BENCHMARK_ROOT / relative, os.X_OK), f"CLI tool is not executable: {relative}"


def test_capability_fixtures_present_and_valid_schema(gate) -> None:
    fixture_dir = BENCHMARK_ROOT / "tests" / "fixtures" / "benchmark-capabilities"
    names = ["async-baseline.json", "async-candidate.json", "native-baseline.json", "native-candidate.json"]
    for name in names:
        path = fixture_dir / name
        assert path.is_file(), f"missing fixture {name}"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload.get("metadata"), dict)
        results = payload.get("results")
        assert isinstance(results, list) and results
        for result in results:
            statistics = result.get("statistics")
            if statistics is not None:
                assert isinstance(statistics, dict)
            comparisons = result.get("comparisons")
            if comparisons is not None:
                assert isinstance(comparisons, dict) and comparisons
    _gate_check(gate.check_capability_fixtures, gate)


def test_historical_report_fixtures_remain_readable(gate) -> None:
    _gate_check(gate.check_report_fixtures, gate)


def test_report_gate_accepts_embedded_metadata(gate, tmp_path) -> None:
    (tmp_path / "benchmarks.csv").write_text("metric,value\nelapsed_s,1\n", encoding="utf-8")
    (tmp_path / "summary.md").write_text("# Synthetic report\n", encoding="utf-8")
    (tmp_path / "results.json").write_text(
        json.dumps({"metadata": {"fixture": "synthetic"}, "results": []}),
        encoding="utf-8",
    )
    gate.check_report_dir(tmp_path)


def test_golden_gate_dependency_and_workflow_checks(gate) -> None:
    _gate_check(gate.check_dependencies, gate)
    _gate_check(gate.check_workflow, gate)


def test_golden_gate_sanitizes_local_diagnostic_paths(gate) -> None:
    sensitive = str(gate.ROOT / "private-diagnostic")
    sanitized = gate.sanitize_diagnostic(f"failure at {sensitive}")
    assert sensitive not in sanitized
    assert "<repo>" in sanitized


def test_workflow_has_only_contract_jobs_in_order() -> None:
    text = (BENCHMARK_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "test-smoke:" in text
    assert "test-golden-standard:" in text
    smoke_pos = text.index("test-smoke:")
    golden_pos = text.index("test-golden-standard:")
    assert golden_pos > smoke_pos
    assert "needs: [test-smoke]" in text
    assert "compileall" in text
    for pattern in ("run-async-bench.py", "run-native-cpp-bench.py", "--case baseline", "--case stress"):
        assert pattern not in text, f"workflow must not run wall-clock benchmarks, found {pattern!r}"
    assert "benchmark-golden-gate.py" in text


def test_documentation_is_synchronized() -> None:
    readme = (BENCHMARK_ROOT / "README.md").read_text(encoding="utf-8")
    for marker in (
        "benchmark-env-check.py",
        "benchmark-compare.py",
        "bootstrap 95% confidence interval",
        "Mann-Whitney U",
        "small", "medium", "large",
        "ten repeats",
        "never executes a real benchmark job",
    ):
        assert marker in readme, f"root README missing marker {marker!r}"
    async_readme = (BENCHMARK_ROOT / "async-runtime" / "README.md").read_text(encoding="utf-8")
    for marker in ("--scale", "bootstrap", "Mann-Whitney", "smoke"):
        assert marker in async_readme, f"async README missing marker {marker!r}"
    native_readme = (BENCHMARK_ROOT / "native-cpp" / "README.md").read_text(encoding="utf-8")
    for marker in ("--scale", "bootstrap", "Mann-Whitney", "native_cpp"):
        assert marker in native_readme, f"native README missing marker {marker!r}"
    coverage = (BENCHMARK_ROOT / "docs" / "COVERAGE-MATRIX.md").read_text(encoding="utf-8")
    for marker in ("benchmark-env-check.py", "benchmark-compare.py", "benchmark-golden-gate.py", "Mann-Whitney"):
        assert marker in coverage, f"coverage matrix missing marker {marker!r}"
    routes = (BENCHMARK_ROOT / "docs" / "IN_TREE_BENCHMARKS.md").read_text(encoding="utf-8")
    for marker in ("benchmark-env-check.py", "benchmark-compare.py", "benchmark-golden-gate.py", "bootstrap"):
        assert marker in routes, f"route guide missing marker {marker!r}"
    spec = (BENCHMARK_ROOT / "docs" / "specs" / "GOLDEN-STANDARD-TEST-SUITE.md").read_text(encoding="utf-8")
    for marker in ("benchmark-golden-gate.py", "capability fixtures", "wall-clock"):
        assert marker in spec, f"golden-standard spec missing marker {marker!r}"


def test_new_sources_are_byte_compilable() -> None:
    sources = [
        "tools/benchmark-env-check.py",
        "tools/benchmark-compare.py",
        "tools/test_benchmark_tools.py",
        "async-runtime/test_async_runtime_statistics.py",
        "native-cpp/test_native_cpp_bench.py",
        "scripts/benchmark-golden-gate.py",
        "tests/test_benchmark_capability_contract.py",
    ]
    for relative in sources:
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", str(BENCHMARK_ROOT / relative)],
            cwd=BENCHMARK_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert proc.returncode == 0, f"{relative} failed byte-compile:\n{proc.stdout}"


def test_comparator_cli_smoke_on_fixtures() -> None:
    compare = BENCHMARK_ROOT / "tools" / "benchmark-compare.py"
    baseline = BENCHMARK_ROOT / "tests" / "fixtures" / "benchmark-capabilities" / "async-baseline.json"
    candidate = BENCHMARK_ROOT / "tests" / "fixtures" / "benchmark-capabilities" / "async-candidate.json"
    proc = subprocess.run(
        [sys.executable, str(compare), "--baseline", str(baseline), "--candidate", str(candidate)],
        cwd=BENCHMARK_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    statuses = {item["status"] for item in payload["comparisons"]}
    assert "regression" in statuses
    assert "improvement" in statuses
    forbidden = ("/Users/", "/home/", "STYIO_ROOT", "hostname")
    for item in forbidden:
        assert item not in proc.stdout, f"comparator output leaked {item!r}"
