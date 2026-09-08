"""Statistical, sharding, merge, and fail-closed gate tests."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import math
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "workloads" / "parity-v2" / "contract.json"
CATALOG = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _gate():
    spec = importlib.util.spec_from_file_location("standard_parity_gate_test", ROOT / "tools" / "standard_parity_gate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog():
    gate = _gate()
    return gate, *gate.load_catalog(CATALOG_PATH)


def _cell(gate, cell_id: str, *, ratio: float = 1.0, repetitions: int = 11) -> dict:
    contract_cells = [
        cell
        for workload in CATALOG["workloads"]
        for cell in workload["cells"]
    ] + CATALOG["compiler_phase_sweep"]["cells"]
    contract_cell = next(cell for cell in contract_cells if cell["id"] == cell_id)
    parts = cell_id.split("/")
    is_phase = parts[0] == "compiler-phase"
    family = "compiler-phase" if is_phase else parts[0]
    scale = parts[2] if is_phase else parts[1]
    route = "compile-and-run" if is_phase else parts[2]
    styio_samples = [ratio] * repetitions
    cpp_samples = [1.0] * repetitions
    rss_samples = [1.0] * repetitions
    schedule = [
        "AB" if order == ("styio", "cpp") else "BA"
        for order in gate._interleaved_pair_orders(cell_id, repetitions, "retained")
    ]
    return {
        "id": cell_id,
        "family": family,
        "scale": scale,
        "route": route,
        "required": scale == "reference",
        "work_units": contract_cell["work_units"],
        "algorithm_id": contract_cell["algorithm_id"],
        "focus_owner": contract_cell["focus_owner"],
        "status": "pass",
        "correctness": {"styio": True, "cpp": True},
        "repetitions": repetitions,
        "warmups": 3,
        "source_digests": contract_cell["source_digest"],
        "input_digest": contract_cell["input_digest"],
        "expected_output_digest": contract_cell["expected_output_digest"],
        "time_samples_s": {"styio": styio_samples, "cpp": cpp_samples},
        "raw_batch_time_samples_s": {"styio": styio_samples, "cpp": cpp_samples},
        "peak_rss_samples_kib": {"styio": rss_samples, "cpp": rss_samples},
        "time": {"geomean_ratio": ratio, "median_ratio": ratio, "styio_cv_pct": 0.0, "cpp_cv_pct": 0.0},
        "peak_rss": {"geomean_ratio": 1.0, "median_ratio": 1.0},
        "throughput": {"geomean_ratio": 1.0, "median_ratio": 1.0},
        "batch": {
            "count": 1,
            "minimum_sample_time_s": gate.MIN_SAMPLE_DURATION_S,
            "target_sample_time_s": gate.CALIBRATION_TARGET_DURATION_S,
            "maximum_count": gate.MAX_BATCH_COUNT,
            "retained_floor_met": True,
            "equal_work": True,
        },
        "sample_schedule": {
            "strategy": "deterministic-random-interleaving-v1",
            "ab_count": schedule.count("AB"),
            "ba_count": schedule.count("BA"),
            "schedule_digest": hashlib.sha256("".join(schedule).encode("ascii")).hexdigest(),
        },
    }


def _report(gate, catalog, cells: list[dict], *, run_class: str = "development", repetitions: int = 11) -> dict:
    return {
        "schema": gate.REPORT_SCHEMA,
        "schema_version": gate.REPORT_VERSION,
        "runner_version": gate.RUNNER_VERSION,
        "contract_digest": gate.contract_digest(catalog),
        "toolchain": {
            "styio_version": "0.0.1",
            "styio_backend": "native-aot",
            "compiler_family": "clang",
            "compiler_version": "18.1",
            "target_class": "portable",
            "optimization": "O3",
            "lto": False,
            "threads": 1,
            "runner": gate.RUNNER_VERSION,
            "phase_probe_available": True,
        },
        "measurement": gate._measurement_metadata(3, repetitions, run_class),
        "capabilities": gate._capability_disclosure(catalog),
        "aggregates": gate.report_aggregates(cells),
        "cells": cells,
    }


def test_statistics_retain_every_sample_and_use_paired_geomean() -> None:
    gate = _gate()
    assert gate.median([1.0, 2.0, 4.0]) == 2.0
    assert gate.sample_cv_pct([1.0, 2.0, 4.0]) > 0
    result = gate.ratio_dimension([1.0, 2.0, 4.0], [1.0, 1.0, 2.0])
    assert result["styio_samples"] == [1.0, 2.0, 4.0]
    assert result["cpp_samples"] == [1.0, 1.0, 2.0]
    assert result["geomean_ratio"] == pytest.approx(4 ** (1 / 3), rel=1e-12)
    assert result["confidence_interval"]["level"] == 0.95
    assert result["confidence_interval"]["resamples"] == 10_000
    throughput = gate.ratio_dimension([10.0, 10.0], [5.0, 5.0], reciprocal=True)
    assert throughput["median_ratio"] == pytest.approx(0.5)


def test_deterministic_shards_are_complete_and_sorted() -> None:
    gate, catalog, _ = _catalog()
    shard = gate.deterministic_shard(catalog, "clbg-fannkuch-redux", "smoke")
    assert [cell["id"] for cell in shard] == sorted(cell["id"] for cell in shard)
    assert {cell["route"] for cell in shard} == set(gate.ROUTES)
    assert gate.deterministic_shard(catalog, "compiler-phase", "reference")[0]["id"].startswith("compiler-phase/")


def test_verify_requires_all_reference_cells_without_deleting_samples() -> None:
    gate, catalog, _ = _catalog()
    cells = [_cell(gate, "clbg-fannkuch-redux/reference/compile-and-run")]
    report = _report(gate, catalog, cells, run_class="controlled")
    verdict = gate.verify_report(report, catalog, require_all=True)
    assert verdict["decision"] == "fail"
    assert "missing_required_cells" in verdict["reason_codes"]
    relaxed = gate.verify_report(report, catalog, require_all=True, max_case_ratio=2.0)
    assert "threshold_relaxed" in relaxed["reason_codes"]


def test_verify_rejects_real_ratio_gap_and_noise() -> None:
    gate, catalog, _ = _catalog()
    cells = [_cell(gate, "clbg-fannkuch-redux/smoke/compile-and-run", ratio=2.0, repetitions=3)]
    cells[0]["time_samples_s"]["styio"] = [1.0, 2.0, 4.0]
    cells[0]["raw_batch_time_samples_s"]["styio"] = [1.0, 2.0, 4.0]
    cells[0]["time"]["geomean_ratio"] = 2.0
    report = _report(gate, catalog, cells, repetitions=3)
    verdict = gate.verify_report(report, catalog, require_all=False)
    assert verdict["decision"] == "pass"  # smoke is descriptive, not an official score
    strict = gate.verify_report(report, catalog, require_all=True)
    assert strict["decision"] == "fail"
    assert {"noise_cv", "missing_required_cells"}.intersection(strict["reason_codes"])


def test_merge_rejects_duplicate_cell_identity(tmp_path: Path) -> None:
    gate, catalog, _ = _catalog()
    cells = [_cell(gate, "clbg-fannkuch-redux/smoke/compile-and-run")]
    base = _report(gate, catalog, cells)
    base.update({"selection": {"cell_ids": ["clbg-fannkuch-redux/smoke/compile-and-run"]}})
    first = tmp_path / "one" / "results.json"; second = tmp_path / "two" / "results.json"
    first.parent.mkdir(); second.parent.mkdir()
    first.write_text(json.dumps(base), encoding="utf-8"); second.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(gate.GateError) as error:
        gate.merge_reports(catalog, [first, second], tmp_path / "merged")
    assert error.value.reason_code == "merge_duplicate_cell"


@pytest.mark.parametrize(
    "payload",
    (
        {"host_name": "public-host"},
        {"nested": {"environment": {"PATH": "private"}}},
        {"command": ["styio", "build"]},
        {"endpoint": "https://example.invalid"},
        {"secret": "sk-test-value"},
        {"diagnostic": "/Users/private/source.cpp"},
        {"stderr": "compiler secret"},
        {"value": "unsanitized\nchild text"},
        {"ratio": float("nan")},
    ),
)
def test_privacy_rejects_sensitive_or_nonfinite_payloads(payload: dict) -> None:
    gate = _gate()
    with pytest.raises(gate.PrivacyError):
        gate.validate_public_report(payload)


def test_phase_probe_and_bucket_mutation_are_isolated() -> None:
    gate, catalog, _ = _catalog()
    probe = "\n".join(
        [
            "[micro-bench] focus=lexer focus_us=10",
            "[micro-bench] focus=parser focus_us=20",
            "[micro-bench] focus=type focus_us=30",
            "[micro-bench] focus=lower focus_us=40",
            "[micro-bench] focus=llvm focus_us=50",
        ]
    )
    parsed = gate._parse_phase_probe_output(probe)
    assert parsed == {
        "tokenize": 0.00001,
        "parse": 0.00002,
        "semantic-analysis": 0.00003,
        "lowering": 0.00004,
        "llvm-emission": 0.00005,
    }
    trace = gate._classify_clang_phase(
        [
            ("Total Frontend", 30.0),
            ("Total Optimizer", 20.0),
            ("Total Backend", 10.0),
        ]
    )
    assert all(trace[phase] > 0 for phase in gate.PHASES)

    phase_cells = catalog["compiler_phase_sweep"]["cells"]
    token_cell = next(cell for cell in phase_cells if cell["id"] == "compiler-phase/tokenize/smoke")
    parse_cell = next(cell for cell in phase_cells if cell["id"] == "compiler-phase/parse/smoke")
    evidence = {
        "styio_time_s": {phase: [1.0, 1.0, 1.0] for phase in gate.PHASES},
        "cpp_time_s": {phase: [1.0, 1.0, 1.0] for phase in gate.PHASES},
        "styio_peak_rss_kib": [1.0, 1.0, 1.0],
        "cpp_peak_rss_kib": [1.0, 1.0, 1.0],
        "static_structure": {
            "styio": {"source_digest": "a" * 64, "source_bytes": 10, "language": "styio"},
            "cpp": {"source_digest": "b" * 64, "source_bytes": 20, "language": "C++20"},
        },
    }
    before_token = gate._phase_record(token_cell, evidence=evidence, correctness={"styio": True, "cpp": True}, warmups=1, repetitions=3)
    before_parse = gate._phase_record(parse_cell, evidence=evidence, correctness={"styio": True, "cpp": True}, warmups=1, repetitions=3)
    evidence["styio_time_s"]["parse"] = [2.0, 2.0, 2.0]
    after_token = gate._phase_record(token_cell, evidence=evidence, correctness={"styio": True, "cpp": True}, warmups=1, repetitions=3)
    after_parse = gate._phase_record(parse_cell, evidence=evidence, correctness={"styio": True, "cpp": True}, warmups=1, repetitions=3)
    assert after_token["time_samples_s"] == before_token["time_samples_s"]
    assert after_parse["time_samples_s"]["styio"] == [2.0, 2.0, 2.0]
    assert before_parse["time_samples_s"]["styio"] != after_parse["time_samples_s"]["styio"]
    assert token_cell["work_units"] == parse_cell["work_units"]
    assert evidence["static_structure"]["styio"]["source_digest"] != evidence["static_structure"]["cpp"]["source_digest"]


def test_minimum_time_calibration_and_interleaving_are_deterministic() -> None:
    gate = _gate()
    assert gate.calibrate_batch_count([0.001, 0.002], target_duration_s=0.5) == 500
    assert gate.calibrate_batch_count([1.0, 2.0], target_duration_s=0.5) == 1
    assert gate.normalize_batched_elapsed(0.5, 100) == pytest.approx(0.005)
    orders = gate._interleaved_pair_orders("cell/reference/native-run", 11, "retained")
    assert orders == gate._interleaved_pair_orders("cell/reference/native-run", 11, "retained")
    assert abs(orders.count(("styio", "cpp")) - orders.count(("cpp", "styio"))) == 1


def test_bootstrap_interval_is_paired_reproducible_and_exact_for_constant_ratio() -> None:
    gate = _gate()
    logs = [[math.log(1.03)] * 11 for _ in range(4)]
    first = gate.bootstrap_geomean_ratio_ci(logs, resamples=1000)
    second = gate.bootstrap_geomean_ratio_ci(logs, resamples=1000)
    assert first == second
    assert first["lower"] == pytest.approx(1.03, rel=1e-12)
    assert first["upper"] == pytest.approx(1.03, rel=1e-12)


def test_aggregates_are_route_separated_and_phases_are_diagnostic_only() -> None:
    gate = _gate()
    cells = [
        _cell(gate, "clbg-fannkuch-redux/reference/native-run", ratio=1.02),
        _cell(gate, "clbg-fannkuch-redux/reference/native-build", ratio=0.5),
        _cell(gate, "compiler-phase/parse/reference", ratio=9.0),
    ]
    aggregates = gate.report_aggregates(cells)
    routes = aggregates["scales"]["reference"]["routes"]
    assert routes["native-run"]["time"]["geomean_ratio"] == pytest.approx(1.02)
    assert routes["native-build"]["time"]["geomean_ratio"] == pytest.approx(0.5)
    assert aggregates["compiler_phases"] == {"diagnostic_only": True, "cell_count": 1}
    assert aggregates["mixed_headline_score"] is False


def test_verifier_rejects_legacy_report_schema() -> None:
    gate, catalog, _ = _catalog()
    cells = [_cell(gate, "clbg-fannkuch-redux/smoke/native-run")]
    report = _report(gate, catalog, cells)
    report["schema"] = "styio.parity.standard.report.v2"
    assert "report_schema" in gate.verify_report(report, catalog)["reason_codes"]


def test_strict_gate_scores_routes_not_phases_and_enforces_memory() -> None:
    gate, catalog, _ = _catalog()
    reference_ids = sorted(gate._required_ids(catalog))
    cells = [
        _cell(gate, cell_id, ratio=8.0 if cell_id == "compiler-phase/parse/reference" else 1.0)
        for cell_id in reference_ids
    ]
    report = _report(gate, catalog, cells, run_class="controlled")
    verdict = gate.verify_report(report, catalog, require_all=True)
    assert verdict["decision"] == "pass"
    assert verdict["primary_route"] == "native-run"
    assert set(verdict["reference_routes"]) == set(gate.ROUTES)
    assert all(summary["time"]["cell_count"] == 11 for summary in verdict["reference_routes"].values())

    victim = next(cell for cell in cells if cell["id"] == "clbg-fannkuch-redux/reference/native-run")
    victim["peak_rss_samples_kib"]["styio"] = [2.0] * 11
    victim["peak_rss"]["geomean_ratio"] = 2.0
    report["aggregates"] = gate.report_aggregates(cells)
    failed = gate.verify_report(report, catalog, require_all=True)
    assert failed["decision"] == "fail"
    assert "memory_case_ratio" in failed["reason_codes"]
    assert "route_memory_confidence" in failed["reason_codes"]
