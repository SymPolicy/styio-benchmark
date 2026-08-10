"""Contract tests for the non-gameable parity runner."""

from __future__ import annotations

import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "workloads" / "parity-v1" / "contract.json"


def _module():
    spec = importlib.util.spec_from_file_location("parity_gate_test_module", ROOT / "tools" / "parity_gate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GATE = _module()


def test_catalog_check_matches_contract_digest() -> None:
    catalog, path = GATE.load_catalog(CATALOG)
    assert catalog["catalog_id"] == "parity-v1"
    assert GATE.HEX_DIGEST.fullmatch(GATE.contract_digest(catalog))
    assert path == CATALOG.resolve()


def test_statistics_keep_all_samples_and_pair_logs() -> None:
    styio = [1.0, 2.0, 4.0]
    cpp = [1.0, 1.0, 2.0]
    assert GATE.median(styio) == 2.0
    assert GATE.sample_cv_pct(styio) > 0
    logs = GATE.paired_log_ratios(styio, cpp)
    assert len(logs) == 3
    summary = GATE._ratio_dimension(styio, cpp)
    assert summary["styio_samples"] == styio
    assert summary["cpp_samples"] == cpp
    assert summary["geomean_ratio"] == pytest.approx(4.0 ** (1 / 3), rel=1e-12)


def test_throughput_uses_reciprocal_ratio() -> None:
    summary = GATE._ratio_dimension([10.0, 10.0], [5.0, 5.0], reciprocal=True)
    assert summary["median_ratio"] == pytest.approx(0.5)


def test_short_cell_calibration_uses_one_equal_batch_and_normalizes() -> None:
    # The faster side determines the minimum equal work count; the slower side
    # receives that same count rather than being measured with less work.
    assert GATE.calibrate_batch_count([0.001, 0.002], minimum_duration_s=0.01) == 10
    assert GATE.calibrate_batch_count([0.1, 0.2], minimum_duration_s=0.01) == 1
    assert GATE.normalize_batched_elapsed(0.02, 10) == pytest.approx(0.002)


def test_phase_round_robin_keeps_equal_work_and_balances_order() -> None:
    batch_count = 6
    repetitions = 5
    for sample_index in range(repetitions):
        orders = [GATE._phase_round_robin_order(block, sample_index) for block in range(batch_count)]
        assert all(sorted(order) == ["cpp", "styio"] for order in orders)
        assert sum(order.index("styio") == 0 for order in orders) == batch_count // 2
        assert sum(order.index("cpp") == 0 for order in orders) == batch_count // 2
    # Each retained sample receives exactly one invocation per implementation
    # in every block; no implementation can silently receive less work.
    assert sum(1 for block in range(batch_count) for _ in GATE._phase_round_robin_order(block, 0)) == batch_count * 2


def test_native_run_round_robin_keeps_each_sample_equal_and_interleaved() -> None:
    batch_count = 8
    repetitions = 3
    for sample_index in range(repetitions):
        orders = [GATE._native_run_round_robin_order(block, sample_index) for block in range(batch_count)]
        assert all(sorted(order) == ["cpp", "styio"] for order in orders)
        assert sum(order[0] == "styio" for order in orders) == batch_count // 2
        assert sum(order[0] == "cpp" for order in orders) == batch_count // 2
    source = inspect.getsource(GATE._measure_cell_once)
    assert "native_run_blocked" in source
    assert "_native_run_round_robin_order(block, pair_index)" in source


def test_cyclic_latin_batch11_gives_each_sample_every_time_position_once() -> None:
    repetitions = 11
    batch_count = 11
    rows = [GATE._cyclic_latin_sample_order(block, repetitions, batch_count) for block in range(batch_count)]
    assert rows[0] == tuple(range(repetitions))
    assert rows[1] == tuple(range(1, repetitions)) + (0,)
    for sample_index in range(repetitions):
        positions = sorted(position for row in rows for position, value in enumerate(row) if value == sample_index)
        assert positions == list(range(repetitions))
    provenance = GATE._sample_schedule_provenance(batch_count, repetitions)
    assert provenance["sample_schedule"] == "cyclic-latin-v1"
    assert all(coverage == list(range(repetitions)) for coverage in provenance["position_coverage"].values())


def test_cyclic_latin_rejects_fixed_sample_order_mutation() -> None:
    repetitions = 11
    fixed_rows = [tuple(range(repetitions)) for _ in range(repetitions)]
    fixed_coverage = {
        sample_index: [position for row in fixed_rows for position, value in enumerate(row) if value == sample_index]
        for sample_index in range(repetitions)
    }
    expected = list(range(repetitions))
    assert any(coverage != expected for coverage in fixed_coverage.values())
    assert GATE._cyclic_latin_sample_order(1, repetitions, repetitions) != fixed_rows[1]


def test_compile_native_baseline_batch_is_equal_and_blocked() -> None:
    assert GATE.COMPILE_AND_RUN_BATCH_FORMULA == 13
    assert GATE.COMPILE_AND_RUN_BASELINE_BATCH_COUNT == 11
    assert GATE._compile_native_batch_count("compile-and-run", "small", 11, 1) == 11
    assert GATE._compile_native_batch_count("native-build", "medium", 11, 200) == 3
    native_rows = [GATE._cyclic_latin_sample_order(block, 11, 3) for block in range(3)]
    assert native_rows[0][0] == 0 and native_rows[1][0] == 4 and native_rows[2][0] == 8
    # Large is the frozen smoke tier, and native-run keeps its own calibration.
    assert GATE._compile_native_batch_count("compile-and-run", "large", 3, 1) == 1
    assert GATE._compile_native_batch_count("native-run", "small", 11, 7) == 7
    batch_count = 3
    repetitions = 11
    invocations = [
        (sample_index, implementation)
        for block in range(batch_count)
        for sample_index in range(repetitions)
        for implementation in GATE._native_run_round_robin_order(block, sample_index)
    ]
    for sample_index in range(repetitions):
        assert sum(impl == "styio" for sample, impl in invocations if sample == sample_index) == batch_count
        assert sum(impl == "cpp" for sample, impl in invocations if sample == sample_index) == batch_count
    source = inspect.getsource(GATE._measure_cell_once)
    assert "compile_native_blocked" in source
    assert "_compile_native_batch_count" in source


def test_rss_replay_is_one_operation_per_retained_sample() -> None:
    assert GATE.RSS_REPLAY_BATCH_COUNT == 1
    source = inspect.getsource(GATE._measure_cell_once)
    assert '"rss_replay_batch_count"' in source
    assert "rss_replay_count * rss_replay_batch_count" in source
    # RSS is a peak statistic, so replay must not aggregate the time batch.
    assert "execute_once(\n                        implementation,\n                        pair_index,\n                        0," in source


def test_calibration_rejects_nonfinite_or_zero_estimates() -> None:
    with pytest.raises(ValueError):
        GATE.calibrate_batch_count([0.0, 0.1])
    with pytest.raises(ValueError):
        GATE.calibrate_batch_count([float("nan"), 0.1])


def test_stability_selects_first_valid_attempt_not_fastest() -> None:
    attempts = [
        {
            "status": "pass",
            "time_samples_s": {"styio": [1.0, 1.4], "cpp": [1.0, 1.3]},
            "time": {"styio_cv_pct": 20.0, "cpp_cv_pct": 16.0},
        },
        {
            "status": "pass",
            "time_samples_s": {"styio": [1.0, 1.01], "cpp": [1.0, 1.01]},
            "time": {"styio_cv_pct": 0.7, "cpp_cv_pct": 0.7},
        },
        {
            "status": "pass",
            "time_samples_s": {"styio": [0.1, 0.1001], "cpp": [0.1, 0.1001]},
            "time": {"styio_cv_pct": 0.1, "cpp_cv_pct": 0.1},
        },
    ]
    assert GATE.select_first_valid_attempt(attempts) == 1


def test_focus_budgets_are_disjoint_and_sum_to_closure() -> None:
    cells = [
        {"id": "a", "status": "pass", "focus_owner": "frontend-parser", "route": "compile-and-run", "time": {"geomean_ratio": 1.20}},
        {"id": "b", "status": "pass", "focus_owner": "backend-runtime", "route": "native-run", "time": {"geomean_ratio": 1.00}},
    ]
    budgets = GATE.derive_focus_budgets(cells)
    assert budgets["non_overlapping"] is True
    assert budgets["required_closure"] == pytest.approx(budgets["allocation_sum"])
    assert budgets["allocations"]["frontend-parser"]["required_savings"] > 0
    assert budgets["allocations"]["backend-runtime"]["no_regression_target"] is True


def test_phase_provenance_is_required_by_strict_verification() -> None:
    catalog, path = GATE.load_catalog(CATALOG)
    cell = {
        "id": "compiler-phase/tokenize/small",
        "family": "compiler-phase",
        "status": "pass",
        "correctness": {"styio": True, "cpp": True},
        "repetitions": 1,
        "balanced_order": ["AB"],
        "batch_count": 1,
        "time_samples_s": {"styio": [1.0], "cpp": [1.0]},
        "peak_rss_samples_kib": {"styio": [1.0], "cpp": [1.0]},
        "reason_codes": [],
        "time": {"styio_cv_pct": 0.0, "cpp_cv_pct": 0.0, "geomean_ratio": 1.0, "median_ratio": 1.0},
        "throughput": {"geomean_ratio": 1.0, "median_ratio": 1.0},
        "peak_rss": {"geomean_ratio": 1.0, "median_ratio": 1.0},
    }
    report = {
        "schema": GATE.REPORT_SCHEMA,
        "schema_version": GATE.REPORT_VERSION,
        "contract_digest": GATE.contract_digest(catalog),
        "selection": {"cell_ids": [cell["id"]]},
        "cells": [cell],
        "diagnostics": [],
        "focus_budgets": GATE.derive_focus_budgets([cell]),
    }
    decision = GATE.verify_report(report, catalog, path, mode="smoke", privacy="strict")
    assert decision["decision"] == "fail"
    assert "missing_phase_provenance" in decision["reason_codes"]


def test_smoke_ignores_three_sample_cv_but_baseline_rejects_eleven() -> None:
    catalog, path = GATE.load_catalog(CATALOG)
    cell_id = "scalar-compute/large/compile-and-run"

    def report_for(repetitions: int) -> dict:
        styio = [1.0 if index % 2 == 0 else 100.0 for index in range(repetitions)]
        cpp = [1.0 if index % 2 == 0 else 80.0 for index in range(repetitions)]
        cell = {
            "id": cell_id,
            "family": "scalar-compute",
            "status": "pass",
            "correctness": {"styio": True, "cpp": True},
            "repetitions": repetitions,
            "balanced_order": ["AB" if index % 2 == 0 else "BA" for index in range(repetitions)],
            "batch_count": 1,
            "time_samples_s": {"styio": styio, "cpp": cpp},
            "peak_rss_samples_kib": {"styio": [10.0] * repetitions, "cpp": [11.0] * repetitions},
            "reason_codes": [],
            "time": {
                "styio_cv_pct": 100.0,
                "cpp_cv_pct": 100.0,
                "geomean_ratio": 1.0,
                "median_ratio": 1.0,
            },
            "throughput": {"geomean_ratio": 1.0, "median_ratio": 1.0},
            "peak_rss": {"geomean_ratio": 1.0, "median_ratio": 1.0},
        }
        return {
            "schema": GATE.REPORT_SCHEMA,
            "schema_version": GATE.REPORT_VERSION,
            "contract_digest": GATE.contract_digest(catalog),
            "selection": {"cell_ids": [cell_id]},
            "cells": [cell],
            "diagnostics": [],
            "focus_budgets": GATE.derive_focus_budgets([cell]),
        }

    smoke = GATE.verify_report(report_for(3), catalog, path, mode="smoke", privacy="strict")
    assert smoke["decision"] == "pass"
    baseline = GATE.verify_report(report_for(11), catalog, path, mode="baseline", privacy="strict")
    assert baseline["decision"] == "fail"
    assert "noise_cv" in baseline["reason_codes"]


def test_timeout_propagates_to_child_and_returns_stable_type() -> None:
    spec = importlib.util.spec_from_file_location("parity_rss_test_module", ROOT / "native-cpp" / "process_tree_rss.py")
    assert spec and spec.loader
    rss = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = rss
    spec.loader.exec_module(rss)
    with pytest.raises(rss.ProcessTimedOut):
        rss.run_process([sys.executable, "-c", "import time; time.sleep(1)"], cwd=ROOT, timeout_s=0.01)


def test_wait4_only_timing_does_not_start_tree_sampler(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("parity_rss_wait4_only_module", ROOT / "native-cpp" / "process_tree_rss.py")
    assert spec and spec.loader
    rss = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = rss
    spec.loader.exec_module(rss)

    def forbidden_sampler(*_args, **_kwargs):
        raise AssertionError("timed wait4-only pass started the RSS sampler")

    monkeypatch.setattr(rss, "_sample_process_tree", forbidden_sampler)
    sample = rss.run_process(
        [sys.executable, "-c", "pass"],
        cwd=ROOT,
        timeout_s=5,
        sample_process_tree=False,
    )
    assert sample.returncode == 0
    assert sample.elapsed_s > 0
    assert sample.peak_rss_kib > 0


def test_compile_and_run_validation_has_no_source_jit_preflight() -> None:
    # Route correctness must exercise a disposable native artifact for both
    # implementations; a source-level Styio JIT call would be a different
    # execution boundary and can mask compiler crashes in large cells.
    source = inspect.getsource(GATE._measure_cell_once)
    assert "_styio_run_command" not in source
    assert "_styio_build_command" in source


def test_route_timing_is_wait4_only_and_rss_replay_keeps_identity() -> None:
    source = inspect.getsource(GATE._measure_cell_once)
    assert "sample_process_tree=False" in source
    assert "sample_process_tree=True" in source
    assert "rss_replay_count" in source
    assert '"source": "wait4-only"' in source
    assert '"source": "isolated-replay"' in source
    # The direct RSS replay keeps route identity while the timed build routes
    # use the sample-level argv-safe helper.
    assert "_cyclic_latin_sample_order" in source
    assert "pair_index,\n                        0," in source


def test_rss_isolated_between_large_and_small_children() -> None:
    spec = importlib.util.spec_from_file_location("parity_rss_isolation_module", ROOT / "native-cpp" / "process_tree_rss.py")
    assert spec and spec.loader
    rss = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = rss
    spec.loader.exec_module(rss)
    large = rss.run_process(
        [sys.executable, "-c", "import time; payload=bytearray(64*1024*1024); time.sleep(0.08)"],
        cwd=ROOT,
        timeout_s=5,
    )
    small = rss.run_process(
        [sys.executable, "-c", "import time; time.sleep(0.08)"],
        cwd=ROOT,
        timeout_s=5,
    )
    assert large.peak_rss_kib > 0
    assert small.peak_rss_kib > 0
    assert small.peak_rss_kib < large.peak_rss_kib


def test_rss_captures_a_long_lived_descendant_tree() -> None:
    spec = importlib.util.spec_from_file_location("parity_rss_descendant_module", ROOT / "native-cpp" / "process_tree_rss.py")
    assert spec and spec.loader
    rss = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = rss
    spec.loader.exec_module(rss)
    child_code = "import time; payload=bytearray(48*1024*1024); time.sleep(0.30)"
    parent_code = (
        "import subprocess,sys; "
        f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}]); child.wait()"
    )
    sample = rss.run_process(
        [sys.executable, "-c", parent_code],
        cwd=ROOT,
        timeout_s=5,
        sample_interval_s=0.01,
    )
    # The parent itself is small; this threshold demonstrates that the
    # process-tree sampler observed its child rather than cumulative rusage.
    assert sample.peak_rss_kib > 24_000


def test_no_proc_sampler_delays_ps_and_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("parity_rss_schedule_module", ROOT / "native-cpp" / "process_tree_rss.py")
    assert spec and spec.loader
    rss = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = rss
    spec.loader.exec_module(rss)

    class FakeProcPath:
        def __init__(self, _value: str) -> None:
            pass

        def is_dir(self) -> bool:
            return False

    monkeypatch.setattr(rss, "Path", FakeProcPath)
    calls: list[float] = []
    stop = threading.Event()

    def fake_tree(_pid: int) -> float:
        calls.append(time.perf_counter())
        if len(calls) >= 2:
            stop.set()
        return 1.0

    monkeypatch.setattr(rss, "_proc_tree_rss_kib", fake_tree)
    started = time.perf_counter()
    values: list[float] = []
    rss._sample_process_tree(1, stop, values, 0.001)
    assert calls and calls[0] - started >= 0.09
    assert len(calls) == 3  # two 100-ms snapshots plus the final race-closing read
    assert calls[1] - calls[0] >= 0.09
    assert values == [1.0]


def test_batch_helper_uses_argv_safe_paths_and_checks_children(tmp_path: Path) -> None:
    helper = ROOT / "native-cpp" / "process_tree_rss.py"
    artifact = tmp_path / "artifact;metachar"
    input_path = tmp_path / "input;metachar"
    output_path = tmp_path / "output;metachar"
    artifact.write_text("#!/usr/bin/env python3\nimport sys\nsys.stdout.write(sys.stdin.read())\n", encoding="utf-8")
    artifact.chmod(0o755)
    input_path.write_bytes(b"ok\n")
    completed = subprocess.run(
        [
            sys.executable,
            str(helper),
            "--batch-exec",
            str(artifact),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--count",
            "3",
        ],
        cwd=ROOT,
        check=False,
    )
    assert completed.returncode == 0
    assert output_path.read_bytes() == b"ok\nok\nok\n"


def test_batch_build_run_uses_fresh_artifacts_and_hostile_paths(tmp_path: Path) -> None:
    helper = ROOT / "native-cpp" / "process_tree_rss.py"
    builder = tmp_path / "builder;argv.py"
    log_path = tmp_path / "build log;paths.txt"
    artifact_dir = tmp_path / "artifact dir;private"
    input_path = tmp_path / "input;identity.bin"
    output_path = tmp_path / "output;identity.bin"
    builder.write_text(
        "import pathlib,sys\n"
        "log=pathlib.Path(sys.argv[1]); artifact=pathlib.Path(sys.argv[2])\n"
        "with log.open('a', encoding='utf-8') as h: h.write(str(artifact)+'\\n')\n"
        "artifact.write_text(\"#!/usr/bin/env python3\\nimport sys\\nsys.stdout.buffer.write(sys.stdin.buffer.read())\\n\", encoding='utf-8')\n"
        "artifact.chmod(0o755)\n",
        encoding="utf-8",
    )
    input_path.write_bytes(b"identity\n")
    build_template = json.dumps([sys.executable, str(builder), str(log_path), "{artifact}"])
    completed = subprocess.run(
        [
            sys.executable,
            str(helper),
            "--batch-build-run",
            "--build-argv-json",
            build_template,
            "--artifact-dir",
            str(artifact_dir),
            "--artifact-token",
            "{artifact}",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--count",
            "3",
        ],
        cwd=ROOT,
        check=False,
    )
    assert completed.returncode == 0
    assert output_path.read_bytes() == input_path.read_bytes()
    artifacts = log_path.read_text(encoding="utf-8").splitlines()
    assert len(artifacts) == 3
    assert len(set(artifacts)) == 3
    assert not list(artifact_dir.glob("artifact-*"))
    source = (ROOT / "native-cpp" / "process_tree_rss.py").read_text(encoding="utf-8")
    assert "shell=True" not in source


def test_batch_build_run_propagates_builder_failure(tmp_path: Path) -> None:
    helper = ROOT / "native-cpp" / "process_tree_rss.py"
    builder = tmp_path / "failing-builder.py"
    builder.write_text(
        "import pathlib,sys\n"
        "artifact=pathlib.Path(sys.argv[1])\n"
        "if artifact.name.endswith('-1'): raise SystemExit(23)\n"
        "artifact.write_text(\"#!/usr/bin/env python3\\n\", encoding='utf-8'); artifact.chmod(0o755)\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(helper),
            "--batch-build",
            "--build-argv-json",
            json.dumps([sys.executable, str(builder), "{artifact}"]),
            "--artifact-dir",
            str(tmp_path / "artifacts"),
            "--count",
            "3",
        ],
        cwd=ROOT,
        check=False,
    )
    assert completed.returncode == 23


def test_elapsed_excludes_sampler_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("parity_rss_elapsed_module", ROOT / "native-cpp" / "process_tree_rss.py")
    assert spec and spec.loader
    rss = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = rss
    spec.loader.exec_module(rss)

    def delayed_sampler(_pid, stop, values, _interval):
        stop.wait()
        time.sleep(0.05)
        values.append(1.0)

    monkeypatch.setattr(rss, "_sample_process_tree", delayed_sampler)
    started = time.perf_counter()
    sample = rss.run_process([sys.executable, "-c", "pass"], cwd=ROOT, timeout_s=5)
    wall = time.perf_counter() - started
    assert wall - sample.elapsed_s >= 0.02


def test_verify_rejects_missing_samples_and_unbalanced_pairs() -> None:
    catalog, path = GATE.load_catalog(CATALOG)
    report = {
        "schema": GATE.REPORT_SCHEMA,
        "schema_version": GATE.REPORT_VERSION,
        "contract_digest": GATE.contract_digest(catalog),
        "selection": {"cell_ids": ["scalar-compute/small/compile-and-run"]},
        "cells": [
            {
                "id": "scalar-compute/small/compile-and-run",
                "status": "pass",
                "correctness": {"styio": True, "cpp": True},
                "repetitions": 3,
                "balanced_order": ["AB", "AB", "BA"],
                "time_samples_s": {"styio": [1.0], "cpp": [1.0, 1.0, 1.0]},
                "peak_rss_samples_kib": {"styio": [1.0, 1.0, 1.0], "cpp": [1.0, 1.0, 1.0]},
                "reason_codes": [],
                "time": {
                    "styio_cv_pct": 0.0,
                    "cpp_cv_pct": 0.0,
                    "geomean_ratio": 1.0,
                    "median_ratio": 1.0,
                },
                "throughput": {"geomean_ratio": 1.0, "median_ratio": 1.0},
                "peak_rss": {"geomean_ratio": 1.0, "median_ratio": 1.0},
            }
        ],
        "diagnostics": [],
    }
    decision = GATE.verify_report(report, catalog, path, mode="smoke", privacy="strict")
    assert decision["decision"] == "fail"
    assert "missing_samples" in decision["reason_codes"]
    assert "unbalanced_pairs" in decision["reason_codes"]


def test_verify_final_enforces_geomean_and_case_limits() -> None:
    catalog, path = GATE.load_catalog(CATALOG)
    cell = {
        "id": "scalar-compute/small/compile-and-run",
        "status": "pass",
        "correctness": {"styio": True, "cpp": True},
        "repetitions": 2,
        "balanced_order": ["AB", "BA"],
        "time_samples_s": {"styio": [2.0, 2.0], "cpp": [1.0, 1.0]},
        "peak_rss_samples_kib": {"styio": [2.0, 2.0], "cpp": [1.0, 1.0]},
        "reason_codes": [],
        "time": {"styio_cv_pct": 0.0, "cpp_cv_pct": 0.0, "geomean_ratio": 2.0, "median_ratio": 2.0},
        "throughput": {"geomean_ratio": 2.0, "median_ratio": 2.0},
        "peak_rss": {"geomean_ratio": 2.0, "median_ratio": 2.0},
    }
    report = {
        "schema": GATE.REPORT_SCHEMA,
        "schema_version": GATE.REPORT_VERSION,
        "contract_digest": GATE.contract_digest(catalog),
        "selection": {"cell_ids": [cell["id"]]},
        "cells": [cell],
        "aggregates": {"equal_weight": {name: {"geomean_ratio": 2.0} for name in ("time", "throughput", "peak_rss")}},
        "diagnostics": [],
    }
    decision = GATE.verify_report(report, catalog, path, mode="final", privacy="strict")
    assert decision["decision"] == "fail"
    assert "geomean_threshold" in decision["reason_codes"]
    assert "case_threshold" in decision["reason_codes"]
