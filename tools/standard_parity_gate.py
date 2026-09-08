#!/usr/bin/env python3
"""Standards-derived parity-v2 catalog, runner, merger, and strict gate.

This module intentionally keeps the benchmark contract data-driven.  A shard
contains complete cell identities for one family and one labelled scale; the
merge operation accepts only reports from the same contract/toolchain class
with disjoint identities.  Timing never decides correctness: output digests
are validated before a cell can contribute statistics.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]


def _load_measurement_core() -> Any:
    path = Path(__file__).resolve().parent / "measurement_core.py"
    spec = importlib.util.spec_from_file_location("styio_measurement_core", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("measurement_core_missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_measurement_core = _load_measurement_core()
CATALOG_DEFAULT = ROOT / "workloads" / "parity-v2" / "contract.json"
REPORT_SCHEMA = "styio.parity.standard.report.v3"
MERGED_SCHEMA = "styio.parity.standard.merged.v3"
VERDICT_SCHEMA = "styio.parity.standard.verdict.v3"
REPORT_VERSION = 3
RUNNER_VERSION = "standard-parity-gate-3"
ROUTES = ("compile-and-run", "native-build", "native-run")
PRIMARY_ROUTE = "native-run"
SCALES = ("smoke", "development", "reference")
PHASES = ("tokenize", "parse", "semantic-analysis", "lowering", "llvm-emission")
REQUIRED_REPETITIONS = 11
DEFAULT_WARMUPS = 3
MIN_SAMPLE_DURATION_S = 0.5
CALIBRATION_TARGET_DURATION_S = 0.75
MAX_BATCH_COUNT = 20_000
CONFIDENCE_LEVEL = 0.95
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 0x53545949
MAX_CV_PCT = 5.0
MAX_CASE_RATIO = 1.10
MAX_GEOMEAN_RATIO = 1.05
MAX_MEMORY_CASE_RATIO = 1.15
MAX_MEMORY_GEOMEAN_RATIO = 1.10
CPP_FLAGS = ("-std=c++20", "-O3", "-DNDEBUG", "-fno-lto")
CPP_STANDARD = "C++20"
HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
PUBLIC_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,63}$")
URL_RE = re.compile(r"(?:https?|file|ssh|git)://", re.IGNORECASE)
ABS_PATH_RE = re.compile(r"(?:^[A-Za-z]:[\\/]|^/|(?:^|/)(?:Users|home|private|tmp|var|opt|Volumes|mnt)/)", re.IGNORECASE)
SECRET_RE = re.compile(r"(?:-----BEGIN|(?:bearer|basic)\s+|(?:sk|ghp|gho|github_pat|xox[baprs])-|AKIA[0-9A-Z]{12,})", re.IGNORECASE)
FORBIDDEN_KEY_RE = re.compile(
    r"(?:^|[_-])(host(?:name)?|user(?:name)?|machine|environment|env|command|argv|path|dir|cwd|root|exec(?:utable)?_path|build_dir|source_path|file_path|absolute_path|url|uri|endpoint|secret|password|credential|api_key|access_token|authorization|stderr|stdout|stack|trace)(?:$|[_-])",
    re.IGNORECASE,
)


GateError = _measurement_core.ReasonError
PrivacyError = _measurement_core.PrivacyError
_sha256 = _measurement_core.sha256
_canonical_json = _measurement_core.canonical_json
contract_digest = _measurement_core.contract_digest


def _load_generators(catalog_path: Path) -> Any:
    generator_path = catalog_path.parent / "generators.py"
    spec = importlib.util.spec_from_file_location("standard_parity_v2_generators", generator_path)
    if spec is None or spec.loader is None:
        raise GateError("catalog_generator_missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _require(condition: bool, reason: str, message: str = "") -> None:
    if not condition:
        raise GateError(reason, message)


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and HEX_DIGEST.fullmatch(value) is not None


def _relative_source(catalog_path: Path, value: Any) -> Path:
    _require(isinstance(value, str) and value, "catalog_source_path")
    path = Path(value)
    _require(not path.is_absolute() and ".." not in path.parts, "catalog_source_path")
    return catalog_path.parent / path


def _expected_family_generators(generators: Any, family: str) -> tuple[Any, Any]:
    try:
        return generators.INPUT_GENERATORS[family], generators.REFERENCE_GENERATORS[family]
    except (AttributeError, KeyError) as exc:
        raise GateError("catalog_generator_family") from exc


def _validate_cell(cell: Mapping[str, Any], *, family: str, scale: str, route: str, size: int, source_digests: Mapping[str, str], input_digest: str, output_digest: str, required: bool) -> None:
    cid = cell.get("id")
    _require(cid == f"{family}/{scale}/{route}", "catalog_cell_id")
    _require(cell.get("family") == family and cell.get("scale") == scale and cell.get("route") == route, "catalog_cell_identity")
    _require(cell.get("required") is required, "catalog_cell_required")
    _require(cell.get("work_units") == size and isinstance(cell.get("work_unit"), str) and cell["work_unit"], "catalog_cell_work")
    _require(cell.get("source_digest") == dict(source_digests), "catalog_cell_source_digest")
    _require(cell.get("input_digest") == input_digest, "catalog_cell_input_digest")
    _require(cell.get("expected_output_digest") == output_digest and cell.get("reference_output_digest") == output_digest, "catalog_cell_output_digest")
    _require(isinstance(cell.get("algorithm_id"), str) and cell["algorithm_id"], "catalog_cell_algorithm")
    _require(isinstance(cell.get("focus_owner"), str) and cell["focus_owner"], "catalog_cell_focus")
    boundary = cell.get("route_boundary")
    _require(isinstance(boundary, dict) and set(boundary) == {"start", "stop"} and all(isinstance(item, str) and item for item in boundary.values()), "catalog_cell_boundary")
    equivalence = cell.get("equivalence")
    _require(isinstance(equivalence, dict), "catalog_cell_equivalence")
    _require(equivalence.get("thread_count") == 1 and equivalence.get("static_work_units") == size, "catalog_cell_equivalence")
    _require(equivalence.get("input_contract") == "same-canonical-bytes" and equivalence.get("output_contract") == "same-byte-digest", "catalog_cell_equivalence")


def _validate_phase_cell(cell: Mapping[str, Any], *, catalog_path: Path, generators: Any, phase: str, scale: str, target: int, required: bool) -> None:
    cid = f"compiler-phase/{phase}/{scale}"
    _require(cell.get("id") == cid and cell.get("family") == "compiler-phase" and cell.get("phase") == phase, "catalog_phase_cell_id")
    _require(cell.get("scale") == scale and cell.get("required") is required and cell.get("route") == "compile-and-run", "catalog_phase_cell_identity")
    _require(cell.get("work_units") == target and cell.get("work_unit") == "tokens", "catalog_phase_work")
    styio_digest = _sha256(generators.phase_source(target))
    cpp_digest = _sha256(generators.phase_cpp_source(target))
    output_digest = _sha256(generators.phase_reference_output(target))
    _require(cell.get("source_digest") == {"styio": styio_digest, "cpp": cpp_digest}, "catalog_phase_source_digest")
    _require(cell.get("input_digest") == _sha256(b""), "catalog_phase_input_digest")
    _require(cell.get("expected_output_digest") == output_digest and cell.get("reference_output_digest") == output_digest, "catalog_phase_output_digest")
    _require(isinstance(cell.get("algorithm_id"), str) and cell["algorithm_id"].startswith("compiler_phase."), "catalog_phase_algorithm")
    _require(isinstance(cell.get("focus_owner"), str) and cell["focus_owner"], "catalog_phase_focus")
    boundary = cell.get("route_boundary")
    _require(isinstance(boundary, dict) and boundary == {"start": "source-read", "stop": "validated-stdout"}, "catalog_phase_boundary")


def _phase_source_structure(generators: Any, token_target: int) -> dict[str, int]:
    """Count the frozen static binding shape from generated source bytes.

    This parser deliberately lives in the gate rather than trusting metadata
    emitted by the generator.  It rejects a comparator that replaces the
    static dependency chain with a runtime loop while retaining the same
    nominal work-unit count.
    """

    styio = generators.phase_source(token_target).decode("ascii")
    cpp = generators.phase_cpp_source(token_target).decode("ascii")
    token_pattern = re.compile(r"::|[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[^\s]")
    styio_declarations = re.findall(r"(?m)^v[0-9]+\s*=", styio)
    cpp_declarations = re.findall(r"(?m)^\s*std::int64_t\s+v[0-9]+\s*=", cpp)
    styio_binary = re.findall(r"(?m)^v[0-9]+\s*=\s*v[0-9]+\s*\+\s*[0-9]+", styio)
    cpp_binary = re.findall(r"(?m)^\s*std::int64_t\s+v[0-9]+\s*=\s*v[0-9]+\s*\+\s*[0-9]+;", cpp)
    return {
        "work_units": token_target,
        "styio_tokens": len(token_pattern.findall(styio)),
        "cpp_tokens": len(token_pattern.findall(cpp)),
        "styio_declarations": len(styio_declarations),
        "cpp_declarations": len(cpp_declarations),
        "styio_expression_nodes": len(styio_declarations),
        "cpp_expression_nodes": len(cpp_declarations),
        "styio_binary_expression_nodes": len(styio_binary),
        "cpp_binary_expression_nodes": len(cpp_binary),
        "styio_print_nodes": len(re.findall(r"(?m)^>_\(v[0-9]+\)", styio)),
        "cpp_print_nodes": len(re.findall(r"(?m)^std::cout\s*<<\s*v[0-9]+", cpp)),
        "runtime_loop_nodes": len(re.findall(r"\b(?:for|while|do)\b", cpp)),
    }


def validate_catalog(catalog: Mapping[str, Any], catalog_path: Path | None = None) -> None:
    """Validate every source, digest, scale, equivalence, and exclusion rule."""

    if catalog_path is None:
        catalog_path = CATALOG_DEFAULT
    catalog_path = catalog_path.resolve()
    _require(catalog.get("schema") == "styio.parity.catalog.v2", "catalog_schema")
    _require(catalog.get("catalog_id") == "parity-v2" and catalog.get("catalog_version") == 2, "catalog_identity")
    _require(catalog.get("digest_algorithm") == "sha256" and catalog.get("canonical_encoding") == "utf-8", "catalog_digest_contract")
    _require(catalog.get("generator") == "generators.py", "catalog_generator")
    scales = catalog.get("scales")
    _require(isinstance(scales, dict) and tuple(scales) == tuple(sorted(SCALES)), "catalog_scales")
    _require(all(isinstance(scales[s], dict) and scales[s].get("official") is (s == "reference") for s in SCALES), "catalog_scale_labels")
    routes = catalog.get("routes")
    _require(isinstance(routes, list) and tuple(item.get("id") for item in routes) == ROUTES, "catalog_routes")
    for route in routes:
        boundary = route.get("boundary")
        _require(isinstance(boundary, dict) and set(boundary) == {"start", "stop", "timed_region", "artifact_policy"}, "catalog_route_boundary")
    cpp_contract = catalog.get("cpp_contract")
    _require(isinstance(cpp_contract, dict), "catalog_cpp_contract")
    _require(cpp_contract.get("language") == "C++20" and cpp_contract.get("compiler_family") == "clang", "catalog_cpp_compiler")
    _require(cpp_contract.get("optimization") == "-O3" and cpp_contract.get("lto") is False and cpp_contract.get("threads") == 1, "catalog_cpp_strength")
    workloads = catalog.get("workloads")
    generators = _load_generators(catalog_path)
    expected_families = tuple(generators.WORKLOAD_SCALES)
    _require(isinstance(workloads, list) and tuple(item.get("id") for item in workloads) == expected_families, "catalog_workloads")
    all_ids: set[str] = set()
    for workload in workloads:
        family = str(workload["id"])
        _require(workload.get("standard_family") in {"CLBG-Style", "LLVM-TestSuite-Style"}, "catalog_workload_lineage")
        workload_algorithm = workload.get("algorithm_id")
        _require(isinstance(workload_algorithm, str) and workload_algorithm, "catalog_workload_algorithm")
        workload_unit = workload.get("work_unit")
        _require(isinstance(workload_unit, str) and workload_unit, "catalog_workload_unit")
        workload_provenance = workload.get("provenance")
        _require(
            isinstance(workload_provenance, dict)
            and workload_provenance.get("authority") == "Project-Microkernel"
            and workload_provenance.get("official_description") == "not an official suite program; independent project microkernel"
            and isinstance(workload_provenance.get("methodology_url"), str),
            "catalog_workload_provenance",
        )
        input_gen, output_gen = _expected_family_generators(generators, family)
        source = workload.get("source")
        _require(isinstance(source, dict) and set(source) == {"styio", "cpp", "source_digest"}, "catalog_sources")
        source_digests = source["source_digest"]
        _require(isinstance(source_digests, dict) and set(source_digests) == {"styio", "cpp"} and all(_is_digest(v) for v in source_digests.values()), "catalog_source_digests")
        for language in ("styio", "cpp"):
            source_file = _relative_source(catalog_path, source.get(language))
            _require(source_file.is_file() and _sha256(source_file.read_bytes()) == source_digests[language], "catalog_source_digest_mismatch")
        scale_map = workload.get("scales")
        _require(isinstance(scale_map, dict) and tuple(scale_map) == tuple(sorted(SCALES)), "catalog_workload_scales")
        input_spec = workload.get("input")
        output_spec = workload.get("output_oracle")
        _require(isinstance(input_spec, dict) and input_spec.get("encoding") == "ascii-decimal-lines", "catalog_input_spec")
        _require(isinstance(output_spec, dict) and output_spec.get("encoding") == "canonical-stdout", "catalog_output_spec")
        input_digests = input_spec.get("sha256_by_scale")
        output_digests = output_spec.get("sha256_by_scale")
        _require(isinstance(input_digests, dict) and isinstance(output_digests, dict), "catalog_digest_maps")
        cells = workload.get("cells")
        _require(isinstance(cells, list) and len(cells) == len(SCALES) * len(ROUTES), "catalog_workload_cells")
        seen: set[str] = set()
        for scale, size in zip(SCALES, generators.WORKLOAD_SCALES[family]):
            _require(scale_map[scale].get("work_units") == size, "catalog_workload_size")
            expected_input = _sha256(input_gen(size))
            expected_output = _sha256(output_gen(size))
            _require(input_digests.get(scale) == expected_input and output_digests.get(scale) == expected_output, "catalog_generated_digest_mismatch")
            for route in ROUTES:
                cell = next((candidate for candidate in cells if candidate.get("id") == f"{family}/{scale}/{route}"), None)
                _require(isinstance(cell, dict), "catalog_cell_missing")
                _validate_cell(cell, family=family, scale=scale, route=route, size=size, source_digests=source_digests, input_digest=expected_input, output_digest=expected_output, required=scale == "reference")
                _require(cell.get("algorithm_id") == workload_algorithm, "catalog_cell_algorithm_mismatch")
                _require(cell.get("work_unit") == workload_unit, "catalog_cell_work_unit_mismatch")
                _require(cell["id"] not in all_ids, "catalog_duplicate_cell")
                all_ids.add(cell["id"]); seen.add(cell["id"])
        _require(len(seen) == len(cells), "catalog_extra_cell")
    phase = catalog.get("compiler_phase_sweep")
    _require(isinstance(phase, dict) and phase.get("token_unit") == "tokens", "catalog_phase_sweep")
    targets = phase.get("token_targets")
    _require(isinstance(targets, dict) and tuple(targets) == tuple(sorted(SCALES)), "catalog_phase_targets")
    phase_structure = phase.get("static_structure")
    _require(isinstance(phase_structure, dict) and tuple(phase_structure) == tuple(sorted(SCALES)), "catalog_phase_static_structure")
    for scale, target in targets.items():
        expected_structure = generators.phase_static_structure(int(target))
        _require(phase_structure.get(scale) == expected_structure, "catalog_phase_static_structure")
        _require(
            _phase_source_structure(generators, int(target)) == expected_structure,
            "catalog_phase_source_structure",
        )
    phase_desc = phase.get("phases")
    _require(isinstance(phase_desc, list) and tuple(item.get("id") for item in phase_desc) == PHASES, "catalog_phase_descriptors")
    phase_cells = phase.get("cells")
    _require(isinstance(phase_cells, list) and len(phase_cells) == len(PHASES) * len(SCALES), "catalog_phase_cells")
    for descriptor in phase_desc:
        _require(descriptor.get("route") == "compile-and-run" and descriptor.get("boundary") == {"start": "source-read", "stop": "validated-stdout"}, "catalog_phase_descriptor")
    for phase_name in PHASES:
        for scale in SCALES:
            cell = next((candidate for candidate in phase_cells if candidate.get("id") == f"compiler-phase/{phase_name}/{scale}"), None)
            _require(isinstance(cell, dict), "catalog_phase_cell_missing")
            _validate_phase_cell(cell, catalog_path=catalog_path, generators=generators, phase=phase_name, scale=scale, target=int(targets[scale]), required=scale == "reference")
            _require(cell["id"] not in all_ids, "catalog_duplicate_cell")
            all_ids.add(cell["id"])
    unsupported = catalog.get("unsupported_cases")
    _require(isinstance(unsupported, list) and unsupported, "catalog_unsupported")
    for case in unsupported:
        _require(case.get("status") == "capability-blocked" and case.get("aggregate") is False, "catalog_unsupported_policy")
    measurement = catalog.get("measurement_contract")
    _require(isinstance(measurement, dict) and measurement.get("warmups") == DEFAULT_WARMUPS and measurement.get("retained_repetitions") == REQUIRED_REPETITIONS, "catalog_measurement")
    _require(measurement.get("max_cv_pct") == MAX_CV_PCT and measurement.get("required_case_ratio") == MAX_CASE_RATIO and measurement.get("required_geomean_ratio") == MAX_GEOMEAN_RATIO, "catalog_thresholds")
    _require(
        measurement.get("minimum_sample_time_s") == MIN_SAMPLE_DURATION_S
        and measurement.get("minimum_time_scope") == "workload-route-cells"
        and measurement.get("calibration_target_time_s") == CALIBRATION_TARGET_DURATION_S
        and measurement.get("maximum_batch_count") == MAX_BATCH_COUNT
        and measurement.get("pair_order") == "deterministic-random-interleaving-v1",
        "catalog_timing_method",
    )
    confidence = measurement.get("confidence_interval")
    _require(
        isinstance(confidence, dict)
        and confidence.get("level") == CONFIDENCE_LEVEL
        and confidence.get("resamples") == BOOTSTRAP_RESAMPLES
        and confidence.get("method") == "paired-hierarchical-percentile-bootstrap",
        "catalog_confidence_method",
    )
    _require(
        measurement.get("required_memory_case_ratio") == MAX_MEMORY_CASE_RATIO
        and measurement.get("required_memory_geomean_ratio") == MAX_MEMORY_GEOMEAN_RATIO
        and measurement.get("primary_route") == PRIMARY_ROUTE
        and measurement.get("controlled_reference_required") is True,
        "catalog_memory_and_control",
    )
    _require(
        measurement.get("aggregate_policy") == "equal-weight workload cells, separated by scale and route; compiler phases are diagnostic only",
        "catalog_aggregate_policy",
    )
    _require(measurement.get("compiler_phase_policy") == "diagnostic-shared-probe", "catalog_phase_policy")
    provenance = catalog.get("provenance")
    _require(
        isinstance(provenance, dict)
        and provenance.get("standard") == "standards-derived measurement over independent project microkernels"
        and provenance.get("workload_lineage") == "clbg-* and llvm-* identifiers are historical style labels, not claims that official suite programs are included",
        "catalog_provenance_scope",
    )
    serialized = json.dumps(catalog, sort_keys=True)
    _require("cached-jit" not in serialized and "runtime-only" not in serialized and "full-cli" not in serialized, "catalog_retired_route")


def load_catalog(path: str | Path = CATALOG_DEFAULT) -> tuple[dict[str, Any], Path]:
    catalog_path = Path(path).resolve()
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError("catalog_unreadable") from exc
    if not isinstance(catalog, dict):
        raise GateError("catalog_shape")
    validate_catalog(catalog, catalog_path)
    return catalog, catalog_path


def _public_key_allowed(key: str) -> bool:
    return _measurement_core.public_key_allowed(key)


def _public_string_allowed(value: str) -> bool:
    return _measurement_core.public_string_allowed(value)


validate_public_report = _measurement_core.validate_public_report
assert_public_report = _measurement_core.assert_public_report
write_public_json = _measurement_core.write_public_json
median = _measurement_core.median
sample_cv_pct = _measurement_core.sample_cv_pct
geometric_mean = _measurement_core.geometric_mean
_percentile = _measurement_core.percentile
bootstrap_geomean_ratio_ci = _measurement_core.bootstrap_geomean_ratio_ci
paired_log_ratios = _measurement_core.paired_log_ratios
ratio_dimension = _measurement_core.ratio_dimension
calibrate_batch_count = _measurement_core.calibrate_batch_count
normalize_batched_elapsed = _measurement_core.normalize_batched_elapsed


def _interleaved_pair_orders(identity: str, count: int, stage: str) -> tuple[tuple[str, str], ...]:
    return _measurement_core.interleaved_pair_orders(
        identity,
        count,
        stage,
        left="styio",
        right="cpp",
        runner_version=RUNNER_VERSION,
    )


_ratio_dimension = ratio_dimension
coefficient_of_variation_pct = sample_cv_pct
geometric_mean_ratio = geometric_mean


def deterministic_shard(catalog: Mapping[str, Any], family: str, scale: str = "smoke") -> list[dict[str, Any]]:
    if family == "compiler-phase":
        cells = catalog["compiler_phase_sweep"]["cells"]
    else:
        workload = next((item for item in catalog["workloads"] if item.get("id") == family), None)
        if workload is None:
            raise GateError("unknown_family")
        cells = workload["cells"]
    selected = [dict(cell) for cell in cells if scale == "all" or cell.get("scale") == scale]
    if not selected:
        raise GateError("empty_shard")
    return sorted(selected, key=lambda cell: str(cell["id"]))


shard_cells = deterministic_shard


def _catalog_cell_ids(catalog: Mapping[str, Any]) -> set[str]:
    ids = {str(cell["id"]) for workload in catalog.get("workloads", []) for cell in workload.get("cells", [])}
    ids.update(str(cell["id"]) for cell in catalog.get("compiler_phase_sweep", {}).get("cells", []))
    return ids


def _public_version(text: str) -> str:
    match = re.search(r"(?<![0-9])([0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+][A-Za-z0-9.-]+)?)", text)
    if not match or not PUBLIC_VERSION.fullmatch(match.group(1)):
        return "unknown"
    return match.group(1)


def _compiler_strength_violations(source: str) -> list[str]:
    checks = (("weak-optimization", ("-O0", "-O1", "-O2", "-Og")), ("lto-enabled", ("-flto",)), ("sync-streams", ("std::endl", "ios::sync_with_stdio(true)")), ("threaded-baseline", ("std::thread", "#pragma omp")))
    return [code for code, needles in checks if any(needle in source for needle in needles)]


def validate_cpp_strength(catalog: Mapping[str, Any], catalog_path: Path | None = None) -> dict[str, Any]:
    if catalog_path is None:
        catalog_path = CATALOG_DEFAULT
    violations: list[dict[str, str]] = []
    for workload in catalog.get("workloads", []):
        source = workload.get("source", {})
        cpp_path = _relative_source(catalog_path, source.get("cpp"))
        try:
            text = cpp_path.read_text(encoding="utf-8")
        except OSError:
            violations.append({"family": str(workload.get("id")), "reason": "cpp_source_missing"})
            continue
        for reason in _compiler_strength_violations(text):
            violations.append({"family": str(workload.get("id")), "reason": reason})
    result = {"schema": "styio.parity.cpp-strength.v2", "language": "C++20", "flags": ["-O3", "-DNDEBUG", "-fno-lto"], "threads": 1, "violations": violations, "pass": not violations}
    return result


audit_cpp_strength = validate_cpp_strength
check_cpp_strength = validate_cpp_strength
strongest_cpp_contract = validate_cpp_strength


def _toolchain(styio_root: Path, build_dir: Path, styio_executable: Path | None = None) -> tuple[Path, Path, dict[str, Any]]:
    if styio_executable is None:
        for candidate in (build_dir / "bin" / "styio", build_dir / "styio", styio_root / "build" / "bin" / "styio"):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                styio_executable = candidate
                break
    if styio_executable is None:
        raise GateError("styio_compiler_missing")
    cxx = shutil.which("clang++") or shutil.which("clang")
    if not cxx:
        raise GateError("clang_toolchain_missing")
    version_proc = subprocess.run([cxx, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=10)
    text = (version_proc.stdout + version_proc.stderr).decode("utf-8", errors="replace")
    if "clang" not in text.lower():
        raise GateError("clang_toolchain_required")
    styio_version_proc = subprocess.run([str(styio_executable), "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=10)
    styio_version_text = (styio_version_proc.stdout + styio_version_proc.stderr).decode("utf-8", errors="replace")
    phase_probe = build_dir / "bin" / "styio_soak_test"
    return Path(styio_executable), Path(cxx), {
        "styio_version": _public_version(styio_version_text),
        "styio_backend": "native-aot",
        "compiler_family": "clang",
        "compiler_version": _public_version(text),
        "target_class": "portable",
        "optimization": "O3",
        "lto": False,
        "threads": 1,
        "runner": RUNNER_VERSION,
        "phase_probe_available": bool(phase_probe.is_file() and os.access(phase_probe, os.X_OK)),
    }


def _run(command: Sequence[str], *, cwd: Path, input_bytes: bytes = b"", timeout_s: float = 300.0) -> tuple[float, int, bytes, bytes]:
    start = time.perf_counter()
    try:
        proc = subprocess.run(list(command), cwd=str(cwd), input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise GateError("timed_out") from exc
    elapsed = max(time.perf_counter() - start, 1e-9)
    return elapsed, int(proc.returncode), bytes(proc.stdout), bytes(proc.stderr)


def _source_pair(catalog_path: Path, cell: Mapping[str, Any], generators: Any) -> tuple[Path, Path, bytes]:
    family = str(cell["family"])
    size = int(cell["work_units"])
    workload = next(item for item in json.loads(catalog_path.read_text(encoding="utf-8"))["workloads"] if item["id"] == family)
    styio = catalog_path.parent / workload["source"]["styio"]
    cpp = catalog_path.parent / workload["source"]["cpp"]
    input_bytes = generators.input_for(family, size)
    return styio, cpp, input_bytes


def _compile_command(executable: Path, source: Path, artifact: Path, *, styio: bool) -> list[str]:
    if styio:
        return [str(executable), "build", str(source), "-o", str(artifact)]
    return [str(executable), "-std=c++20", "-O3", "-DNDEBUG", "-fno-lto", str(source), "-o", str(artifact)]


def _digest_output(output: bytes) -> str:
    return _sha256(output)


def _load_rss_helper() -> Any:
    return _measurement_core.load_rss_helper()


PHASE_TIER_NAMES = {scale: scale for scale in SCALES}
PHASE_FOCUS_NAMES = {
    "lexer": "tokenize",
    "parser": "parse",
    "type": "semantic-analysis",
    "lower": "lowering",
    "llvm": "llvm-emission",
}


def _phase_probe_path(build_dir: Path) -> Path | None:
    candidate = build_dir / "bin" / "styio_soak_test"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def _parse_phase_probe_output(text: str) -> dict[str, float]:
    """Parse the five numeric phase buckets from one complete Styio probe."""

    values: dict[str, float] = {}
    for line in text.splitlines():
        focus = re.search(r"(?:^|\s)focus=([A-Za-z0-9_-]+)", line)
        duration = re.search(r"(?:^|\s)focus_us=([0-9]+(?:\.[0-9]+)?)", line)
        if focus is None or duration is None:
            continue
        phase = PHASE_FOCUS_NAMES.get(focus.group(1))
        if phase is None:
            continue
        value = float(duration.group(1)) / 1_000_000.0
        if not math.isfinite(value) or value <= 0:
            raise GateError("phase_probe_malformed")
        if phase in values:
            raise GateError("phase_probe_duplicate_bucket")
        values[phase] = value
    # Keep compatibility with the compact probe line used by the earlier
    # soak binary while preferring the five independent micro-bench records.
    if len(values) != len(PHASES):
        line = next((item for item in text.splitlines() if item.startswith("[parity-phase]")), "")
        if line:
            keys = {
                "tokenize": "tokenize_us",
                "parse": "parse_us",
                "semantic-analysis": "semantic_analysis_us",
                "lowering": "lowering_us",
                "llvm-emission": "llvm_emission_us",
            }
            values = {}
            for phase, key in keys.items():
                match = re.search(rf"(?:^|\s){re.escape(key)}=([0-9]+(?:\.[0-9]+)?)", line)
                if match is None:
                    raise GateError("phase_probe_malformed")
                value = float(match.group(1)) / 1_000_000.0
                if not math.isfinite(value) or value <= 0:
                    raise GateError("phase_probe_malformed")
                values[phase] = value
    if tuple(sorted(values)) != tuple(sorted(PHASES)):
        raise GateError("phase_probe_malformed")
    return values


def _phase_trace_events(trace_path: Path) -> list[tuple[str, float]]:
    try:
        payload = json.loads(trace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError("phase_trace_malformed") from exc
    events = payload.get("traceEvents") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise GateError("phase_trace_malformed")
    parsed: list[tuple[str, float]] = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("name"), str):
            continue
        duration = event.get("dur")
        if isinstance(duration, (int, float)) and math.isfinite(float(duration)) and float(duration) > 0:
            parsed.append((str(event["name"]), float(duration) / 1_000_000.0))
    if not parsed:
        raise GateError("phase_trace_empty")
    return parsed


def _classify_clang_phase(events: Sequence[tuple[str, float]]) -> dict[str, float]:
    """Classify one Clang time-trace into the five contract buckets."""

    # Prefer Clang's aggregate ``Total ...`` records to avoid counting nested
    # trace events more than once.  Older Clang traces may not provide them.
    aggregate = [(name[6:], duration) for name, duration in events if name.startswith("Total ")]
    source = aggregate or list(events)
    buckets = {phase: 0.0 for phase in PHASES}
    for name, duration in source:
        lower = name.lower()
        if "codegen" in lower or "codegenpass" in lower or "backend" in lower or "emit" in lower:
            buckets["llvm-emission"] += duration
        elif "opt" in lower or "pass" in lower or "optimizer" in lower:
            buckets["lowering"] += duration
        elif "sema" in lower or "semantic" in lower or "instantiate" in lower or "type" in lower:
            buckets["semantic-analysis"] += duration
        elif "parse" in lower or "decl" in lower or "preprocess" in lower:
            buckets["parse"] += duration
        elif "frontend" in lower or "lex" in lower or "source" in lower:
            buckets["tokenize"] += duration
    total = sum(duration for _, duration in source)
    frontend = sum(duration for name, duration in source if "frontend" in name.lower())
    backend = sum(duration for name, duration in source if "backend" in name.lower())
    fallbacks = {
        "tokenize": frontend / 3.0 if frontend > 0 else total / 15.0,
        "parse": frontend / 3.0 if frontend > 0 else total / 15.0,
        "semantic-analysis": frontend / 3.0 if frontend > 0 else total / 15.0,
        "lowering": backend / 2.0 if backend > 0 else total / 10.0,
        "llvm-emission": backend / 2.0 if backend > 0 else total / 10.0,
    }
    for phase in PHASES:
        if buckets[phase] <= 0 or not math.isfinite(buckets[phase]):
            buckets[phase] = max(fallbacks[phase], 1e-9)
    return buckets


def _run_phase_probe_once(*, helper: Any, probe: Path, styio_root: Path, tier: str, output: Path, timeout_s: float) -> tuple[dict[str, float], float]:
    env = os.environ.copy()
    env["STYIO_PARITY_SWEEP_TIER"] = tier
    env["GTEST_COLOR"] = "0"
    sample = helper.run_process(
        [str(probe), "--gtest_filter=StyioSoakSingleThread.ParityPhaseSweepReport"],
        cwd=styio_root,
        stdout_path=output,
        timeout_s=timeout_s,
        env=env,
        sample_process_tree=True,
    )
    try:
        text = output.read_text(encoding="utf-8", errors="replace")
    finally:
        output.unlink(missing_ok=True)
    if sample.returncode != 0:
        raise GateError("phase_probe_failed")
    return _parse_phase_probe_output(text), max(float(sample.peak_rss_kib), 0.001)


def _run_clang_phase_once(*, helper: Any, cxx: Path, cwd: Path, source: Path, artifact: Path, timeout_s: float) -> tuple[dict[str, float], float]:
    trace = artifact.with_suffix(".json")
    command = [str(cxx), *CPP_FLAGS, "-ftime-trace", "-c", str(source), "-o", str(artifact)]
    sample = helper.run_process(command, cwd=cwd, timeout_s=timeout_s, sample_process_tree=True)
    if sample.returncode != 0:
        artifact.unlink(missing_ok=True)
        trace.unlink(missing_ok=True)
        raise GateError("phase_trace_failed")
    try:
        buckets = _classify_clang_phase(_phase_trace_events(trace))
    finally:
        artifact.unlink(missing_ok=True)
        trace.unlink(missing_ok=True)
    return buckets, max(float(sample.peak_rss_kib), 0.001)


def _collect_phase_evidence(*, build_dir: Path, styio_root: Path, cxx: Path, source_styio: Path, source_cpp: Path, scale: str, work_units: int, work_root: Path, warmups: int, repetitions: int, timeout_s: float) -> dict[str, Any]:
    probe = _phase_probe_path(build_dir)
    if probe is None:
        return {"error": "phase_probe_missing"}
    tier = PHASE_TIER_NAMES.get(scale)
    if tier is None:
        return {"error": "phase_scale_unknown"}
    helper = _load_rss_helper()
    styio_times = {phase: [] for phase in PHASES}
    cpp_times = {phase: [] for phase in PHASES}
    styio_rss: list[float] = []
    cpp_rss: list[float] = []
    try:
        def collect(implementation: str, label: str, index: int) -> None:
            if implementation == "styio":
                values, rss = _run_phase_probe_once(
                    helper=helper,
                    probe=probe,
                    styio_root=styio_root,
                    tier=tier,
                    output=work_root / f"phase-probe-{label}-{index}.out",
                    timeout_s=timeout_s,
                )
                if label == "retained":
                    for phase in PHASES:
                        styio_times[phase].append(values[phase])
                    styio_rss.append(rss)
            else:
                values, rss = _run_clang_phase_once(
                    helper=helper,
                    cxx=cxx,
                    cwd=styio_root,
                    source=source_cpp,
                    artifact=work_root / f"phase-clang-{label}-{index}.o",
                    timeout_s=timeout_s,
                )
                if label == "retained":
                    for phase in PHASES:
                        cpp_times[phase].append(values[phase])
                    cpp_rss.append(rss)

        schedule_identity = f"compiler-phase/{scale}"
        for index, order in enumerate(_interleaved_pair_orders(schedule_identity, warmups, "phase-warmup")):
            for implementation in order:
                collect(implementation, "warmup", index)
        for index, order in enumerate(_interleaved_pair_orders(schedule_identity, repetitions, "phase-retained")):
            for implementation in order:
                collect(implementation, "retained", index)
    except (GateError, OSError, ValueError) as exc:
        return {
            "error": exc.reason_code if isinstance(exc, GateError) else "phase_measurement_failed",
            "styio_time_s": styio_times,
            "cpp_time_s": cpp_times,
            "styio_peak_rss_kib": styio_rss,
            "cpp_peak_rss_kib": cpp_rss,
        }
    return {
        "styio_time_s": styio_times,
        "cpp_time_s": cpp_times,
        "styio_peak_rss_kib": styio_rss,
        "cpp_peak_rss_kib": cpp_rss,
        "sample_count": repetitions,
        "warmups": warmups,
        "shared_work_units": int(work_units),
        "static_structure": {
            "styio": {"source_digest": _sha256(source_styio.read_bytes()), "source_bytes": source_styio.stat().st_size, "language": "styio"},
            "cpp": {"source_digest": _sha256(source_cpp.read_bytes()), "source_bytes": source_cpp.stat().st_size, "language": "C++20"},
        },
    }


def _phase_record(cell: Mapping[str, Any], *, evidence: Mapping[str, Any], correctness: Mapping[str, bool], warmups: int, repetitions: int) -> dict[str, Any]:
    phase = str(cell.get("phase"))
    styio_samples = list(evidence.get("styio_time_s", {}).get(phase, [])) if isinstance(evidence.get("styio_time_s"), Mapping) else []
    cpp_samples = list(evidence.get("cpp_time_s", {}).get(phase, [])) if isinstance(evidence.get("cpp_time_s"), Mapping) else []
    styio_rss = list(evidence.get("styio_peak_rss_kib", []))
    cpp_rss = list(evidence.get("cpp_peak_rss_kib", []))
    reasons = [str(evidence["error"])] if isinstance(evidence.get("error"), str) else []
    if len(styio_samples) != repetitions or len(cpp_samples) != repetitions:
        reasons.append("missing_phase_samples")
    if len(styio_rss) != repetitions or len(cpp_rss) != repetitions:
        reasons.append("missing_phase_rss")
    if any(not isinstance(value, (int, float)) or value <= 0 or not math.isfinite(float(value)) for value in (*styio_samples, *cpp_samples)):
        reasons.append("malformed_phase_samples")
    if correctness != {"styio": True, "cpp": True}:
        reasons.append("correctness_failed")
    status = "pass" if not reasons else "incomplete"
    structure = evidence.get("static_structure", {})
    record: dict[str, Any] = {
        "id": cell["id"],
        "family": cell["family"],
        "scale": cell["scale"],
        "route": cell["route"],
        "required": bool(cell.get("required")),
        "work_units": cell["work_units"],
        "algorithm_id": cell.get("algorithm_id"),
        "focus_owner": cell.get("focus_owner"),
        "phase": phase,
        "score_eligible": False,
        "status": status,
        "correctness": dict(correctness),
        "repetitions": repetitions,
        "warmups": warmups,
        "source_digests": cell.get("source_digest"),
        "input_digest": cell.get("input_digest"),
        "expected_output_digest": cell.get("expected_output_digest"),
        "time_samples_s": {"styio": styio_samples, "cpp": cpp_samples},
        "peak_rss_samples_kib": {"styio": styio_rss, "cpp": cpp_rss},
        "reason_codes": sorted(set(reasons)),
        "phase_provenance": {
            "timing_boundary": "one-complete-probe-per-sample",
            "shared_sample_count": repetitions,
            "styio": {"probe": "styio_soak_test.ParityPhaseSweepReport", "bucket_source": "focus_us"},
            "cpp": {"timing_source": "clang-ftime-trace", "bucket_source": "traceEvents", "flags": list(CPP_FLAGS)},
            "shared_work_units": cell["work_units"],
            "static_structure": structure,
        },
    }
    if status == "pass":
        record["time"] = ratio_dimension(styio_samples, cpp_samples)
        record["peak_rss"] = ratio_dimension(styio_rss, cpp_rss)
        record["throughput"] = ratio_dimension(styio_samples, cpp_samples, reciprocal=True)
    return record


def _measure_phase_shard(cells: Sequence[Mapping[str, Any]], *, catalog_path: Path, styio: Path, cxx: Path, styio_root: Path, build_dir: Path, warmups: int, repetitions: int, timeout_s: float) -> list[dict[str, Any]]:
    if not cells:
        raise GateError("empty_phase_shard")
    generators = _load_generators(catalog_path)
    size = int(cells[0]["work_units"])
    source_styio_bytes = generators.phase_source(size)
    source_cpp_bytes = generators.phase_cpp_source(size)
    expected = str(cells[0]["expected_output_digest"])
    with tempfile.TemporaryDirectory(prefix="standard-parity-phase-shard-") as raw:
        work_root = Path(raw)
        source_styio = work_root / "phase.styio"
        source_cpp = work_root / "phase.cpp"
        source_styio.write_bytes(source_styio_bytes)
        source_cpp.write_bytes(source_cpp_bytes)
        evidence = _collect_phase_evidence(
            build_dir=build_dir,
            styio_root=styio_root,
            cxx=cxx,
            source_styio=source_styio,
            source_cpp=source_cpp,
            scale=str(cells[0]["scale"]),
            work_units=size,
            work_root=work_root,
            warmups=warmups,
            repetitions=repetitions,
            timeout_s=timeout_s,
        )
        input_path = work_root / "input.bin"
        input_path.write_bytes(b"")
        correctness = {"styio": False, "cpp": False}
        for implementation, executable, source in (("styio", styio, source_styio), ("cpp", cxx, source_cpp)):
            artifact = work_root / f"preflight-{implementation}.bin"
            elapsed, code, output, _ = _run(_compile_command(executable, source, artifact, styio=implementation == "styio"), cwd=styio_root, timeout_s=timeout_s)
            if code == 0 and artifact.is_file():
                _, run_code, run_output, _ = _run([str(artifact)], cwd=styio_root, input_bytes=b"", timeout_s=timeout_s)
                correctness[implementation] = run_code == 0 and _digest_output(run_output) == expected
            artifact.unlink(missing_ok=True)
        if "static_structure" not in evidence:
            evidence["static_structure"] = {
                "styio": {"source_digest": _sha256(source_styio_bytes), "source_bytes": len(source_styio_bytes), "language": "styio"},
                "cpp": {"source_digest": _sha256(source_cpp_bytes), "source_bytes": len(source_cpp_bytes), "language": "C++20"},
            }
        return [_phase_record(cell, evidence=evidence, correctness=correctness, warmups=warmups, repetitions=repetitions) for cell in cells]


def _batched_output_matches(output: Path, expected: bytes, count: int, *, repeated: bool) -> bool:
    return _measurement_core.batched_output_matches(output, expected, count, repeated=repeated)


def _incomplete_cell(
    cell: Mapping[str, Any],
    *,
    correctness: Mapping[str, bool],
    warmups: int,
    repetitions: int,
    reason: str,
) -> dict[str, Any]:
    record = _measurement_core.incomplete_cell(
        cell,
        left_name="styio",
        right_name="cpp",
        correctness=correctness,
        warmups=warmups,
        repetitions=repetitions,
        reason=reason,
    )
    record.pop("side_names", None)
    return record


def _measure_cell(cell: Mapping[str, Any], *, catalog: Mapping[str, Any], catalog_path: Path, styio: Path, cxx: Path, styio_root: Path, warmups: int, repetitions: int, timeout_s: float) -> dict[str, Any]:
    del catalog
    generators = _load_generators(catalog_path)
    styio_source, cpp_source, input_bytes = _source_pair(catalog_path, cell, generators)
    expected_digest = str(cell["expected_output_digest"])
    expected_output = generators.reference_for(str(cell["family"]), int(cell["work_units"]))
    left = _measurement_core.SideSpec("styio", styio, styio_source, styio_root, True)
    right = _measurement_core.SideSpec("cpp", cxx, cpp_source, styio_root, False)
    try:
        record = _measurement_core.measure_paired_cell(
            cell,
            left=left,
            right=right,
            input_bytes=input_bytes,
            expected_output=expected_output,
            expected_digest=expected_digest,
            warmups=warmups,
            repetitions=repetitions,
            timeout_s=timeout_s,
            runner_version=RUNNER_VERSION,
            ratio_numerator="styio",
            ratio_denominator="cpp",
        )
    except _measurement_core.ReasonError as exc:
        raise GateError(exc.reason_code) from exc
    record.pop("side_names", None)
    return record


def _capability_disclosure(catalog: Mapping[str, Any]) -> dict[str, Any]:
    supported = sorted(str(workload["id"]) for workload in catalog.get("workloads", []))
    blocked = sorted(str(case["id"]) for case in catalog.get("unsupported_cases", []))
    return {
        "supported_family_count": len(supported),
        "supported_family_ids": supported,
        "blocked_case_count": len(blocked),
        "blocked_case_ids": blocked,
        "blocked_cases_scored": False,
    }


def _aggregate_dimension(cells: Sequence[Mapping[str, Any]], sample_key: str) -> dict[str, Any]:
    groups: list[list[float]] = []
    for cell in cells:
        samples = cell.get(sample_key)
        if not isinstance(samples, Mapping):
            continue
        styio = samples.get("styio")
        cpp = samples.get("cpp")
        if not isinstance(styio, list) or not isinstance(cpp, list):
            continue
        try:
            groups.append(paired_log_ratios(styio, cpp))
        except (TypeError, ValueError):
            continue
    if not groups:
        return {"cell_count": 0}
    case_ratios = [math.exp(statistics.fmean(group)) for group in groups]
    return {
        "cell_count": len(groups),
        "geomean_ratio": math.exp(statistics.fmean(math.log(value) for value in case_ratios)),
        "max_case_ratio": max(case_ratios),
        "confidence_interval": bootstrap_geomean_ratio_ci(groups),
    }


def report_aggregates(cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build route-separated summaries; never mix phases, scales, or routes."""

    scales: dict[str, Any] = {}
    for scale in SCALES:
        routes: dict[str, Any] = {}
        for route in ROUTES:
            selected = [
                cell
                for cell in cells
                if cell.get("family") != "compiler-phase"
                and cell.get("scale") == scale
                and cell.get("route") == route
                and cell.get("status") == "pass"
                and cell.get("correctness") == {"styio": True, "cpp": True}
            ]
            if selected:
                routes[route] = {
                    "time": _aggregate_dimension(selected, "time_samples_s"),
                    "peak_rss": _aggregate_dimension(selected, "peak_rss_samples_kib"),
                }
        if routes:
            scales[scale] = {"official": scale == "reference", "routes": routes}
    phase_count = sum(1 for cell in cells if cell.get("family") == "compiler-phase")
    return {
        "policy": "equal-weight-cells-separated-by-scale-and-route",
        "primary_route": PRIMARY_ROUTE,
        "scales": scales,
        "compiler_phases": {"diagnostic_only": True, "cell_count": phase_count},
        "mixed_headline_score": False,
    }


def _measurement_metadata(warmups: int, repetitions: int, run_class: str) -> dict[str, Any]:
    return {
        "warmups": warmups,
        "repetitions": repetitions,
        "minimum_sample_time_s": MIN_SAMPLE_DURATION_S,
        "minimum_time_scope": "workload-route-cells",
        "calibration_target_time_s": CALIBRATION_TARGET_DURATION_S,
        "maximum_batch_count": MAX_BATCH_COUNT,
        "max_cv_pct": MAX_CV_PCT,
        "observer_free_timing": True,
        "isolated_memory_replay": True,
        "sample_policy": "retain-all",
        "pair_order": "deterministic-random-interleaving-v1",
        "compiler_phase_policy": "diagnostic-shared-probe",
        "confidence_interval": {
            "level": CONFIDENCE_LEVEL,
            "method": "paired-hierarchical-percentile-bootstrap",
            "resamples": BOOTSTRAP_RESAMPLES,
        },
        "run_control_class": run_class,
    }


def run_shard(catalog: Mapping[str, Any], catalog_path: Path, *, family: str, scale: str, styio_root: Path, build_dir: Path, output_dir: Path, warmups: int = DEFAULT_WARMUPS, repetitions: int = REQUIRED_REPETITIONS, timeout_s: float = 300.0, run_class: str = "development") -> dict[str, Any]:
    _require(scale in (*SCALES, "all"), "unknown_scale")
    _require(warmups >= 0 and repetitions > 0, "invalid_repetition_budget")
    _require(run_class in {"development", "controlled"}, "invalid_run_control")
    styio, cxx, toolchain = _toolchain(styio_root, build_dir)
    cells = deterministic_shard(catalog, family, scale)
    if family == "compiler-phase":
        report_cells = _measure_phase_shard(
            cells,
            catalog_path=catalog_path,
            styio=styio,
            cxx=cxx,
            styio_root=styio_root,
            build_dir=build_dir,
            warmups=warmups,
            repetitions=repetitions,
            timeout_s=timeout_s,
        )
    else:
        report_cells = [_measure_cell(cell, catalog=catalog, catalog_path=catalog_path, styio=styio, cxx=cxx, styio_root=styio_root, warmups=warmups, repetitions=repetitions, timeout_s=timeout_s) for cell in cells]
    report = {
        "schema": REPORT_SCHEMA,
        "schema_version": REPORT_VERSION,
        "catalog_id": catalog["catalog_id"],
        "contract_digest": contract_digest(catalog),
        "runner_version": RUNNER_VERSION,
        "selection": {
            "family": family,
            "scale": scale,
            "scale_classification": "all-labelled-scales" if scale == "all" else catalog["scales"][scale]["label"],
            "cell_ids": [cell["id"] for cell in cells],
        },
        "toolchain": toolchain,
        "measurement": _measurement_metadata(warmups, repetitions, run_class),
        "capabilities": _capability_disclosure(catalog),
        "aggregates": report_aggregates(report_cells),
        "cells": report_cells,
        "diagnostics": [],
    }
    write_public_json(output_dir / "results.json", report)
    return report


def merge_reports(catalog: Mapping[str, Any], report_paths: Iterable[Path], output_dir: Path) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for report_path in report_paths:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GateError("report_unreadable") from exc
        assert_public_report(report)
        _require(
            report.get("schema") == REPORT_SCHEMA
            and report.get("schema_version") == REPORT_VERSION
            and report.get("runner_version") == RUNNER_VERSION
            and report.get("contract_digest") == contract_digest(catalog),
            "merge_contract_mismatch",
        )
        selected_ids = report.get("selection", {}).get("cell_ids") if isinstance(report.get("selection"), Mapping) else None
        report_ids = [str(cell.get("id")) for cell in report.get("cells", [])]
        _require(isinstance(selected_ids, list) and set(selected_ids) == set(report_ids), "merge_selection_mismatch")
        _require(set(report_ids).issubset(_catalog_cell_ids(catalog)), "merge_unknown_cell")
        reports.append(report)
    _require(reports, "merge_empty")
    base_toolchain = reports[0].get("toolchain")
    base_measurement = reports[0].get("measurement")
    expected_capabilities = _capability_disclosure(catalog)
    all_cells: list[dict[str, Any]] = []; identities: set[str] = set()
    for report in reports:
        _require(
            report.get("toolchain") == base_toolchain
            and report.get("measurement") == base_measurement
            and report.get("capabilities") == expected_capabilities,
            "merge_environment_mismatch",
        )
        for cell in report.get("cells", []):
            identity = str(cell.get("id")); _require(identity not in identities, "merge_duplicate_cell")
            identities.add(identity); all_cells.append(cell)
    sorted_cells = sorted(all_cells, key=lambda cell: str(cell.get("id")))
    merged = {
        "schema": MERGED_SCHEMA,
        "schema_version": REPORT_VERSION,
        "catalog_id": catalog["catalog_id"],
        "contract_digest": contract_digest(catalog),
        "runner_version": RUNNER_VERSION,
        "toolchain": base_toolchain,
        "measurement": base_measurement,
        "capabilities": expected_capabilities,
        "aggregates": report_aggregates(sorted_cells),
        "shards": len(reports),
        "selection": {"cell_ids": sorted(identities)},
        "cells": sorted_cells,
        "diagnostics": [],
    }
    write_public_json(output_dir / "results.json", merged)
    return merged


def _required_ids(catalog: Mapping[str, Any], *, scale: str = "reference") -> set[str]:
    ids = {cell["id"] for workload in catalog["workloads"] for cell in workload["cells"] if cell.get("scale") == scale and cell.get("required")}
    ids.update(cell["id"] for cell in catalog["compiler_phase_sweep"]["cells"] if cell.get("scale") == scale and cell.get("required"))
    return ids


def _catalog_cells_by_id(catalog: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    cells = {
        str(cell["id"]): cell
        for workload in catalog.get("workloads", [])
        for cell in workload.get("cells", [])
    }
    cells.update(
        {str(cell["id"]): cell for cell in catalog.get("compiler_phase_sweep", {}).get("cells", [])}
    )
    return cells


def _positive_samples(value: Any, repetitions: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != repetitions:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) or float(item) <= 0 or not math.isfinite(float(item)) for item in value):
        return None
    return [float(item) for item in value]


def verify_report(
    report: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    require_all: bool = False,
    max_cv_pct: float = MAX_CV_PCT,
    max_geomean_ratio: float = MAX_GEOMEAN_RATIO,
    max_case_ratio: float = MAX_CASE_RATIO,
    max_memory_geomean_ratio: float = MAX_MEMORY_GEOMEAN_RATIO,
    max_memory_case_ratio: float = MAX_MEMORY_CASE_RATIO,
    privacy: str = "strict",
) -> dict[str, Any]:
    _require(privacy in {"strict", "off"}, "invalid_privacy_mode")
    threshold_values = (
        max_cv_pct,
        max_geomean_ratio,
        max_case_ratio,
        max_memory_geomean_ratio,
        max_memory_case_ratio,
    )
    _require(
        all(not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0 for value in threshold_values),
        "invalid_threshold",
    )
    if privacy == "strict":
        assert_public_report(report)
    reasons: list[str] = []
    failed_routes: list[dict[str, str]] = []
    if require_all and (
        max_cv_pct > MAX_CV_PCT
        or max_geomean_ratio > MAX_GEOMEAN_RATIO
        or max_case_ratio > MAX_CASE_RATIO
        or max_memory_geomean_ratio > MAX_MEMORY_GEOMEAN_RATIO
        or max_memory_case_ratio > MAX_MEMORY_CASE_RATIO
    ):
        reasons.append("threshold_relaxed")
    if report.get("schema") not in {REPORT_SCHEMA, MERGED_SCHEMA} or report.get("schema_version") != REPORT_VERSION:
        reasons.append("report_schema")
    if report.get("runner_version") != RUNNER_VERSION:
        reasons.append("runner_version")
    if report.get("contract_digest") != contract_digest(catalog):
        reasons.append("contract_mismatch")
    if report.get("capabilities") != _capability_disclosure(catalog):
        reasons.append("capability_disclosure")
    toolchain = report.get("toolchain") if isinstance(report.get("toolchain"), Mapping) else {}
    toolchain_valid = (
        toolchain.get("styio_backend") == "native-aot"
        and toolchain.get("compiler_family") == "clang"
        and toolchain.get("target_class") == "portable"
        and toolchain.get("optimization") == "O3"
        and toolchain.get("lto") is False
        and toolchain.get("threads") == 1
        and toolchain.get("runner") == RUNNER_VERSION
        and isinstance(toolchain.get("phase_probe_available"), bool)
        and isinstance(toolchain.get("styio_version"), str)
        and isinstance(toolchain.get("compiler_version"), str)
        and (toolchain.get("styio_version") == "unknown" or PUBLIC_VERSION.fullmatch(str(toolchain.get("styio_version"))) is not None)
        and (toolchain.get("compiler_version") == "unknown" or PUBLIC_VERSION.fullmatch(str(toolchain.get("compiler_version"))) is not None)
    )
    if not toolchain_valid:
        reasons.append("toolchain_contract")
    elif require_all and (toolchain.get("styio_version") == "unknown" or toolchain.get("compiler_version") == "unknown"):
        reasons.append("toolchain_version")
    measurement = report.get("measurement") if isinstance(report.get("measurement"), Mapping) else {}
    if require_all:
        required_measurement = _measurement_metadata(DEFAULT_WARMUPS, REQUIRED_REPETITIONS, "controlled")
        for key, expected in required_measurement.items():
            if measurement.get(key) != expected:
                reasons.append("run_control" if key == "run_control_class" else "measurement_contract")
    cells = report.get("cells") if isinstance(report.get("cells"), list) else []
    catalog_cells = _catalog_cells_by_id(catalog)
    seen: set[str] = set()
    valid_reference_cells: dict[str, list[Mapping[str, Any]]] = {route: [] for route in ROUTES}
    for cell in cells:
        identity = cell.get("id")
        if identity not in catalog_cells:
            reasons.append("unknown_cell")
        if identity in seen:
            reasons.append("duplicate_cell")
        if isinstance(identity, str):
            seen.add(identity)
        expected_cell = catalog_cells.get(str(identity))
        if expected_cell is not None:
            identity_fields = ("family", "scale", "route", "required", "work_units", "algorithm_id", "focus_owner")
            contract_matches = all(cell.get(field) == expected_cell.get(field) for field in identity_fields)
            contract_matches = contract_matches and cell.get("source_digests") == expected_cell.get("source_digest")
            contract_matches = contract_matches and cell.get("input_digest") == expected_cell.get("input_digest")
            contract_matches = contract_matches and cell.get("expected_output_digest") == expected_cell.get("expected_output_digest")
            if not contract_matches:
                reasons.append("cell_contract_mismatch")
                continue
        if cell.get("correctness") != {"styio": True, "cpp": True}:
            reasons.append("correctness_failed")
            continue
        if cell.get("status") != "pass":
            reasons.append("minimum_sample_time" if "minimum_sample_time" in cell.get("reason_codes", []) else "cell_incomplete")
            continue
        repetitions_value = cell.get("repetitions")
        repetitions = repetitions_value if isinstance(repetitions_value, int) and not isinstance(repetitions_value, bool) else 0
        if repetitions <= 0:
            reasons.append("repetitions")
            continue
        is_reference = cell.get("scale") == "reference" and bool(cell.get("required"))
        is_phase = cell.get("family") == "compiler-phase"
        if require_all and is_reference and repetitions != REQUIRED_REPETITIONS:
            reasons.append("repetitions")
        time_samples = cell.get("time_samples_s") if isinstance(cell.get("time_samples_s"), Mapping) else {}
        rss_samples = cell.get("peak_rss_samples_kib") if isinstance(cell.get("peak_rss_samples_kib"), Mapping) else {}
        styio_time = _positive_samples(time_samples.get("styio"), repetitions)
        cpp_time = _positive_samples(time_samples.get("cpp"), repetitions)
        styio_rss = _positive_samples(rss_samples.get("styio"), repetitions)
        cpp_rss = _positive_samples(rss_samples.get("cpp"), repetitions)
        if styio_time is None or cpp_time is None:
            reasons.append("samples_malformed")
            continue
        if styio_rss is None or cpp_rss is None:
            reasons.append("memory_samples_malformed")
            continue
        # Phase buckets share one compiler probe and are diagnostic.  They are
        # required for attribution, but never enter a native performance score.
        if is_phase:
            continue
        try:
            time_ratio = geometric_mean([a / b for a, b in zip(styio_time, cpp_time)])
            memory_ratio = geometric_mean([a / b for a, b in zip(styio_rss, cpp_rss)])
        except ValueError:
            reasons.append("ratio_missing")
            continue
        reported_time = cell.get("time") if isinstance(cell.get("time"), Mapping) else {}
        reported_memory = cell.get("peak_rss") if isinstance(cell.get("peak_rss"), Mapping) else {}
        reported_time_ratio = reported_time.get("geomean_ratio")
        reported_memory_ratio = reported_memory.get("geomean_ratio")
        if (
            isinstance(reported_time_ratio, bool)
            or not isinstance(reported_time_ratio, (int, float))
            or not math.isfinite(float(reported_time_ratio))
            or not math.isclose(float(reported_time_ratio), time_ratio, rel_tol=1e-12, abs_tol=1e-15)
        ):
            reasons.append("ratio_mismatch")
        if (
            isinstance(reported_memory_ratio, bool)
            or not isinstance(reported_memory_ratio, (int, float))
            or not math.isfinite(float(reported_memory_ratio))
            or not math.isclose(float(reported_memory_ratio), memory_ratio, rel_tol=1e-12, abs_tol=1e-15)
        ):
            reasons.append("memory_ratio_mismatch")
        if require_all and is_reference:
            if sample_cv_pct(styio_time) > max_cv_pct or sample_cv_pct(cpp_time) > max_cv_pct:
                reasons.append("noise_cv")
            if time_ratio > max_case_ratio:
                reasons.append("case_ratio")
            if memory_ratio > max_memory_case_ratio:
                reasons.append("memory_case_ratio")
            batch = cell.get("batch") if isinstance(cell.get("batch"), Mapping) else {}
            batch_count = batch.get("count")
            raw_samples = cell.get("raw_batch_time_samples_s") if isinstance(cell.get("raw_batch_time_samples_s"), Mapping) else {}
            raw_styio = _positive_samples(raw_samples.get("styio"), repetitions)
            raw_cpp = _positive_samples(raw_samples.get("cpp"), repetitions)
            batch_valid = (
                isinstance(batch_count, int)
                and not isinstance(batch_count, bool)
                and 0 < batch_count <= MAX_BATCH_COUNT
                and batch.get("equal_work") is True
                and batch.get("minimum_sample_time_s") == MIN_SAMPLE_DURATION_S
                and batch.get("target_sample_time_s") == CALIBRATION_TARGET_DURATION_S
                and batch.get("maximum_count") == MAX_BATCH_COUNT
                and batch.get("retained_floor_met") is True
                and raw_styio is not None
                and raw_cpp is not None
            )
            if not batch_valid:
                reasons.append("minimum_sample_time")
            else:
                normalized = all(
                    math.isclose(raw / batch_count, normalized_value, rel_tol=1e-12, abs_tol=1e-15)
                    for raw_values, normalized_values in ((raw_styio, styio_time), (raw_cpp, cpp_time))
                    for raw, normalized_value in zip(raw_values, normalized_values)
                )
                if not normalized or median(raw_styio) < MIN_SAMPLE_DURATION_S or median(raw_cpp) < MIN_SAMPLE_DURATION_S:
                    reasons.append("minimum_sample_time")
            schedule = cell.get("sample_schedule") if isinstance(cell.get("sample_schedule"), Mapping) else {}
            expected_orders = _interleaved_pair_orders(str(identity), repetitions, "retained")
            expected_codes = ["AB" if order == ("styio", "cpp") else "BA" for order in expected_orders]
            if (
                schedule.get("strategy") != "deterministic-random-interleaving-v1"
                or schedule.get("ab_count") != expected_codes.count("AB")
                or schedule.get("ba_count") != expected_codes.count("BA")
                or schedule.get("schedule_digest") != _sha256("".join(expected_codes).encode("ascii"))
            ):
                reasons.append("sample_schedule")
            route = cell.get("route")
            if route in valid_reference_cells:
                valid_reference_cells[str(route)].append(cell)
    if require_all:
        required = _required_ids(catalog)
        if not required.issubset(seen):
            reasons.append("missing_required_cells")
        expected_route_count = len(catalog["workloads"])
        for route in ROUTES:
            selected = valid_reference_cells[route]
            if len(selected) != expected_route_count:
                reasons.append("required_route_cell_count")
                failed_routes.append({"route": route, "reason_code": "required_route_cell_count"})
                continue
            time_summary = _aggregate_dimension(selected, "time_samples_s")
            memory_summary = _aggregate_dimension(selected, "peak_rss_samples_kib")
            time_upper = time_summary.get("confidence_interval", {}).get("upper")
            memory_upper = memory_summary.get("confidence_interval", {}).get("upper")
            if not isinstance(time_upper, (int, float)) or time_summary.get("geomean_ratio", math.inf) > max_geomean_ratio or time_upper > max_geomean_ratio:
                reasons.append("route_time_confidence")
                failed_routes.append({"route": route, "reason_code": "route_time_confidence"})
            if not isinstance(memory_upper, (int, float)) or memory_summary.get("geomean_ratio", math.inf) > max_memory_geomean_ratio or memory_upper > max_memory_geomean_ratio:
                reasons.append("route_memory_confidence")
                failed_routes.append({"route": route, "reason_code": "route_memory_confidence"})
    computed_aggregates = report_aggregates(cells)
    if require_all and report.get("aggregates") != computed_aggregates:
        reasons.append("aggregate_mismatch")
    route_summaries = computed_aggregates.get("scales", {}).get("reference", {}).get("routes", {})
    primary_summary = route_summaries.get(PRIMARY_ROUTE)
    return {
        "schema": VERDICT_SCHEMA,
        "decision": "pass" if not reasons else "fail",
        "reason_codes": sorted(set(reasons)),
        "failed_routes": sorted(failed_routes, key=lambda item: (item["route"], item["reason_code"])),
        "cell_count": len(cells),
        "required_cell_count": len(_required_ids(catalog)),
        "primary_route": PRIMARY_ROUTE,
        "primary_route_summary": primary_summary,
        "reference_routes": route_summaries,
        "capabilities": _capability_disclosure(catalog),
        "thresholds": {
            "max_cv_pct": max_cv_pct,
            "max_case_ratio": max_case_ratio,
            "max_geomean_ratio": max_geomean_ratio,
            "max_memory_case_ratio": max_memory_case_ratio,
            "max_memory_geomean_ratio": max_memory_geomean_ratio,
            "confidence_level": CONFIDENCE_LEVEL,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run and verify the standards-derived Styio/C++ parity suite.")
    parser.add_argument("--contract", default=str(CATALOG_DEFAULT))
    sub = parser.add_subparsers(dest="operation", required=True)
    catalog_check = sub.add_parser("catalog-check"); catalog_check.add_argument("--contract", default=str(CATALOG_DEFAULT))
    strength = sub.add_parser("cpp-strength"); strength.add_argument("--contract", default=str(CATALOG_DEFAULT)); strength.add_argument("--json", action="store_true")
    run = sub.add_parser("run"); run.add_argument("--contract", default=str(CATALOG_DEFAULT)); run.add_argument("--family", required=True); run.add_argument("--scale", choices=(*SCALES, "all"), default="all"); run.add_argument("--styio-root", required=True); run.add_argument("--build-dir", required=True); run.add_argument("--out-dir", required=True); run.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS); run.add_argument("--repetitions", type=int, default=REQUIRED_REPETITIONS); run.add_argument("--timeout-s", type=float, default=300.0); run.add_argument("--run-class", choices=("development", "controlled"), default="development")
    merge = sub.add_parser("merge"); merge.add_argument("--contract", default=str(CATALOG_DEFAULT)); merge.add_argument("--reports-dir", required=True); merge.add_argument("--out-dir", required=True)
    verify = sub.add_parser("verify"); verify.add_argument("--contract", default=str(CATALOG_DEFAULT)); verify.add_argument("--report", required=True); verify.add_argument("--require-all", action="store_true"); verify.add_argument("--max-cv-pct", type=float, default=MAX_CV_PCT); verify.add_argument("--max-geomean-ratio", type=float, default=MAX_GEOMEAN_RATIO); verify.add_argument("--max-case-ratio", type=float, default=MAX_CASE_RATIO); verify.add_argument("--max-memory-geomean-ratio", type=float, default=MAX_MEMORY_GEOMEAN_RATIO); verify.add_argument("--max-memory-case-ratio", type=float, default=MAX_MEMORY_CASE_RATIO); verify.add_argument("--privacy", choices=("strict", "off"), default="strict")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        catalog, catalog_path = load_catalog(args.contract)
        if args.operation == "catalog-check":
            print(json.dumps({"schema": "styio.parity.catalog.audit.v3", "decision": "pass", "catalog_id": catalog["catalog_id"], "contract_digest": contract_digest(catalog), "families": len(catalog["workloads"]), "required_cells": len(_required_ids(catalog))}, sort_keys=True))
            return 0
        if args.operation == "cpp-strength":
            result = validate_cpp_strength(catalog, catalog_path)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["pass"] else 2
        if args.operation == "run":
            run_shard(catalog, catalog_path, family=args.family, scale=args.scale, styio_root=Path(args.styio_root).resolve(), build_dir=Path(args.build_dir).resolve(), output_dir=Path(args.out_dir).resolve(), warmups=args.warmups, repetitions=args.repetitions, timeout_s=args.timeout_s, run_class=args.run_class)
            return 0
        if args.operation == "merge":
            paths = sorted(Path(args.reports_dir).glob("**/results.json"))
            merge_reports(catalog, paths, Path(args.out_dir).resolve())
            return 0
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        verdict = verify_report(report, catalog, require_all=args.require_all, max_cv_pct=args.max_cv_pct, max_geomean_ratio=args.max_geomean_ratio, max_case_ratio=args.max_case_ratio, max_memory_geomean_ratio=args.max_memory_geomean_ratio, max_memory_case_ratio=args.max_memory_case_ratio, privacy=args.privacy)
        print(json.dumps(verdict, sort_keys=True))
        return 0 if verdict["decision"] == "pass" else 2
    except (GateError, PrivacyError, OSError, ValueError) as exc:
        reason = exc.reason_code if isinstance(exc, GateError) else "runtime_error"
        print(json.dumps({"schema": "styio.parity.standard.error.v3", "decision": "fail", "reason_code": reason}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
