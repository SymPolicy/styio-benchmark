"""Deterministic Styio Analyzer v1 tests. These fixtures are not real timings."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "workloads" / "parity-v2" / "contract.json"
# AC-04/AC-05 fixtures are synthetic Analyzer math only, not performance evidence.
FIXTURE_NOTE = "synthetic-analyzer-math-fixture"


def _load(name: str, path: Path):
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _analyzer():
    return _load("styio_analyzer_test", ROOT / "tools" / "styio_analyzer.py")


def _core():
    return _analyzer()._core


def _gate():
    return _analyzer()._gate


def _oracle_hex(family: str = "llvm-scalar-chain", scale: str = "smoke") -> str:
    gate = _gate()
    catalog, catalog_path = gate.load_catalog(CATALOG_PATH)
    generators = gate._load_generators(catalog_path)
    size = next(
        cell["work_units"]
        for workload in catalog["workloads"]
        if workload["id"] == family
        for cell in workload["cells"]
        if cell["scale"] == scale
    )
    return generators.reference_for(family, int(size)).hex()


def _write_standin(
    path: Path,
    *,
    role: str,
    log_path: Path,
    output_hex: str,
    compile_fail: bool = False,
    wrong_output: bool = False,
    embedded_root: Path | None = None,
) -> None:
    payload = "00" if wrong_output else output_hex
    if embedded_root is None:
        cache = path.parent.parent / "CMakeCache.txt"
        if cache.exists():
            value = next((line.split("=", 1)[1] for line in cache.read_text().splitlines() if line.startswith("CMAKE_HOME_DIRECTORY:")), "")
            embedded_root = Path(value) if value else None
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json, os, stat, sys, subprocess
            from pathlib import Path
            log = Path({str(log_path)!r})
            argv = sys.argv[1:]
            cwd = os.getcwd()
            event = {{
                "role": {role!r},
                "op": "version" if argv[:1] == ["--version"] else ("build" if argv[:1] == ["build"] else "other"),
                "cache": os.environ.get("STYIO_NATIVE_CACHE"),
                "cwd": cwd,
                "native_cxx_set": bool(os.environ.get("STYIO_NATIVE_CXX")),
                "runtime_marker": (Path(cwd) / "src" / "StyioExtern" / "ExternLib.cpp").is_file(),
            }}
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event) + "\\n")
            if argv[:1] == ["--version"]:
                print("styio 0.0.1-{role}")
                raise SystemExit(0)
            if argv[:1] != ["build"] or "-o" not in argv:
                raise SystemExit(2)
            if {compile_fail!r}:
                raise SystemExit(1)
            runtime = Path({str(embedded_root)!r}) if {embedded_root is not None!r} else None
            if runtime is None or not (runtime / "src/StyioExtern/ExternLib.cpp").is_file():
                runtime = next((p for p in Path(__file__).resolve().parents if (p / "src/StyioExtern/ExternLib.cpp").is_file()), None)
            if runtime is not None and os.environ.get("STYIO_NATIVE_CXX"):
                code = subprocess.run([
                    os.environ["STYIO_NATIVE_CXX"], "-std=c++20", "-O3", "-fno-lto",
                    "-fsyntax-only", str(runtime / "src/StyioExtern/ExternLib.cpp"),
                ]).returncode
                if code:
                    raise SystemExit(code)
            dest = Path(argv[argv.index("-o") + 1])
            dest.write_text(
                "#!/usr/bin/env python3\\nimport sys\\nsys.stdout.buffer.write(bytes.fromhex({payload!r}))\\n"
            )
            dest.chmod(dest.stat().st_mode | stat.S_IEXEC)
            raise SystemExit(0)
            """
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _plant_runtime(root: Path) -> None:
    marker = root / "src" / "StyioExtern" / "ExternLib.cpp"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("// runtime marker\n", encoding="utf-8")


def _write_cmake_cache(build_dir: Path, *, cxx: Path, source_root: Path) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "CMakeCache.txt").write_text(
        "\n".join(
            [
                f"CMAKE_CXX_COMPILER:FILEPATH={cxx}",
                f"CMAKE_HOME_DIRECTORY:INTERNAL={source_root}",
                f"Styio_SOURCE_DIR:STATIC={source_root}",
                "CMAKE_BUILD_TYPE:STRING=Release",
                "CMAKE_CXX_FLAGS_RELEASE:STRING=-O3 -DNDEBUG",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_clang_standin(path: Path, version: str, log_path: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json, sys
            from pathlib import Path
            log = Path({str(log_path)!r}) if {log_path is not None!r} else None
            if log is not None:
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(json.dumps({{"argv0": sys.argv[0], "op": sys.argv[1:]}}) + "\\n", encoding="utf-8")
            if sys.argv[1:2] == ["--version"]:
                print("clang version {version}")
                print("Target: x86_64-unknown-linux-gnu")
                raise SystemExit(0)
            raise SystemExit(0)
            """
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _load_events(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_help_states_exit_code_is_not_performance_acceptance() -> None:
    analyzer = _analyzer()
    help_text = analyzer._parser().format_help()
    assert "not" in help_text.lower() and "performance acceptance" in help_text.lower()
    assert "compare exits 0" in help_text
    assert "verify exits 0" in help_text


def test_family_selection_covers_all_eleven_and_rejects_unknown() -> None:
    analyzer = _analyzer()
    gate = _gate()
    catalog, _ = gate.load_catalog(CATALOG_PATH)
    families = analyzer.catalog_family_ids(catalog)
    assert len(families) == 11
    assert "compiler-phase" not in families
    assert analyzer.select_families(catalog, None) == list(families)
    assert analyzer.select_families(catalog, ["llvm-scalar-chain", "llvm-dense-matmul"]) == [
        "llvm-scalar-chain",
        "llvm-dense-matmul",
    ]
    with pytest.raises(analyzer.ReasonError) as error:
        analyzer.select_families(catalog, ["compiler-phase"])
    assert error.value.reason_code == "unknown_family"
    with pytest.raises(analyzer.ReasonError) as error:
        analyzer.select_families(catalog, [""])
    assert error.value.reason_code == "empty_family_selection"
    cells = analyzer.select_cells(catalog, families, "smoke")
    assert len(cells) == 11 * 3
    assert {cell["route"] for cell in cells} == set(analyzer.ROUTES)


def test_missing_candidate_does_not_fall_back_to_baseline(tmp_path: Path) -> None:
    analyzer = _analyzer()
    gate = _gate()
    catalog, catalog_path = gate.load_catalog(CATALOG_PATH)
    output_hex = _oracle_hex()
    baseline_root = tmp_path / "baseline-root"
    candidate_root = tmp_path / "candidate-root"
    baseline_build = tmp_path / "baseline-build" / "bin"
    candidate_build = tmp_path / "candidate-build" / "bin"
    baseline_root.mkdir()
    candidate_root.mkdir()
    baseline_build.mkdir(parents=True)
    candidate_build.mkdir(parents=True)
    baseline_log = tmp_path / "baseline.log"
    _write_standin(baseline_build / "styio", role="baseline", log_path=baseline_log, output_hex=output_hex)
    out = tmp_path / "out"
    report = analyzer.compare_toolchains(
        catalog,
        catalog_path,
        baseline_root=baseline_root,
        baseline_build_dir=baseline_build.parent,
        candidate_root=candidate_root,
        candidate_build_dir=candidate_build.parent,
        families=["llvm-scalar-chain"],
        scale="smoke",
        run_class="development",
        output_dir=out,
        warmups=0,
        repetitions=1,
    )
    assert report["collection_complete"] is False
    assert report["toolchains"]["incomparable_reasons"] == ["styio_compiler_missing"]
    assert report["cells"] == []
    assert not _load_events(baseline_log)
    text = (out / "results.json").read_text(encoding="utf-8")
    assert "baseline-root" not in text
    assert str(tmp_path) not in text


def test_sides_use_own_compiler_workdir_oracle_and_cache_overlay(tmp_path: Path) -> None:
    analyzer = _analyzer()
    gate = _gate()
    catalog, catalog_path = gate.load_catalog(CATALOG_PATH)
    output_hex = _oracle_hex()
    baseline_root = tmp_path / "baseline-root"
    candidate_root = tmp_path / "candidate-root"
    baseline_build = tmp_path / "baseline-build" / "bin"
    candidate_build = tmp_path / "candidate-build" / "bin"
    for path in (baseline_root, candidate_root, baseline_build, candidate_build):
        path.mkdir(parents=True, exist_ok=True)
    baseline_log = tmp_path / "baseline.log"
    candidate_log = tmp_path / "candidate.log"
    clang = tmp_path / "clang++"
    _write_clang_standin(clang, "18.1.0")
    _plant_runtime(baseline_root)
    _plant_runtime(candidate_root)
    _write_cmake_cache(baseline_build.parent, cxx=clang, source_root=baseline_root)
    _write_cmake_cache(candidate_build.parent, cxx=clang, source_root=candidate_root)
    _write_standin(baseline_build / "styio", role="baseline", log_path=baseline_log, output_hex=output_hex)
    _write_standin(candidate_build / "styio", role="candidate", log_path=candidate_log, output_hex=output_hex)
    assert os.environ.get("STYIO_NATIVE_CACHE") is None
    events: list[dict[str, str]] = []
    report = analyzer.compare_toolchains(
        catalog,
        catalog_path,
        baseline_root=baseline_root,
        baseline_build_dir=baseline_build.parent,
        candidate_root=candidate_root,
        candidate_build_dir=candidate_build.parent,
        families=["llvm-scalar-chain"],
        scale="smoke",
        run_class="development",
        output_dir=tmp_path / "ok",
        warmups=0,
        repetitions=2,
        event_sink=events,
    )
    assert os.environ.get("STYIO_NATIVE_CACHE") is None
    assert report["toolchains"]["baseline"]["artifact_digest"] != report["toolchains"]["candidate"]["artifact_digest"]
    assert report["toolchains"]["baseline"]["role"] == "baseline"
    assert report["toolchains"]["candidate"]["role"] == "candidate"
    assert report["toolchains"]["baseline"]["runtime_binding"] == "confirmed"
    assert report["toolchains"]["candidate"]["runtime_binding"] == "confirmed"
    assert report["toolchains"]["comparable"] is True
    assert report["toolchains"]["external"]["compiler_version"] == "18.1.0"
    assert report["toolchains"]["external"]["selection"] == "embedded_cmake"
    assert all(cell["correctness"] == {"baseline": True, "candidate": True} for cell in report["cells"])
    baseline_events = _load_events(baseline_log)
    candidate_events = _load_events(candidate_log)
    baseline_ops = {item["role"] for item in baseline_events}
    candidate_ops = {item["role"] for item in candidate_events}
    assert baseline_ops == {"baseline"}
    assert candidate_ops == {"candidate"}
    assert {Path(item["cwd"]).resolve() for item in baseline_events if item["op"] == "build"} == {baseline_root.resolve()}
    assert {Path(item["cwd"]).resolve() for item in candidate_events if item["op"] == "build"} == {candidate_root.resolve()}
    assert all(item["runtime_marker"] for item in baseline_events + candidate_events if item["op"] == "build")
    assert all(item["native_cxx_set"] for item in baseline_events + candidate_events if item["op"] == "build")
    assert all(item["cache"] == "0" for item in baseline_events + candidate_events if item["op"] == "build")
    routes = {cell["id"]: cell for cell in report["cells"]}
    assert set(routes) == {
        "llvm-scalar-chain/smoke/compile-and-run",
        "llvm-scalar-chain/smoke/native-build",
        "llvm-scalar-chain/smoke/native-run",
    }
    timing = [item for item in events if item["phase"] == "timing"]
    rss = [item for item in events if item["phase"] == "rss"]
    assert {item["action"] for item in timing if item["route"] == "native-run"} == {"batch-exec"}
    assert {item["action"] for item in timing if item["route"] == "native-build"} == {"batch-build"}
    assert {item["action"] for item in timing if item["route"] == "compile-and-run"} == {"batch-build-run"}
    assert {item["action"] for item in rss if item["route"] == "native-run"} == {"run"}
    assert "build" in {item["action"] for item in rss if item["route"] == "native-build"}
    assert {"build", "run"} <= {item["action"] for item in rss if item["route"] == "compile-and-run"}
    assert all(item["phase"] != "timing" or item["action"] != "run" for item in rss)


def test_candidate_and_both_wrong_outputs_fail_independent_oracle(tmp_path: Path) -> None:
    analyzer = _analyzer()
    gate = _gate()
    catalog, catalog_path = gate.load_catalog(CATALOG_PATH)
    output_hex = _oracle_hex()

    def run(*, candidate_wrong: bool, baseline_wrong: bool) -> dict:
        root = tmp_path / f"{int(candidate_wrong)}{int(baseline_wrong)}"
        baseline_build = root / "b" / "bin"
        candidate_build = root / "c" / "bin"
        baseline_root = root / "br"
        candidate_root = root / "cr"
        for path in (baseline_build, candidate_build, baseline_root, candidate_root):
            path.mkdir(parents=True)
        _write_standin(
            baseline_build / "styio",
            role="baseline",
            log_path=root / "b.log",
            output_hex=output_hex,
            wrong_output=baseline_wrong,
        )
        _write_standin(
            candidate_build / "styio",
            role="candidate",
            log_path=root / "c.log",
            output_hex=output_hex,
            wrong_output=candidate_wrong,
        )
        return analyzer.compare_toolchains(
            catalog,
            catalog_path,
            baseline_root=baseline_root,
            baseline_build_dir=baseline_build.parent,
            candidate_root=candidate_root,
            candidate_build_dir=candidate_build.parent,
            families=["llvm-scalar-chain"],
            scale="smoke",
            run_class="development",
            output_dir=root / "out",
            warmups=0,
            repetitions=1,
        )

    candidate_bad = run(candidate_wrong=True, baseline_wrong=False)
    assert candidate_bad["_collection_failed"] is True
    assert any(cell["correctness"]["baseline"] is True and cell["correctness"]["candidate"] is False for cell in candidate_bad["cells"])
    both_bad = run(candidate_wrong=True, baseline_wrong=True)
    assert any(cell["correctness"] == {"baseline": False, "candidate": False} for cell in both_bad["cells"])


def test_ratio_status_fixture_directions_noise_and_short_samples() -> None:
    # Synthetic Analyzer math fixture; not a real A/A or A/B measurement.
    core = _core()
    baseline = [1.0] * 11
    improved = core.ratio_dimension([0.8] * 11, baseline, left_name="candidate", right_name="baseline")
    unchanged = core.ratio_dimension([1.0] * 11, baseline, left_name="candidate", right_name="baseline")
    regressed = core.ratio_dimension([1.2] * 11, baseline, left_name="candidate", right_name="baseline")
    assert improved["geomean_ratio"] == pytest.approx(0.8)
    assert unchanged["geomean_ratio"] == pytest.approx(1.0)
    assert regressed["geomean_ratio"] == pytest.approx(1.2)
    assert core.classify_paired_status(
        numerator=[0.8] * 11,
        denominator=baseline,
        confidence_interval=improved["confidence_interval"],
        retained_floor_met=True,
        expected_repetitions=11,
    ) == ("improved", "interval_below_one")
    assert core.classify_paired_status(
        numerator=[1.0] * 11,
        denominator=baseline,
        confidence_interval=unchanged["confidence_interval"],
        retained_floor_met=True,
        expected_repetitions=11,
    ) == ("no_detected_change", "interval_contains_one")
    assert core.classify_paired_status(
        numerator=[1.2] * 11,
        denominator=baseline,
        confidence_interval=regressed["confidence_interval"],
        retained_floor_met=True,
        expected_repetitions=11,
    ) == ("regressed", "interval_above_one")
    noisy = [0.2, 4.0, 0.3, 5.0, 0.25, 4.5, 0.2, 3.8, 0.35, 4.2, 0.22]
    noisy_ratio = core.ratio_dimension(noisy, baseline, left_name="candidate", right_name="baseline")
    assert core.classify_paired_status(
        numerator=noisy,
        denominator=baseline,
        confidence_interval=noisy_ratio["confidence_interval"],
        retained_floor_met=True,
        expected_repetitions=11,
    ) == ("inconclusive", "noise_cv")
    short = core.ratio_dimension([0.8, 0.8], [1.0, 1.0], left_name="candidate", right_name="baseline")
    assert core.classify_paired_status(
        numerator=[0.8, 0.8],
        denominator=[1.0, 1.0],
        confidence_interval=short["confidence_interval"],
        retained_floor_met=True,
        expected_repetitions=11,
    ) == ("inconclusive", "insufficient_pairs")
    assert core.classify_paired_status(
        numerator=None,
        denominator=None,
        confidence_interval=None,
        retained_floor_met=False,
        expected_repetitions=11,
    ) == ("unavailable", "samples_missing")


def _fixture_report(analyzer, catalog, cells: list[dict], *, families: list[str]) -> dict:
    expected = [cell["id"] for cell in analyzer.select_cells(catalog, families, "smoke")]
    observed = [cell["id"] for cell in cells]
    failed = [
        cell["id"]
        for cell in cells
        if cell.get("status") != "pass"
        or cell.get("collection_complete") is not True
        or cell.get("correctness") != {"baseline": True, "candidate": True}
    ]
    missing = [cell_id for cell_id in expected if cell_id not in observed]
    incomplete_ids = failed + missing
    report = {
        "schema": analyzer.REPORT_SCHEMA,
        "schema_version": analyzer.REPORT_VERSION,
        "comparison_kind": analyzer.COMPARISON_KIND,
        "catalog_id": catalog["catalog_id"],
        "contract_digest": analyzer._core.contract_digest(catalog),
        "runner_version": analyzer.RUNNER_VERSION,
        "selection": {
            "families": families,
            "scale": "smoke",
            "scale_official": False,
            "requested_cell_ids": expected,
            "observed_cell_ids": observed,
            "incomplete_cell_ids": incomplete_ids,
            "incomplete": [
                {"id": cell_id, "reason": ",".join(next((cell.get("reason_codes") for cell in cells if cell["id"] == cell_id), None) or ["incomplete"])}
                for cell_id in incomplete_ids
            ],
            "subset_of_catalog": True,
        },
        "toolchains": {
            "baseline": {
                "role": "baseline",
                "styio_version": "0.0.1",
                "revision": "a" * 40,
                "dirty": False,
                "traceable": True,
                "artifact_digest": "b" * 64,
                "backend": "native-aot",
                "runtime_binding": "confirmed",
                "runtime_marker": "present",
                "binding_basis": "native_build_probe",
                "native_cxx_version": "18.1.0",
            },
            "candidate": {
                "role": "candidate",
                "styio_version": "0.0.1",
                "revision": "c" * 40,
                "dirty": False,
                "traceable": True,
                "artifact_digest": "d" * 64,
                "backend": "native-aot",
                "runtime_binding": "confirmed",
                "runtime_marker": "present",
                "binding_basis": "native_build_probe",
                "native_cxx_version": "18.1.0",
            },
            "external": {
                "compiler_family": "clang",
                "compiler_version": "18.1.0",
                "present": True,
                "optimization": "O3",
                "lto": False,
                "threads": 1,
                "selection": "path_lookup",
                "artifact_digest": "e" * 64,
                "pinned": True,
                "driver_mode": "clang++",
                "target": "x86_64-unknown-linux-gnu",
            },
            "comparable": True,
            "incomparable_reasons": [],
        },
        "measurement": analyzer.measurement_metadata(3, 11, "development"),
        "capabilities": analyzer.capability_disclosure(catalog, families),
        "cells": cells,
        "collection_complete": not incomplete_ids,
        "performance_claim": "not-evaluated",
        "fixture": FIXTURE_NOTE,
    }
    for role in ("baseline", "candidate"):
        report["toolchains"][role]["native_cxx"] = dict(report["toolchains"]["external"])
        report["toolchains"][role]["native_configuration"] = {
            "optimization": "O3", "lto": "disabled", "target": "compiler-default",
        }
        report["toolchains"][role]["build_configuration"] = {
            "build_type": "Release", "cxx_flags": ["-O3", "-DNDEBUG"], "linker_flags": [],
            "target_architectures": ["compiler-default"], "coverage": "OFF",
        }
    return report


def _math_cell(analyzer, cell_id: str, candidate: float, *, status: str = "pass") -> dict:
    gate = _gate()
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    contract_cell = next(
        cell
        for workload in catalog["workloads"]
        for cell in workload["cells"]
        if cell["id"] == cell_id
    )
    baseline = [1.0] * 11
    values = [candidate] * 11
    time = analyzer._core.ratio_dimension(values, baseline, left_name="candidate", right_name="baseline")
    rss = analyzer._core.ratio_dimension([10.0] * 11, [10.0] * 11, left_name="candidate", right_name="baseline")
    orders = analyzer._core.interleaved_pair_orders(
        cell_id, 11, "retained", left="baseline", right="candidate", runner_version=analyzer.RUNNER_VERSION
    )
    codes = ["AB" if order == ("baseline", "candidate") else "BA" for order in orders]
    annotated = {
        "id": cell_id,
        "family": contract_cell["family"],
        "scale": contract_cell["scale"],
        "route": contract_cell["route"],
        "required": contract_cell["required"],
        "work_units": contract_cell["work_units"],
        "algorithm_id": contract_cell["algorithm_id"],
        "focus_owner": contract_cell["focus_owner"],
        "status": status,
        "correctness": {"baseline": True, "candidate": True},
        "repetitions": 11,
        "warmups": 3,
        "source_digests": contract_cell["source_digest"],
        "input_digest": contract_cell["input_digest"],
        "expected_output_digest": contract_cell["expected_output_digest"],
        "time_samples_s": {"baseline": baseline, "candidate": values},
        "raw_batch_time_samples_s": {"baseline": baseline, "candidate": values},
        "peak_rss_samples_kib": {"baseline": [10.0] * 11, "candidate": [10.0] * 11},
        "time": time,
        "peak_rss": rss,
        "batch": {
            "count": 1,
            "minimum_sample_time_s": 0.5,
            "target_sample_time_s": 0.75,
            "maximum_count": 20000,
            "retained_floor_met": True,
            "equal_work": True,
        },
        "sample_schedule": {
            "strategy": "deterministic-random-interleaving-v1",
            "ab_count": codes.count("AB"),
            "ba_count": codes.count("BA"),
            "schedule_digest": analyzer._core.sha256("".join(codes).encode("ascii")),
        },
        "pair_orders": [f"{first},{second}" for first, second in orders],
        "artifact_bytes": {
            "baseline": 100,
            "candidate": 100,
            "delta": 0,
            "ratio": 1.0,
            "origin": "untimed-native-artifact",
            "comparison_status": "direct_observation",
        },
        "reason_codes": [],
        "time_comparison_status": "no_detected_change" if candidate == 1.0 else ("improved" if candidate < 1 else "regressed"),
        "time_comparison_reason": "interval_contains_one" if candidate == 1.0 else ("interval_below_one" if candidate < 1 else "interval_above_one"),
        "rss_comparison_status": "no_detected_change",
        "rss_comparison_reason": "interval_contains_one",
        "collection_complete": status == "pass",
    }
    del gate
    return annotated


def _scalar_math_cells(analyzer) -> list[dict]:
    return [
        _math_cell(analyzer, "llvm-scalar-chain/smoke/compile-and-run", 1.0),
        _math_cell(analyzer, "llvm-scalar-chain/smoke/native-build", 1.0),
        _math_cell(analyzer, "llvm-scalar-chain/smoke/native-run", 1.0),
    ]


def test_verify_detects_tamper_missing_values_and_privacy(tmp_path: Path) -> None:
    analyzer = _analyzer()
    catalog, _ = _gate().load_catalog(CATALOG_PATH)
    report = _fixture_report(analyzer, catalog, _scalar_math_cells(analyzer), families=["llvm-scalar-chain"])
    verdict = analyzer.verify_report(report, catalog)
    assert verdict["decision"] == "pass"
    tampered = json.loads(json.dumps(report))
    tampered["cells"][0]["time"]["geomean_ratio"] = 0.5
    assert "ratio_mismatch" in analyzer.verify_report(tampered, catalog)["reason_codes"]
    normalized = json.loads(json.dumps(report))
    normalized["cells"][0]["time_samples_s"]["candidate"] = [2.0] * 11
    assert "normalization_mismatch" in analyzer.verify_report(normalized, catalog)["reason_codes"]
    paired = json.loads(json.dumps(report))
    paired["cells"][0]["pair_orders"] = ["candidate,baseline"] * 11
    assert "sample_schedule" in analyzer.verify_report(paired, catalog)["reason_codes"]
    missing = json.loads(json.dumps(report))
    missing["cells"][0]["time_samples_s"] = {"baseline": [1.0], "candidate": None}
    missing["cells"][0]["raw_batch_time_samples_s"] = {"baseline": [1.0], "candidate": [1.0]}
    missing["cells"][0]["status"] = "pass"
    assert analyzer.verify_report(missing, catalog)["reason_codes"]
    private = {"host_name": "workstation", "diagnostic": "/Users/private/source.styio", "stderr": "secret"}
    with pytest.raises(analyzer.PrivacyError):
        analyzer._core.validate_public_report(private)
    with pytest.raises(analyzer.PrivacyError):
        analyzer._core.write_public_json(tmp_path / "bad.json", {"schema": "x", "command": ["styio"]})


def test_failed_and_missing_metrics_do_not_inflate_summary() -> None:
    analyzer = _analyzer()
    catalog, _ = _gate().load_catalog(CATALOG_PATH)
    failed = _math_cell(analyzer, "llvm-scalar-chain/smoke/native-run", 1.0, status="incomplete")
    failed["correctness"] = {"baseline": True, "candidate": False}
    failed["time_samples_s"] = {"baseline": [1.0] * 11, "candidate": []}
    failed.pop("time", None)
    failed["artifact_bytes"] = {"comparison_status": "unavailable", "reason": "artifact_size_missing"}
    failed["collection_complete"] = False
    failed["time_comparison_status"] = "unavailable"
    report = _fixture_report(analyzer, catalog, [failed], families=["llvm-scalar-chain"])
    report["collection_complete"] = False
    summary = analyzer.render_summary(report)
    assert "llvm-scalar-chain/smoke/native-run" in summary
    assert "unavailable" in summary
    assert "Full catalog selected: False" in summary
    assert "not scored" in summary.lower() or "Blocked capabilities" in summary
    assert "performance acceptance" in summary.lower()
    assert "0.0" not in summary.split("ratio=")[0] or "unavailable" in summary


def test_parity_gate_still_exports_shared_sampler_without_copy() -> None:
    gate = _gate()
    core = _core()
    assert gate.calibrate_batch_count([0.001], target_duration_s=0.5) == 500
    assert gate.ratio_dimension([1.0, 2.0, 4.0], [1.0, 1.0, 2.0])["styio_samples"] == [1.0, 2.0, 4.0]


def test_verify_rejects_tampered_median_interval_and_rss_conclusion() -> None:
    analyzer = _analyzer()
    catalog, _ = _gate().load_catalog(CATALOG_PATH)
    report = _fixture_report(analyzer, catalog, _scalar_math_cells(analyzer), families=["llvm-scalar-chain"])
    assert analyzer.verify_report(report, catalog)["decision"] == "pass"
    f1a = json.loads(json.dumps(report))
    f1a["cells"][0]["time"]["candidate_median"] = 0.000001
    f1a["cells"][0]["time"]["confidence_interval"]["lower"] = 0.00001
    f1a["cells"][0]["time"]["confidence_interval"]["upper"] = 0.00002
    codes = analyzer.verify_report(f1a, catalog)["reason_codes"]
    assert "ratio_mismatch" in codes or "interval_mismatch" in codes
    f1b = json.loads(json.dumps(report))
    f1b["cells"][0]["rss_comparison_status"] = "improved"
    f1b["cells"][0]["rss_comparison_reason"] = "interval_below_one"
    f1b["cells"][0]["artifact_bytes"]["delta"] = -999999999
    codes = analyzer.verify_report(f1b, catalog)["reason_codes"]
    assert "rss_status_mismatch" in codes
    assert "artifact_delta_mismatch" in codes
    f1c = json.loads(json.dumps(report))
    f1c["cells"][0].pop("pair_orders")
    assert "sample_schedule" in analyzer.verify_report(f1c, catalog)["reason_codes"]
    honest = json.loads(json.dumps(report))
    honest["cells"][0]["status"] = "incomplete"
    honest["cells"][0]["collection_complete"] = False
    honest["cells"][0]["time_samples_s"] = {"baseline": [], "candidate": []}
    honest["cells"][0]["raw_batch_time_samples_s"] = {"baseline": [], "candidate": []}
    honest["cells"][0]["batch"]["retained_floor_met"] = False
    honest["cells"][0].pop("time", None)
    honest["cells"][0]["time_comparison_status"] = "unavailable"
    honest["cells"][0]["time_comparison_reason"] = "samples_missing"
    honest["cells"][0]["artifact_bytes"] = {"comparison_status": "unavailable", "reason": "artifact_size_missing"}
    honest["cells"][0]["pair_orders"] = []
    honest["cells"][0]["sample_schedule"] = {
        "strategy": "deterministic-random-interleaving-v1",
        "ab_count": 0, "ba_count": 0,
        "schedule_digest": analyzer._core.sha256(b""),
    }
    honest["selection"]["incomplete_cell_ids"] = [honest["cells"][0]["id"]]
    honest["selection"]["incomplete"] = [{"id": honest["cells"][0]["id"], "reason": "incomplete"}]
    honest["collection_complete"] = False
    verdict = analyzer.verify_report(honest, catalog)
    assert verdict["decision"] == "pass"
    assert verdict["collection_complete"] is False


def test_verify_rejects_inflated_coverage_and_accepts_honest_missing_toolchain(tmp_path: Path) -> None:
    analyzer = _analyzer()
    catalog, catalog_path = _gate().load_catalog(CATALOG_PATH)
    report = _fixture_report(analyzer, catalog, _scalar_math_cells(analyzer), families=["llvm-scalar-chain"])
    f2a = json.loads(json.dumps(report))
    f2a["cells"] = []
    f2a["selection"]["requested_cell_ids"] = []
    f2a["selection"]["observed_cell_ids"] = []
    f2a["selection"]["incomplete_cell_ids"] = []
    f2a["selection"]["incomplete"] = []
    f2a["collection_complete"] = True
    verdict = analyzer.verify_report(f2a, catalog)
    assert verdict["decision"] == "fail"
    assert verdict["collection_complete"] is False
    assert "collection_complete_mismatch" in verdict["reason_codes"] or "selection_mismatch" in verdict["reason_codes"]
    f2b = json.loads(json.dumps(report))
    keep = f2b["cells"][0]
    f2b["cells"] = [keep]
    f2b["selection"]["requested_cell_ids"] = [keep["id"]]
    f2b["selection"]["observed_cell_ids"] = [keep["id"]]
    f2b["selection"]["incomplete_cell_ids"] = []
    f2b["selection"]["incomplete"] = []
    f2b["collection_complete"] = True
    verdict = analyzer.verify_report(f2b, catalog)
    assert verdict["decision"] == "fail"
    assert verdict["collection_complete"] is False
    f2c = json.loads(json.dumps(report))
    f2c["cells"][0]["correctness"]["candidate"] = False
    f2c["collection_complete"] = True
    f2c["selection"]["incomplete_cell_ids"] = []
    f2c["selection"]["incomplete"] = []
    verdict = analyzer.verify_report(f2c, catalog)
    assert verdict["decision"] == "fail"
    assert verdict["collection_complete"] is False
    assert verdict["incomplete_cell_count"] >= 1
    f2d = json.loads(json.dumps(report))
    f2d["cells"][0]["work_units"] = 999999
    f2d["cells"][0]["algorithm_id"] = "different-work"
    f2d["cells"][0]["expected_output_digest"] = "0" * 64
    assert "cell_identity_mismatch" in analyzer.verify_report(f2d, catalog)["reason_codes"]
    baseline_root = tmp_path / "baseline-root"
    candidate_root = tmp_path / "candidate-root"
    baseline_build = tmp_path / "baseline-build" / "bin"
    candidate_build = tmp_path / "candidate-build" / "bin"
    for path in (baseline_root, candidate_root, baseline_build, candidate_build):
        path.mkdir(parents=True)
    _write_standin(baseline_build / "styio", role="baseline", log_path=tmp_path / "b.log", output_hex=_oracle_hex())
    honest = analyzer.compare_toolchains(
        catalog,
        catalog_path,
        baseline_root=baseline_root,
        baseline_build_dir=baseline_build.parent,
        candidate_root=candidate_root,
        candidate_build_dir=candidate_build.parent,
        families=["llvm-scalar-chain"],
        scale="smoke",
        run_class="development",
        output_dir=tmp_path / "missing",
        warmups=0,
        repetitions=1,
    )
    verdict = analyzer.verify_report(honest, catalog)
    assert verdict["decision"] == "pass"
    assert verdict["collection_complete"] is False
    assert honest["selection"]["requested_cell_ids"]
    assert honest["selection"]["incomplete_cell_ids"] == honest["selection"]["requested_cell_ids"]


def test_clang_identity_uses_native_cxx_overlay_not_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer = _analyzer()
    catalog, catalog_path = _gate().load_catalog(CATALOG_PATH)
    overlay = tmp_path / "overlay" / "clang++"
    path_clang = tmp_path / "pathbin" / "clang++"
    _write_clang_standin(overlay, "99.0.0", tmp_path / "overlay.log")
    _write_clang_standin(path_clang, "21.0.0", tmp_path / "path.log")
    monkeypatch.setenv("PATH", str(path_clang.parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("STYIO_NATIVE_CXX", str(overlay))
    baseline_root = tmp_path / "baseline-root"
    candidate_root = tmp_path / "candidate-root"
    baseline_build = tmp_path / "baseline-build" / "bin"
    candidate_build = tmp_path / "candidate-build" / "bin"
    for path in (baseline_root, candidate_root, baseline_build, candidate_build):
        path.mkdir(parents=True)
    _plant_runtime(baseline_root)
    _plant_runtime(candidate_root)
    _write_cmake_cache(baseline_build.parent, cxx=path_clang, source_root=baseline_root)
    _write_cmake_cache(candidate_build.parent, cxx=path_clang, source_root=candidate_root)
    _write_standin(baseline_build / "styio", role="baseline", log_path=tmp_path / "b.log", output_hex=_oracle_hex())
    _write_standin(candidate_build / "styio", role="candidate", log_path=tmp_path / "c.log", output_hex=_oracle_hex())
    report = analyzer.compare_toolchains(
        catalog,
        catalog_path,
        baseline_root=baseline_root,
        baseline_build_dir=baseline_build.parent,
        candidate_root=candidate_root,
        candidate_build_dir=candidate_build.parent,
        families=["llvm-scalar-chain"],
        scale="smoke",
        run_class="development",
        output_dir=tmp_path / "overlay-out",
        warmups=0,
        repetitions=1,
    )
    assert report["toolchains"]["external"]["compiler_version"] == "99.0.0"
    assert report["toolchains"]["external"]["selection"] == "native_cxx_overlay"
    assert report["toolchains"]["external"]["artifact_digest"] == analyzer._core.sha256(overlay.read_bytes())
    assert report["toolchains"]["comparable"] is True
    events = _load_events(tmp_path / "b.log") + _load_events(tmp_path / "c.log")
    assert all(item["native_cxx_set"] for item in events if item["op"] == "build")


def test_different_embedded_cxx_is_incomparable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer = _analyzer()
    catalog, catalog_path = _gate().load_catalog(CATALOG_PATH)
    monkeypatch.delenv("STYIO_NATIVE_CXX", raising=False)
    cxx18 = tmp_path / "clang18" / "clang++"
    cxx19 = tmp_path / "clang19" / "clang++"
    _write_clang_standin(cxx18, "18.1.0")
    _write_clang_standin(cxx19, "19.1.0")
    baseline_root = tmp_path / "baseline-root"
    candidate_root = tmp_path / "candidate-root"
    baseline_build = tmp_path / "baseline-build" / "bin"
    candidate_build = tmp_path / "candidate-build" / "bin"
    for path in (baseline_root, candidate_root, baseline_build, candidate_build):
        path.mkdir(parents=True)
    _plant_runtime(baseline_root)
    _plant_runtime(candidate_root)
    _write_cmake_cache(baseline_build.parent, cxx=cxx18, source_root=baseline_root)
    _write_cmake_cache(candidate_build.parent, cxx=cxx19, source_root=candidate_root)
    _write_standin(baseline_build / "styio", role="baseline", log_path=tmp_path / "b.log", output_hex=_oracle_hex())
    _write_standin(candidate_build / "styio", role="candidate", log_path=tmp_path / "c.log", output_hex=_oracle_hex())
    report = analyzer.compare_toolchains(
        catalog,
        catalog_path,
        baseline_root=baseline_root,
        baseline_build_dir=baseline_build.parent,
        candidate_root=candidate_root,
        candidate_build_dir=candidate_build.parent,
        families=["llvm-scalar-chain"],
        scale="smoke",
        run_class="development",
        output_dir=tmp_path / "mismatch-out",
        warmups=0,
        repetitions=1,
    )
    assert report["toolchains"]["comparable"] is False
    assert "external_compiler_mismatch" in report["toolchains"]["incomparable_reasons"]
    assert report["toolchains"]["baseline"]["native_cxx_version"] == "18.1.0"
    assert report["toolchains"]["candidate"]["native_cxx_version"] == "19.1.0"
    assert analyzer.verify_report(report, catalog)["decision"] == "pass"


def test_unconfirmed_runtime_binding_is_incomparable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer = _analyzer()
    catalog, catalog_path = _gate().load_catalog(CATALOG_PATH)
    monkeypatch.delenv("STYIO_NATIVE_CXX", raising=False)
    clang = tmp_path / "clang++"
    _write_clang_standin(clang, "18.1.0")
    monkeypatch.setenv("PATH", str(clang.parent) + os.pathsep + os.environ.get("PATH", ""))
    baseline_root = tmp_path / "baseline-root"
    candidate_root = tmp_path / "candidate-root"
    baseline_build = tmp_path / "baseline-build" / "bin"
    candidate_build = tmp_path / "candidate-build" / "bin"
    for path in (baseline_root, candidate_root, baseline_build, candidate_build):
        path.mkdir(parents=True)
    _write_standin(baseline_build / "styio", role="baseline", log_path=tmp_path / "b.log", output_hex=_oracle_hex())
    _write_standin(candidate_build / "styio", role="candidate", log_path=tmp_path / "c.log", output_hex=_oracle_hex())
    report = analyzer.compare_toolchains(
        catalog,
        catalog_path,
        baseline_root=baseline_root,
        baseline_build_dir=baseline_build.parent,
        candidate_root=candidate_root,
        candidate_build_dir=candidate_build.parent,
        families=["llvm-scalar-chain"],
        scale="smoke",
        run_class="development",
        output_dir=tmp_path / "unbound-out",
        warmups=0,
        repetitions=1,
    )
    assert report["toolchains"]["baseline"]["runtime_binding"] == "unconfirmed"
    assert report["toolchains"]["candidate"]["runtime_binding"] == "unconfirmed"
    assert report["toolchains"]["comparable"] is False
    assert "runtime_binding_unconfirmed" in report["toolchains"]["incomparable_reasons"]
    assert analyzer.verify_report(report, catalog)["decision"] == "pass"


class _FakeSample:
    def __init__(self, elapsed_s: float = 1.0, peak_rss_kib: float = 100.0, returncode: int = 0) -> None:
        self.elapsed_s = elapsed_s
        self.peak_rss_kib = peak_rss_kib
        self.returncode = returncode


def _install_helper(monkeypatch, core, *, fail_on: str, fail_index: int) -> dict[str, int]:
    helper = core.load_rss_helper()

    class ProcessTimedOut(Exception):
        pass

    counts = {"timing": 0, "rss": 0}

    def run_process(*args, **kwargs):
        if kwargs.get("sample_process_tree"):
            counts["rss"] += 1
            if fail_on == "rss" and counts["rss"] == fail_index:
                raise core.ReasonError("audit_rss_failure")
            argv = args[0]
            if "-o" in argv:
                Path(argv[argv.index("-o") + 1]).write_bytes(b"fixture artifact")
            return _FakeSample()
        counts["timing"] += 1
        if fail_on == "retained" and counts["timing"] == fail_index:
            raise core.ReasonError("measured_process_failed")
        return _FakeSample()

    helper.run_process = run_process
    helper.ProcessTimedOut = ProcessTimedOut
    monkeypatch.setattr(core, "load_rss_helper", lambda: helper)
    monkeypatch.setattr(core, "batched_output_matches", lambda *_args, **_kwargs: True)
    return counts


def test_rss_replay_failure_keeps_time_pairs_and_correctness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer = _analyzer()
    core = analyzer._core
    counts = _install_helper(monkeypatch, core, fail_on="rss", fail_index=1)
    catalog, catalog_path = _gate().load_catalog(CATALOG_PATH)
    output_hex = _oracle_hex()
    baseline_root = tmp_path / "baseline-root"
    candidate_root = tmp_path / "candidate-root"
    baseline_build = tmp_path / "baseline-build" / "bin"
    candidate_build = tmp_path / "candidate-build" / "bin"
    for path in (baseline_root, candidate_root, baseline_build, candidate_build):
        path.mkdir(parents=True)
    _write_standin(baseline_build / "styio", role="baseline", log_path=tmp_path / "b.log", output_hex=output_hex)
    _write_standin(candidate_build / "styio", role="candidate", log_path=tmp_path / "c.log", output_hex=output_hex)
    report = analyzer.compare_toolchains(
        catalog,
        catalog_path,
        baseline_root=baseline_root,
        baseline_build_dir=baseline_build.parent,
        candidate_root=candidate_root,
        candidate_build_dir=candidate_build.parent,
        families=["llvm-scalar-chain"],
        scale="smoke",
        run_class="development",
        output_dir=tmp_path / "rss-fail",
        warmups=3,
        repetitions=11,
    )
    first = report["cells"][0]
    assert first["correctness"] == {"baseline": True, "candidate": True}
    assert len(first["time_samples_s"]["baseline"]) == 11
    assert len(first["time_samples_s"]["candidate"]) == 11
    assert first["peak_rss_samples_kib"]["baseline"] == []
    assert first["measurement_stage"] == "rss"
    assert "audit_rss_failure" in first["reason_codes"]
    assert first["status"] == "incomplete"
    assert counts["timing"] == 90
    assert counts["rss"] >= 1
    assert report["collection_complete"] is False
    assert analyzer.verify_report(report, catalog)["decision"] == "pass"


def test_retained_sampling_failure_keeps_completed_pairs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    analyzer = _analyzer()
    core = analyzer._core
    # 2 calibration + 6 warmup + 5 retained calls succeed; the 14th timing call is the
    # second side of the third retained pair, leaving two complete pairs and one unpaired.
    counts = _install_helper(monkeypatch, core, fail_on="retained", fail_index=14)
    catalog, catalog_path = _gate().load_catalog(CATALOG_PATH)
    output_hex = _oracle_hex()
    baseline_root = tmp_path / "baseline-root"
    candidate_root = tmp_path / "candidate-root"
    baseline_build = tmp_path / "baseline-build" / "bin"
    candidate_build = tmp_path / "candidate-build" / "bin"
    for path in (baseline_root, candidate_root, baseline_build, candidate_build):
        path.mkdir(parents=True)
    _write_standin(baseline_build / "styio", role="baseline", log_path=tmp_path / "b.log", output_hex=output_hex)
    _write_standin(candidate_build / "styio", role="candidate", log_path=tmp_path / "c.log", output_hex=output_hex)
    report = analyzer.compare_toolchains(
        catalog,
        catalog_path,
        baseline_root=baseline_root,
        baseline_build_dir=baseline_build.parent,
        candidate_root=candidate_root,
        candidate_build_dir=candidate_build.parent,
        families=["llvm-scalar-chain"],
        scale="smoke",
        run_class="development",
        output_dir=tmp_path / "retained-fail",
        warmups=3,
        repetitions=11,
    )
    first = report["cells"][0]
    assert first["correctness"] == {"baseline": True, "candidate": True}
    assert len(first["time_samples_s"]["baseline"]) == 2
    assert len(first["time_samples_s"]["candidate"]) == 2
    unpaired = first.get("unpaired_time_samples_s") or {}
    assert sum(len(values) for values in unpaired.values()) == 1
    assert first["measurement_stage"] == "retained"
    assert "measured_process_failed" in first["reason_codes"]
    assert "time" not in first
    assert first["time_comparison_status"] in {"unavailable", "inconclusive"}
    assert counts["timing"] >= 14
    assert len(first["pair_orders"]) == 2
    assert sum(map(len, first["unpaired_raw_batch_time_samples_s"].values())) == 1
    assert analyzer.verify_report(report, catalog)["decision"] == "pass"
    broken = json.loads(json.dumps(report))
    broken["cells"][0]["time_samples_s"]["candidate"][0] = 1000000.0
    assert "normalization_mismatch" in analyzer.verify_report(broken, catalog)["reason_codes"]


def test_runtime_probe_observes_embedded_tree_despite_neighbor_cache(tmp_path: Path) -> None:
    analyzer = _analyzer()
    original, relocated = tmp_path / "original", tmp_path / "relocated"
    for root in (original, relocated):
        _plant_runtime(root)
    build = relocated / "build"
    (build / "bin").mkdir(parents=True)
    compiler = build / "bin" / "styio"
    cxx = tmp_path / "clang++"
    _write_clang_standin(cxx, "21.0.0")
    _write_cmake_cache(build, cxx=cxx, source_root=relocated)
    _write_standin(compiler, role="baseline", log_path=tmp_path / "calls.jsonl",
                   output_hex=_oracle_hex(), embedded_root=original)
    source = CATALOG_PATH.parent / "sources" / "llvm-scalar-chain.styio"
    binding = analyzer.runtime_binding_status(relocated, compiler, source, timeout_s=30)
    assert binding["runtime_binding"] == "mismatch"
    assert binding["binding_basis"] == "native_build_probe"
    # Missing metadata also cannot override a live embedded root.
    (build / "CMakeCache.txt").unlink()
    assert analyzer.runtime_binding_status(relocated, compiler, source, timeout_s=30)["runtime_binding"] == "mismatch"
    # Once the embedded tree is unavailable the actual fallback can be observed.
    (original / analyzer.RUNTIME_MARKER).unlink()
    assert analyzer.runtime_binding_status(relocated, compiler, source, timeout_s=30)["runtime_binding"] == "confirmed"
    assert str(tmp_path) not in json.dumps(binding)


@pytest.mark.parametrize("selection", ["overlay", "cmake", "tree", "path"])
def test_native_cxx_alias_survives_resolution_and_real_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    analyzer = _analyzer()
    clang = shutil.which("clang")
    if clang is None:
        pytest.skip("real Clang required for driver alias linking")
    alias = tmp_path / "bin" / "clang++"
    alias.parent.mkdir()
    alias.symlink_to(Path(clang).absolute())
    build = tmp_path / "build"
    compiler = build / "bin" / "styio"
    overlay = {}
    monkeypatch.delenv("STYIO_NATIVE_CXX", raising=False)
    monkeypatch.delenv("STYIO_NATIVE_TOOLCHAIN_ROOT", raising=False)
    if selection == "overlay":
        overlay["STYIO_NATIVE_CXX"] = str(alias)
    elif selection == "cmake":
        _write_cmake_cache(build, cxx=alias, source_root=tmp_path)
    elif selection == "tree":
        overlay["STYIO_NATIVE_TOOLCHAIN_ROOT"] = str(tmp_path)
    else:
        monkeypatch.setenv("PATH", str(alias.parent) + os.pathsep + os.environ.get("PATH", ""))
    resolved, _ = analyzer.resolve_native_cxx(styio_compiler=compiler, build_dir=build, overlay=overlay)
    assert resolved == alias
    child = analyzer.child_env_for_side(resolved)
    source = tmp_path / "driver.cpp"
    source.write_text("#include <iostream>\nint main(){std::cout << 7;}\n")
    output = tmp_path / "driver"
    result = subprocess.run([child["STYIO_NATIVE_CXX"], "-std=c++20", str(source), "-o", str(output)], capture_output=True)
    assert result.returncode == 0
    assert subprocess.run([str(output)], capture_output=True).stdout == b"7"
    assert analyzer.identify_cxx_binary(resolved)["driver_mode"] == "clang++"


def _partial_math_report(analyzer, catalog) -> dict:
    cells = _scalar_math_cells(analyzer)
    cell = cells[0]
    cell["status"] = "incomplete"
    cell["measurement_stage"] = "retained"
    cell["reason_codes"] = ["measured_process_failed"]
    for key in ("time_samples_s", "raw_batch_time_samples_s"):
        cell[key] = {side: values[:2] for side, values in cell[key].items()}
    cell["peak_rss_samples_kib"] = {"baseline": [], "candidate": []}
    cell.pop("time")
    cell.pop("peak_rss")
    orders = cell["pair_orders"]
    side = orders[2].split(",")[0]
    cell["unpaired_time_samples_s"] = {name: [1.0] if name == side else [] for name in ("baseline", "candidate")}
    cell["unpaired_raw_batch_time_samples_s"] = json.loads(json.dumps(cell["unpaired_time_samples_s"]))
    cell["pair_orders"] = orders[:2]
    codes = ["AB" if order == "baseline,candidate" else "BA" for order in orders[:2]]
    cell["sample_schedule"].update(ab_count=codes.count("AB"), ba_count=codes.count("BA"),
                                   schedule_digest=analyzer._core.sha256("".join(codes).encode("ascii")))
    analyzer._annotate_cell(cell, comparable=True)
    return _fixture_report(analyzer, catalog, cells, families=["llvm-scalar-chain"])


@pytest.mark.parametrize("mutation", ["normalization", "orders", "statistics", "unpaired", "rss"])
def test_verify_validates_partial_and_unpaired_evidence(mutation: str) -> None:
    analyzer = _analyzer()
    catalog, _ = _gate().load_catalog(CATALOG_PATH)
    report = _partial_math_report(analyzer, catalog)
    assert analyzer.verify_report(report, catalog)["decision"] == "pass"
    cell = report["cells"][0]
    if mutation == "normalization":
        cell["time_samples_s"]["candidate"][0] = 1000000.0
    elif mutation == "orders":
        cell.pop("pair_orders")
    elif mutation == "statistics":
        cell["time"] = {"candidate_median": 0.000001}
    elif mutation == "unpaired":
        side = next(name for name, values in cell["unpaired_time_samples_s"].items() if values)
        cell["unpaired_raw_batch_time_samples_s"][side][0] = 2.0
    else:
        cell["peak_rss_samples_kib"] = {"baseline": [100.0], "candidate": [-1.0]}
    assert analyzer.verify_report(report, catalog)["decision"] == "fail"


@pytest.mark.parametrize("mutation", ["source", "input", "full_catalog", "excluded", "scope", "configuration", "invalid_selection"])
def test_verify_uses_one_catalog_identity_and_coverage(mutation: str) -> None:
    analyzer = _analyzer()
    catalog, _ = _gate().load_catalog(CATALOG_PATH)
    report = _fixture_report(analyzer, catalog, _scalar_math_cells(analyzer), families=["llvm-scalar-chain"])
    assert analyzer.verify_report(report, catalog)["decision"] == "pass"
    if mutation == "source":
        report["cells"][0]["source_digests"] = {"styio": "0" * 64, "cpp": "0" * 64}
    elif mutation == "input":
        report["cells"][0]["input_digest"] = "0" * 64
    elif mutation == "full_catalog":
        report["capabilities"].update(selected_is_full_catalog=True, selected_family_ids=list(analyzer.catalog_family_ids(catalog)))
    elif mutation == "excluded":
        report["capabilities"]["blocked_cases_scored"] = True
    elif mutation == "scope":
        report["selection"]["subset_of_catalog"] = False
    elif mutation == "configuration":
        report["measurement"]["ratio_direction"] = "baseline_over_candidate"
    else:
        report["selection"]["families"] = [{}]
    assert analyzer.verify_report(report, catalog)["decision"] == "fail"


def test_build_configuration_keeps_custom_values_private(tmp_path: Path) -> None:
    analyzer = _analyzer()
    _write_cmake_cache(tmp_path, cxx=tmp_path / "clang++", source_root=tmp_path)
    assert analyzer.public_build_configuration(tmp_path)["build_type"] == "Release"
    with (tmp_path / "CMakeCache.txt").open("a") as handle:
        handle.write("CMAKE_CXX_FLAGS:STRING=-DPRIVATE_VALUE=synthetic-private-value\n")
    assert analyzer.public_build_configuration(tmp_path) is None
