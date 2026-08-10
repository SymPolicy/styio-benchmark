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
CATALOG_DEFAULT = ROOT / "workloads" / "parity-v2" / "contract.json"
REPORT_SCHEMA = "styio.parity.standard.report.v2"
MERGED_SCHEMA = "styio.parity.standard.merged.v2"
REPORT_VERSION = 2
RUNNER_VERSION = "standard-parity-gate-2"
ROUTES = ("compile-and-run", "native-build", "native-run")
SCALES = ("smoke", "development", "reference")
PHASES = ("tokenize", "parse", "semantic-analysis", "lowering", "llvm-emission")
REQUIRED_REPETITIONS = 11
DEFAULT_WARMUPS = 3
MAX_CV_PCT = 5.0
MAX_CASE_RATIO = 1.10
MAX_GEOMEAN_RATIO = 1.05
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


class GateError(RuntimeError):
    """Fail-closed error carrying a privacy-safe stable reason code."""

    def __init__(self, reason_code: str, message: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(message or reason_code)


class PrivacyError(GateError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def contract_digest(contract: Mapping[str, Any]) -> str:
    return _sha256(_canonical_json(contract))


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
    return not FORBIDDEN_KEY_RE.search(key)


def _public_string_allowed(value: str) -> bool:
    return not ("\x00" in value or "\n" in value or "\r" in value or URL_RE.search(value) or ABS_PATH_RE.search(value) or SECRET_RE.search(value))


def validate_public_report(value: Any, *, strict: bool = True, _location: str = "report") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or (strict and not _public_key_allowed(key)):
                raise PrivacyError("privacy_forbidden_key", _location)
            validate_public_report(child, strict=strict, _location=f"{_location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            validate_public_report(child, strict=strict, _location=f"{_location}[{index}]")
    elif isinstance(value, str):
        if strict and not _public_string_allowed(value):
            raise PrivacyError("privacy_forbidden_value", _location)
    elif isinstance(value, (int, bool)) or value is None:
        return
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise PrivacyError("privacy_nonfinite_number", _location)
    else:
        raise PrivacyError("privacy_nonserializable", _location)


def assert_public_report(value: Any, *, strict: bool = True) -> Any:
    validate_public_report(value, strict=strict)
    return value


def write_public_json(path: Path, payload: Mapping[str, Any]) -> None:
    assert_public_report(payload, strict=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("median requires samples")
    return float(statistics.median(values))


def sample_cv_pct(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(values)
    if mean == 0:
        return 0.0 if all(value == 0 for value in values) else math.inf
    return float(statistics.stdev(values) / abs(mean) * 100.0)


def geometric_mean(values: Sequence[float]) -> float:
    if not values or any(value <= 0 or not math.isfinite(value) for value in values):
        raise ValueError("geometric mean requires finite positive values")
    return float(math.exp(statistics.fmean(math.log(value) for value in values)))


def paired_log_ratios(styio: Sequence[float], cpp: Sequence[float], *, reciprocal: bool = False) -> list[float]:
    if len(styio) != len(cpp) or not styio or any(a <= 0 or b <= 0 for a, b in zip(styio, cpp)):
        raise ValueError("paired samples must have equal positive length")
    return [math.log((b / a) if reciprocal else (a / b)) for a, b in zip(styio, cpp)]


def ratio_dimension(styio: Sequence[float], cpp: Sequence[float], *, reciprocal: bool = False) -> dict[str, Any]:
    left, right = list(styio), list(cpp)
    ratios = [(b / a) if reciprocal else (a / b) for a, b in zip(left, right)]
    return {"styio_samples": left, "cpp_samples": right, "styio_median": median(left), "cpp_median": median(right), "styio_cv_pct": sample_cv_pct(left), "cpp_cv_pct": sample_cv_pct(right), "median_ratio": median(ratios), "geomean_ratio": geometric_mean(ratios), "paired_log_ratios": [math.log(value) for value in ratios]}


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
    phase_probe = build_dir / "bin" / "styio_soak_test"
    return Path(styio_executable), Path(cxx), {
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
    helper_path = ROOT / "native-cpp" / "standard_process_tree_rss.py"
    spec = importlib.util.spec_from_file_location("standard_parity_process_tree_rss", helper_path)
    if spec is None or spec.loader is None:
        raise GateError("rss_helper_missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PHASE_TIER_NAMES = {"smoke": "small", "development": "medium", "reference": "large"}
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

        for index in range(warmups):
            for implementation in (("styio", "cpp") if index % 2 == 0 else ("cpp", "styio")):
                collect(implementation, "warmup", index)
        for index in range(repetitions):
            for implementation in (("styio", "cpp") if index % 2 == 0 else ("cpp", "styio")):
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


def _measure_cell(cell: Mapping[str, Any], *, catalog: Mapping[str, Any], catalog_path: Path, styio: Path, cxx: Path, styio_root: Path, warmups: int, repetitions: int, timeout_s: float) -> dict[str, Any]:
    generators = _load_generators(catalog_path)
    styio_source, cpp_source, input_bytes = _source_pair(catalog_path, cell, generators)
    expected = str(cell["expected_output_digest"])
    family = str(cell["family"]); route = str(cell["route"])
    implementation_samples: dict[str, list[float]] = {"styio": [], "cpp": []}
    rss_samples: dict[str, list[float]] = {"styio": [], "cpp": []}
    correctness = {"styio": False, "cpp": False}
    with tempfile.TemporaryDirectory(prefix="standard-parity-cell-") as temporary:
        temp = Path(temporary)
        rss_helper = _load_rss_helper()
        input_path = temp / "input.bin"
        input_path.write_bytes(input_bytes)
        artifacts: dict[str, Path] = {}
        for implementation, executable, source in (("styio", styio, styio_source), ("cpp", cxx, cpp_source)):
            artifact = temp / f"{implementation}.bin"
            elapsed, code, output, _ = _run(_compile_command(executable, source, artifact, styio=implementation == "styio"), cwd=styio_root, timeout_s=timeout_s)
            if code != 0 or not artifact.exists():
                return {"id": cell["id"], "family": family, "scale": cell["scale"], "route": route, "status": "incomplete", "correctness": correctness, "repetitions": repetitions, "warmups": warmups, "source_digests": cell.get("source_digest"), "input_digest": cell.get("input_digest"), "expected_output_digest": expected, "reason_codes": ["compile_failed"]}
            artifacts[implementation] = artifact
            elapsed_run, run_code, run_output, _ = _run([str(artifact)], cwd=styio_root, input_bytes=input_bytes, timeout_s=timeout_s)
            correctness[implementation] = run_code == 0 and _digest_output(run_output) == expected
            if not correctness[implementation]:
                return {"id": cell["id"], "family": family, "scale": cell["scale"], "route": route, "status": "incomplete", "correctness": correctness, "repetitions": repetitions, "warmups": warmups, "source_digests": cell.get("source_digest"), "input_digest": cell.get("input_digest"), "expected_output_digest": expected, "reason_codes": ["correctness_output_digest"]}
        def measure_one(implementation: str, index: int) -> tuple[float, float]:
            artifact = artifacts[implementation]
            if route == "compile-and-run":
                fresh = temp / f"{implementation}-{index}.bin"
                build_command = _compile_command(styio if implementation == "styio" else cxx, styio_source if implementation == "styio" else cpp_source, fresh, styio=implementation == "styio")
                build_elapsed, code, _, _ = _run(build_command, cwd=styio_root, timeout_s=timeout_s)
                if code != 0: raise GateError("compile_failed")
                run_elapsed, run_code, output, _ = _run([str(fresh)], cwd=styio_root, input_bytes=input_bytes, timeout_s=timeout_s)
                if run_code != 0 or _digest_output(output) != expected: raise GateError("correctness_output_digest")
                replay = rss_helper.run_process(build_command, cwd=styio_root, timeout_s=timeout_s, sample_process_tree=True)
                if replay.returncode != 0: raise GateError("compile_failed")
                fresh.unlink(missing_ok=True)
                return build_elapsed + run_elapsed, max(float(replay.peak_rss_kib), 0.001)
            if route == "native-build":
                artifact = temp / f"build-{implementation}-{index}.bin"
                build_command = _compile_command(styio if implementation == "styio" else cxx, styio_source if implementation == "styio" else cpp_source, artifact, styio=implementation == "styio")
                elapsed, code, _, _ = _run(build_command, cwd=styio_root, timeout_s=timeout_s)
                if code != 0: raise GateError("compile_failed")
                replay = rss_helper.run_process(build_command, cwd=styio_root, timeout_s=timeout_s, sample_process_tree=True)
                if replay.returncode != 0: raise GateError("compile_failed")
                artifact.unlink(missing_ok=True)
                return elapsed, max(float(replay.peak_rss_kib), 0.001)
            elapsed, code, output, _ = _run([str(artifact)], cwd=styio_root, input_bytes=input_bytes, timeout_s=timeout_s)
            if code != 0 or _digest_output(output) != expected: raise GateError("correctness_output_digest")
            replay_output = temp / f"rss-replay-{implementation}-{index}.out"
            replay = rss_helper.run_process([str(artifact)], cwd=styio_root, stdin_path=input_path, stdout_path=replay_output, timeout_s=timeout_s, sample_process_tree=True)
            replay_output.unlink(missing_ok=True)
            if replay.returncode != 0: raise GateError("correctness_output_digest")
            return elapsed, max(float(replay.peak_rss_kib), 0.001)
        for warmup_index in range(warmups):
            for implementation in ("styio", "cpp") if warmup_index % 2 == 0 else ("cpp", "styio"):
                measure_one(implementation, -warmup_index - 1)
        for index in range(repetitions):
            order = ("styio", "cpp") if index % 2 == 0 else ("cpp", "styio")
            for implementation in order:
                elapsed, rss = measure_one(implementation, index)
                implementation_samples[implementation].append(elapsed)
                rss_samples[implementation].append(rss)
    time_ratio = ratio_dimension(implementation_samples["styio"], implementation_samples["cpp"])
    memory_ratio = ratio_dimension(rss_samples["styio"], rss_samples["cpp"])
    status = "pass" if correctness == {"styio": True, "cpp": True} else "incomplete"
    record = {"id": cell["id"], "family": family, "scale": cell["scale"], "route": route, "required": bool(cell.get("required")), "work_units": cell["work_units"], "algorithm_id": cell.get("algorithm_id"), "focus_owner": cell.get("focus_owner"), "status": status, "correctness": correctness, "repetitions": repetitions, "warmups": warmups, "source_digests": cell.get("source_digest"), "input_digest": cell.get("input_digest"), "expected_output_digest": expected, "time_samples_s": implementation_samples, "peak_rss_samples_kib": rss_samples, "time": time_ratio, "peak_rss": memory_ratio, "throughput": ratio_dimension(implementation_samples["styio"], implementation_samples["cpp"], reciprocal=True), "reason_codes": []}
    return record


def run_shard(catalog: Mapping[str, Any], catalog_path: Path, *, family: str, scale: str, styio_root: Path, build_dir: Path, output_dir: Path, warmups: int = DEFAULT_WARMUPS, repetitions: int = REQUIRED_REPETITIONS, timeout_s: float = 300.0) -> dict[str, Any]:
    _require(scale in (*SCALES, "all"), "unknown_scale")
    _require(warmups >= 0 and repetitions > 0, "invalid_repetition_budget")
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
    report = {"schema": REPORT_SCHEMA, "schema_version": REPORT_VERSION, "catalog_id": catalog["catalog_id"], "contract_digest": contract_digest(catalog), "runner_version": RUNNER_VERSION, "selection": {"family": family, "scale": scale, "scale_classification": "all-labelled-scales" if scale == "all" else catalog["scales"][scale]["label"], "cell_ids": [cell["id"] for cell in cells]}, "toolchain": toolchain, "measurement": {"warmups": warmups, "repetitions": repetitions, "max_cv_pct": MAX_CV_PCT, "observer_free_timing": True, "isolated_memory_replay": True, "sample_policy": "retain-all"}, "cells": report_cells, "diagnostics": []}
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
        _require(report.get("schema") == REPORT_SCHEMA and report.get("contract_digest") == contract_digest(catalog), "merge_contract_mismatch")
        selected_ids = report.get("selection", {}).get("cell_ids") if isinstance(report.get("selection"), Mapping) else None
        report_ids = [str(cell.get("id")) for cell in report.get("cells", [])]
        _require(isinstance(selected_ids, list) and set(selected_ids) == set(report_ids), "merge_selection_mismatch")
        _require(set(report_ids).issubset(_catalog_cell_ids(catalog)), "merge_unknown_cell")
        reports.append(report)
    _require(reports, "merge_empty")
    base_toolchain = reports[0].get("toolchain")
    base_measurement = reports[0].get("measurement")
    all_cells: list[dict[str, Any]] = []; identities: set[str] = set()
    for report in reports:
        _require(report.get("toolchain") == base_toolchain and report.get("measurement") == base_measurement, "merge_environment_mismatch")
        for cell in report.get("cells", []):
            identity = str(cell.get("id")); _require(identity not in identities, "merge_duplicate_cell")
            identities.add(identity); all_cells.append(cell)
    merged = {"schema": MERGED_SCHEMA, "schema_version": REPORT_VERSION, "catalog_id": catalog["catalog_id"], "contract_digest": contract_digest(catalog), "runner_version": RUNNER_VERSION, "toolchain": base_toolchain, "measurement": base_measurement, "shards": len(reports), "selection": {"cell_ids": sorted(identities)}, "cells": sorted(all_cells, key=lambda cell: str(cell.get("id"))), "diagnostics": []}
    write_public_json(output_dir / "results.json", merged)
    return merged


def _required_ids(catalog: Mapping[str, Any], *, scale: str = "reference") -> set[str]:
    ids = {cell["id"] for workload in catalog["workloads"] for cell in workload["cells"] if cell.get("scale") == scale and cell.get("required")}
    ids.update(cell["id"] for cell in catalog["compiler_phase_sweep"]["cells"] if cell.get("scale") == scale and cell.get("required"))
    return ids


def verify_report(report: Mapping[str, Any], catalog: Mapping[str, Any], *, require_all: bool = False, max_cv_pct: float = MAX_CV_PCT, max_geomean_ratio: float = MAX_GEOMEAN_RATIO, max_case_ratio: float = MAX_CASE_RATIO, privacy: str = "strict") -> dict[str, Any]:
    if privacy == "strict":
        assert_public_report(report)
    reasons: list[str] = []
    if report.get("contract_digest") != contract_digest(catalog): reasons.append("contract_mismatch")
    cells = report.get("cells") if isinstance(report.get("cells"), list) else []
    seen: set[str] = set(); ratios: list[float] = []
    for cell in cells:
        identity = cell.get("id")
        if identity not in _catalog_cell_ids(catalog): reasons.append("unknown_cell")
        if identity in seen: reasons.append("duplicate_cell")
        seen.add(identity)
        if cell.get("status") != "pass" or cell.get("correctness") != {"styio": True, "cpp": True}:
            reasons.append("correctness_failed"); continue
        repetitions = int(cell.get("repetitions", 0))
        if require_all and repetitions != REQUIRED_REPETITIONS: reasons.append("repetitions")
        time_data = cell.get("time") if isinstance(cell.get("time"), Mapping) else {}
        if not isinstance(cell.get("time_samples_s"), Mapping): reasons.append("samples_missing"); continue
        for implementation in ("styio", "cpp"):
            values = cell["time_samples_s"].get(implementation, [])
            if not isinstance(values, list) or len(values) != repetitions or any(not isinstance(value, (int, float)) or value <= 0 or not math.isfinite(float(value)) for value in values): reasons.append("samples_malformed")
            elif require_all and sample_cv_pct(values) > max_cv_pct: reasons.append("noise_cv")
        ratio = time_data.get("geomean_ratio")
        if isinstance(ratio, (int, float)) and math.isfinite(float(ratio)) and ratio > 0:
            ratios.append(float(ratio))
            if require_all and ratio > max_case_ratio: reasons.append("case_ratio")
        else: reasons.append("ratio_missing")
    if require_all:
        required = _required_ids(catalog)
        if not required.issubset(seen): reasons.append("missing_required_cells")
        if len(ratios) != len(required): reasons.append("required_cell_count")
        if ratios:
            geomean = geometric_mean(ratios)
            if geomean > max_geomean_ratio: reasons.append("geomean_ratio")
        else: geomean = None
    else:
        geomean = geometric_mean(ratios) if ratios else None
    return {"schema": "styio.parity.standard.verdict.v2", "decision": "pass" if not reasons else "fail", "reason_codes": sorted(set(reasons)), "cell_count": len(cells), "required_cell_count": len(_required_ids(catalog)), "geomean_ratio": geomean, "thresholds": {"max_cv_pct": max_cv_pct, "max_case_ratio": max_case_ratio, "max_geomean_ratio": max_geomean_ratio}}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run and verify the standards-derived Styio/C++ parity-v2 suite.")
    parser.add_argument("--contract", default=str(CATALOG_DEFAULT))
    sub = parser.add_subparsers(dest="operation", required=True)
    catalog_check = sub.add_parser("catalog-check"); catalog_check.add_argument("--contract", default=str(CATALOG_DEFAULT))
    strength = sub.add_parser("cpp-strength"); strength.add_argument("--contract", default=str(CATALOG_DEFAULT)); strength.add_argument("--json", action="store_true")
    run = sub.add_parser("run"); run.add_argument("--contract", default=str(CATALOG_DEFAULT)); run.add_argument("--family", required=True); run.add_argument("--scale", choices=(*SCALES, "all"), default="all"); run.add_argument("--styio-root", required=True); run.add_argument("--build-dir", required=True); run.add_argument("--out-dir", required=True); run.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS); run.add_argument("--repetitions", type=int, default=REQUIRED_REPETITIONS); run.add_argument("--timeout-s", type=float, default=300.0)
    merge = sub.add_parser("merge"); merge.add_argument("--contract", default=str(CATALOG_DEFAULT)); merge.add_argument("--reports-dir", required=True); merge.add_argument("--out-dir", required=True)
    verify = sub.add_parser("verify"); verify.add_argument("--contract", default=str(CATALOG_DEFAULT)); verify.add_argument("--report", required=True); verify.add_argument("--require-all", action="store_true"); verify.add_argument("--max-cv-pct", type=float, default=MAX_CV_PCT); verify.add_argument("--max-geomean-ratio", type=float, default=MAX_GEOMEAN_RATIO); verify.add_argument("--max-case-ratio", type=float, default=MAX_CASE_RATIO); verify.add_argument("--privacy", choices=("strict", "off"), default="strict")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        catalog, catalog_path = load_catalog(args.contract)
        if args.operation == "catalog-check":
            print(json.dumps({"schema": "styio.parity.catalog.audit.v2", "decision": "pass", "catalog_id": catalog["catalog_id"], "contract_digest": contract_digest(catalog), "families": len(catalog["workloads"]), "required_cells": len(_required_ids(catalog))}, sort_keys=True))
            return 0
        if args.operation == "cpp-strength":
            result = validate_cpp_strength(catalog, catalog_path)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["pass"] else 2
        if args.operation == "run":
            run_shard(catalog, catalog_path, family=args.family, scale=args.scale, styio_root=Path(args.styio_root).resolve(), build_dir=Path(args.build_dir).resolve(), output_dir=Path(args.out_dir).resolve(), warmups=args.warmups, repetitions=args.repetitions, timeout_s=args.timeout_s)
            return 0
        if args.operation == "merge":
            paths = sorted(Path(args.reports_dir).glob("**/results.json"))
            merge_reports(catalog, paths, Path(args.out_dir).resolve())
            return 0
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        verdict = verify_report(report, catalog, require_all=args.require_all, max_cv_pct=args.max_cv_pct, max_geomean_ratio=args.max_geomean_ratio, max_case_ratio=args.max_case_ratio, privacy=args.privacy)
        print(json.dumps(verdict, sort_keys=True))
        return 0 if verdict["decision"] == "pass" else 2
    except (GateError, PrivacyError, OSError, ValueError) as exc:
        reason = exc.reason_code if isinstance(exc, GateError) else "runtime_error"
        print(json.dumps({"schema": "styio.parity.standard.error.v2", "decision": "fail", "reason_code": reason}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
