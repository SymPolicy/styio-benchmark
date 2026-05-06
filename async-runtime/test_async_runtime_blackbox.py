from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
RUNNER = BENCHMARK_ROOT / "async-runtime" / "run-async-bench.py"


def styio_root_for_test() -> Path:
    raw = os.environ.get("STYIO_ROOT")
    root = Path(raw).expanduser() if raw else BENCHMARK_ROOT.parent / "styio"
    root = root.resolve()
    if not (root / "CMakeLists.txt").exists():
        pytest.skip(f"STYIO_ROOT is not a Styio source checkout: {root}")
    probe_cmake = root / "benchmark" / "CMakeLists.txt"
    if not probe_cmake.exists():
        pytest.skip(f"Styio checkout does not expose benchmark probe CMake file: {root}")
    cmake_lists = probe_cmake.read_text(encoding="utf-8", errors="replace")
    if "styio_task_scheduler_perf_test" not in cmake_lists:
        pytest.skip(f"Styio checkout does not expose styio_task_scheduler_perf_test: {root}")
    return root


@pytest.fixture(scope="session")
def async_runtime_smoke(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    styio_root = styio_root_for_test()
    out_dir = tmp_path_factory.mktemp("styio-async-runtime-smoke")
    proc = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--styio-root",
            str(styio_root),
            "--case",
            "smoke",
            "--runtime",
            "styio",
            "--runtime",
            "cpp",
            "--require-runtime",
            "styio",
            "--require-runtime",
            "cpp",
            "--out-dir",
            str(out_dir),
        ],
        cwd=BENCHMARK_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=900,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
    return {"out_dir": out_dir, "payload": payload}


def result_by_runtime(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {result["runtime_key"]: result for result in payload["results"]}


def test_async_runtime_report_contract(async_runtime_smoke: dict[str, Any]) -> None:
    payload = async_runtime_smoke["payload"]
    metadata = payload["metadata"]
    assert metadata["framework"] == "pytest-compatible black-box runner"
    assert metadata["interface"] == "subprocess"
    assert metadata["case"] == "smoke"
    assert metadata["runtimes"] == ["styio", "cpp"]
    assert metadata["required_runtimes"] == ["styio", "cpp"]


@pytest.mark.parametrize("runtime_key", ["styio", "cpp"])
def test_required_runtimes_are_available(async_runtime_smoke: dict[str, Any], runtime_key: str) -> None:
    payload = async_runtime_smoke["payload"]
    result = result_by_runtime(payload)[runtime_key]
    assert result["status"] == "ok"
    assert result["sample_count"] == payload["metadata"]["repeats"]
    assert result["workers"] == payload["metadata"]["workers"]
    assert result["sleep"]["tasks"] == payload["metadata"]["tasks"]
    assert result["sleep"]["sleep_ms"] == payload["metadata"]["sleep_ms"]
    assert result["sleep"]["parallel_ms"] > 0
    assert result["noop"]["tasks"] == payload["metadata"]["noop_tasks"]
    assert result["noop"]["per_task_us"] > 0


def test_relative_performance_contract(async_runtime_smoke: dict[str, Any]) -> None:
    results = result_by_runtime(async_runtime_smoke["payload"])
    for runtime_key in ("styio", "cpp"):
        relative = results[runtime_key]["relative_performance"]
        assert 0 < relative["sleep_speedup"] <= 1.01
        assert 0 < relative["noop_fanout"] <= 1.01
    assert "sleep_vs_cpp_stackless" in results["styio"]["relative_performance"]
    assert "noop_vs_cpp_stackless" in results["styio"]["relative_performance"]


def test_report_artifacts_are_written(async_runtime_smoke: dict[str, Any]) -> None:
    out_dir = async_runtime_smoke["out_dir"]
    for filename in ("metadata.json", "results.json", "benchmarks.csv", "summary.md"):
        path = out_dir / filename
        assert path.exists()
        assert path.stat().st_size > 0
    summary = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert "C++ Stackless Parity" in summary
    assert "Sleep perf" in summary
    assert "Noop perf" in summary
