from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "workloads" / "core"


def _load_compare_module():
    path = ROOT / "tools" / "core-benchmark-compare.py"
    spec = importlib.util.spec_from_file_location("core_benchmark_compare", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_core_manifest_is_self_contained() -> None:
    manifest = json.loads((CORE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "styio.core_benchmark_manifest.v1"
    assert len(manifest["workloads"]) == 3
    for workload in manifest["workloads"]:
        source = (ROOT / workload["source"]).resolve()
        assert ROOT in source.parents
        assert source.is_file()
        assert "styio-nightly" not in workload["source"]


def test_core_runner_emits_path_free_evidence(tmp_path: Path) -> None:
    fake_styio = tmp_path / "styio"
    fake_styio.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "name = pathlib.Path(sys.argv[2]).name\n"
        "print({'accumulate_sum.styio': '15', 'binary_search.styio': '2', "
        "'inner_product.styio': '300'}[name])\n",
        encoding="utf-8",
    )
    fake_styio.chmod(0o755)
    output = tmp_path / "results.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(CORE / "run-core.py"),
            "--repo-root",
            str(ROOT),
            "--styio",
            str(fake_styio),
            "--iterations",
            "1",
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == "styio.core_benchmark_result.v1"
    assert payload["manifest"] == "workloads/core/manifest.json"
    assert payload["repo_root"] == "styio-benchmark"
    assert payload["styio"] == "styio"
    assert len(payload["workloads"]) == 3
    rendered = output.read_text(encoding="utf-8")
    assert str(ROOT) not in rendered
    assert str(tmp_path) not in rendered


def test_curated_core_reports_have_public_fields_only() -> None:
    forbidden_keys = {"path", "command", "environment", "hostname", "username", "endpoint"}
    for report in (ROOT / "reports" / "core").glob("*.json"):
        payload = json.loads(report.read_text(encoding="utf-8"))
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                assert forbidden_keys.isdisjoint(value)
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
            elif isinstance(value, str):
                assert not value.startswith(("/Users/", "/home/"))
                assert not (len(value) > 2 and value[1:3] == ":\\")


def test_core_compare_preserves_counter_fields(tmp_path: Path) -> None:
    payload = {
        "schema": "styio.benchmark.v1",
        "samples": [
            {
                "phase": "route_cache",
                "label": "lookup",
                "duration_ns": 1,
                "route_cache_scan_count": 7,
                "route_cache_miss_count": 2,
            },
            {
                "phase": "ir_alloc",
                "label": "factory",
                "duration_ns": 2,
                "ir_node_count": 3,
                "ir_bytes_allocated": 64,
            },
        ],
    }
    report = tmp_path / "result.json"
    report.write_text(json.dumps(payload), encoding="utf-8")
    loaded = _load_compare_module().load_results(str(report))["samples"]
    assert loaded["route_cache/lookup"]["route_cache_scan_count"] == 7
    assert loaded["route_cache/lookup"]["route_cache_miss_count"] == 2
    assert loaded["ir_alloc/factory"]["ir_node_count"] == 3
    assert loaded["ir_alloc/factory"]["ir_bytes_allocated"] == 64
