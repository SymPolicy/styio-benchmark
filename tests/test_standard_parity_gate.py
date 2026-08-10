"""Statistical, sharding, merge, and fail-closed gate tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "workloads" / "parity-v2" / "contract.json"


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
    samples = [1.0] * repetitions
    return {
        "id": cell_id,
        "status": "pass",
        "correctness": {"styio": True, "cpp": True},
        "repetitions": repetitions,
        "time_samples_s": {"styio": samples, "cpp": samples},
        "peak_rss_samples_kib": {"styio": samples, "cpp": samples},
        "time": {"geomean_ratio": ratio, "median_ratio": ratio, "styio_cv_pct": 0.0, "cpp_cv_pct": 0.0},
        "peak_rss": {"geomean_ratio": 1.0, "median_ratio": 1.0},
        "throughput": {"geomean_ratio": 1.0, "median_ratio": 1.0},
    }


def test_statistics_retain_every_sample_and_use_paired_geomean() -> None:
    gate = _gate()
    assert gate.median([1.0, 2.0, 4.0]) == 2.0
    assert gate.sample_cv_pct([1.0, 2.0, 4.0]) > 0
    result = gate.ratio_dimension([1.0, 2.0, 4.0], [1.0, 1.0, 2.0])
    assert result["styio_samples"] == [1.0, 2.0, 4.0]
    assert result["cpp_samples"] == [1.0, 1.0, 2.0]
    assert result["geomean_ratio"] == pytest.approx(4 ** (1 / 3), rel=1e-12)
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
    report = {"contract_digest": gate.contract_digest(catalog), "cells": [_cell(gate, "clbg-fannkuch-redux/reference/compile-and-run")]}
    verdict = gate.verify_report(report, catalog, require_all=True)
    assert verdict["decision"] == "fail"
    assert "missing_required_cells" in verdict["reason_codes"]


def test_verify_rejects_real_ratio_gap_and_noise() -> None:
    gate, catalog, _ = _catalog()
    report = {"contract_digest": gate.contract_digest(catalog), "cells": [_cell(gate, "clbg-fannkuch-redux/smoke/compile-and-run", ratio=1.2, repetitions=3)]}
    report["cells"][0]["time_samples_s"]["styio"] = [1.0, 2.0, 4.0]
    verdict = gate.verify_report(report, catalog, require_all=False)
    assert verdict["decision"] == "pass"  # smoke is descriptive, not an official score
    strict = gate.verify_report(report, catalog, require_all=True)
    assert strict["decision"] == "fail"
    assert {"noise_cv", "missing_required_cells"}.intersection(strict["reason_codes"])


def test_merge_rejects_duplicate_cell_identity(tmp_path: Path) -> None:
    gate, catalog, _ = _catalog()
    base = {"schema": gate.REPORT_SCHEMA, "contract_digest": gate.contract_digest(catalog), "toolchain": {"compiler_family": "clang", "compiler_version": "18.1", "target_class": "portable", "optimization": "O3", "lto": False, "threads": 1, "runner": gate.RUNNER_VERSION}, "measurement": {"warmups": 3, "repetitions": 11, "max_cv_pct": 5.0, "observer_free_timing": True, "isolated_memory_replay": True, "sample_policy": "retain-all"}, "selection": {"cell_ids": ["clbg-fannkuch-redux/smoke/compile-and-run"]}, "cells": [_cell(gate, "clbg-fannkuch-redux/smoke/compile-and-run")]}
    first = tmp_path / "one" / "results.json"; second = tmp_path / "two" / "results.json"
    first.parent.mkdir(); second.parent.mkdir()
    first.write_text(json.dumps(base), encoding="utf-8"); second.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(gate.GateError) as error:
        gate.merge_reports(catalog, [first, second], tmp_path / "merged")
    assert error.value.reason_code == "merge_duplicate_cell"


def test_privacy_rejects_raw_subprocess_and_absolute_path_values() -> None:
    gate = _gate()
    with pytest.raises(gate.PrivacyError):
        gate.validate_public_report({"diagnostic": "/Users/private/source.cpp"})
    with pytest.raises(gate.PrivacyError):
        gate.validate_public_report({"stderr": "compiler secret"})


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
