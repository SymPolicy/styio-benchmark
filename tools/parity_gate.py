#!/usr/bin/env python3
"""Correctness-first, paired Styio/C++ parity evidence runner.

The runner has three deliberately small public operations:

``catalog-check``
    Validate the frozen parity-v1 catalog and its independent digests.
``run``
    Produce privacy-safe evidence.  This command never evaluates parity
    thresholds and therefore cannot turn a noisy/incomplete run into a pass.
``verify``
    Consume an evidence report and apply completeness, correctness, noise,
    privacy, and (when requested) parity thresholds.

The implementation keeps route setup and measurement as plain functions.  A
fixed three-route catalog does not need an indirect lifecycle/strategy layer;
the direct table makes the timed boundaries auditable.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CATALOG_DEFAULT = ROOT / "workloads" / "parity-v1" / "contract.json"
HELPER_ROOT = ROOT / "native-cpp"
ROUTES = ("compile-and-run", "native-build", "native-run")
TIERS = ("small", "medium", "large")
PHASES = ("tokenize", "parse", "semantic-analysis", "lowering", "llvm-emission")
FOCUS_OWNERS = {"frontend-parser", "frontend-sema", "ir-lowering", "backend-runtime"}
FOCUS_BUDGET_OWNERS = (
    "frontend-parser",
    "frontend-sema",
    "ir-lowering",
    "backend-runtime",
    "compiler-pipeline",
)
REPORT_SCHEMA = "styio.parity.report.v1"
REPORT_VERSION = 1
RUNNER_VERSION = "parity-gate-1"
CALIBRATION_MAX_BATCH = 20_000
# Keep short samples above timer/scheduler quantisation while avoiding a
# second full compile for routes whose calibration is already substantial.
CALIBRATION_MIN_DURATION_S = 0.500
SHORT_CELL_MAX_ESTIMATE_S = 0.500
STABILITY_MAX_ATTEMPTS = 3
STABILITY_CV_LIMIT_PCT = 5.0
STABILITY_MAX_CALIBRATION_DURATION_S = 8.0
PHASE_SAMPLE_BATCH_COUNT = 5
COMPILE_NATIVE_BASELINE_BATCH_COUNT = 3
RSS_REPLAY_BATCH_COUNT = 1
# The scalar compile-and-run C++ combined CV audit was 10.16%.  Applying the
# minimum-time batching rule gives ceil(3*(10.16/5)^2)=13.  Eleven operations
# are the fixed Latin-block budget for the eleven retained samples: each
# sample occupies every position exactly once, so scheduler/cache drift is
# spread without adding another full set of operations.  This is
# evidence-derived, not adaptive to a retained sample's outcome.
COMPILE_AND_RUN_AUDIT_CV_PCT = 10.16
COMPILE_AND_RUN_BATCH_MIN = COMPILE_NATIVE_BASELINE_BATCH_COUNT
COMPILE_AND_RUN_BATCH_FORMULA = math.ceil(
    COMPILE_AND_RUN_BATCH_MIN * (COMPILE_AND_RUN_AUDIT_CV_PCT / STABILITY_CV_LIMIT_PCT) ** 2
)
COMPILE_AND_RUN_BATCH_SAFETY_CAP = 11
# The fixed route budget is exactly one cyclic Latin block for the baseline
# eleven-sample cell.  The formula remains recorded as the audit evidence;
# no retained sample may increase this work count.
COMPILE_AND_RUN_BASELINE_BATCH_COUNT = COMPILE_AND_RUN_BATCH_SAFETY_CAP
# Small generated C++ phase traces are short enough for scheduler noise to
# dominate a twenty-run aggregate.  The shortest observed Clang phase bucket
# is about 8.4 ms, so 60 independent compilations provide the 0.5 s minimum
# work floor.  Styio's probe runs twenty in-process passes per invocation;
# `_run_phase_probe_samples` groups those into the same total compile count
# (three probe processes × twenty passes) before normalizing.
PHASE_SAMPLE_BATCH_COUNTS = {"small": 60, "medium": 20, "large": 1}

HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
HASH = re.compile(r"^[0-9a-f]{7,64}$")
PUBLIC_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,63}$")
PUBLIC_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DIAGNOSTIC_CODE = re.compile(r"STYIO_[A-Z0-9_]+")
VERSION_RE = re.compile(r"(?<![0-9])([0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+][A-Za-z0-9.-]+)?)")
URL_RE = re.compile(r"(?:https?|file|ssh|git)://", re.IGNORECASE)
ABS_PATH_RE = re.compile(
    r"(?:^[A-Za-z]:[\\/]|^/|(?:^|/)(?:Users|home|private|tmp|var|opt|Volumes|mnt)/)",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(
    r"(?:-----BEGIN|(?:bearer|basic)\s+|(?:sk|ghp|gho|github_pat|xox[baprs])-|AKIA[0-9A-Z]{12,})",
    re.IGNORECASE,
)
FORBIDDEN_KEY_RE = re.compile(
    r"(?:^|[_-])(host(?:name)?|user(?:name)?|machine|environment|env|command|argv|path|dir|cwd|root|exec(?:utable)?_path|"
    r"build_dir|source_path|file_path|absolute_path|url|uri|endpoint|secret|password|credential|api_key|"
    r"access_token|authorization|stderr|stdout|stack|trace)(?:$|[_-])",
    re.IGNORECASE,
)


class GateError(RuntimeError):
    """Expected contract/runtime failure with a stable public reason code."""

    def __init__(self, reason_code: str, message: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(message or reason_code)


class PrivacyError(GateError):
    """Raised when a report contains a private or unsanitized value."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def contract_digest(contract: Mapping[str, Any]) -> str:
    """Digest the contract independently of formatting/indentation."""

    return _sha256(_canonical_json(contract))


def _load_generators(catalog_path: Path) -> Any:
    generator_path = catalog_path.parent / "generators.py"
    spec = importlib.util.spec_from_file_location("parity_v1_generators_gate", generator_path)
    if spec is None or spec.loader is None:
        raise GateError("catalog_generator_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and HEX_DIGEST.fullmatch(value) is not None


def _require(condition: bool, reason: str, message: str = "") -> None:
    if not condition:
        raise GateError(reason, message)


def validate_catalog(catalog: Mapping[str, Any], catalog_path: Path | None = None) -> None:
    """Validate the frozen catalog without relying on benchmark tests."""

    _require(catalog.get("schema") == "styio.parity.catalog.v1", "catalog_schema")
    _require(catalog.get("catalog_id") == "parity-v1", "catalog_id")
    _require(catalog.get("catalog_version") == 1, "catalog_version")
    _require(catalog.get("digest_algorithm") == "sha256", "catalog_digest_algorithm")
    _require(catalog.get("canonical_encoding") == "utf-8", "catalog_encoding")
    _require(catalog.get("generator") == "generators.py", "catalog_generator")

    routes = catalog.get("routes")
    _require(isinstance(routes, list) and tuple(item.get("id") for item in routes) == ROUTES, "catalog_routes")
    for route in routes:
        boundary = route.get("boundary")
        _require(
            isinstance(boundary, dict)
            and set(boundary) == {"start", "stop", "timed_region", "artifact_policy"}
            and all(isinstance(item, str) and item for item in boundary.values()),
            "catalog_route_boundary",
        )

    if catalog_path is None:
        catalog_path = CATALOG_DEFAULT
    generators = _load_generators(catalog_path)
    workload_specs = {
        "scalar-compute": ((10_000, 250_000, 5_000_000), generators.scalar_compute_input, generators.scalar_compute_reference),
        "collection-reduce": ((256, 4_096, 65_536), generators.collection_reduce_input, generators.collection_reduce_reference),
        "stream-I/O": ((65_536, 4_194_304, 33_554_432), generators.stream_io_input, generators.stream_io_reference),
    }
    workloads = catalog.get("workloads")
    _require(isinstance(workloads, list) and tuple(item.get("id") for item in workloads) == tuple(workload_specs), "catalog_workloads")
    all_ids: set[str] = set()
    for workload in workloads:
        family = workload.get("id")
        sizes, input_generator, reference_generator = workload_specs.get(family, ((), None, None))
        _require(tuple(workload.get("sizes", ())) == sizes, "catalog_sizes")
        _require(isinstance(workload.get("algorithm_id"), str) and workload["algorithm_id"], "catalog_algorithm")
        source = workload.get("source")
        _require(isinstance(source, dict) and set(source) == {"styio", "cpp", "source_digest"}, "catalog_sources")
        source_digest = source.get("source_digest")
        _require(isinstance(source_digest, dict) and set(source_digest) == {"styio", "cpp"}, "catalog_source_digests")
        for language in ("styio", "cpp"):
            rel = Path(str(source.get(language, "")))
            _require(not rel.is_absolute() and str(rel) == str(source.get(language)), "catalog_source_path")
            source_file = catalog_path.parent / rel
            _require(source_file.is_file(), "catalog_source_missing")
            _require(_is_digest(source_digest.get(language)), "catalog_source_digest")
            _require(_sha256(source_file.read_bytes()) == source_digest[language], "catalog_source_digest_mismatch")
        input_spec = workload.get("input")
        _require(isinstance(input_spec, dict), "catalog_input")
        by_size = input_spec.get("sha256_by_size")
        _require(isinstance(by_size, dict) and tuple(int(k) for k in by_size) == sizes, "catalog_input_digests")
        for size in sizes:
            _require(_is_digest(by_size.get(str(size))), "catalog_input_digest")
            _require(_sha256(input_generator(size)) == by_size[str(size)], "catalog_input_digest_mismatch")
        cells = workload.get("cells")
        _require(isinstance(cells, list) and len(cells) == len(sizes) * len(ROUTES), "catalog_cells")
        for cell in cells:
            cid = cell.get("id")
            _require(isinstance(cid, str) and cid not in all_ids, "catalog_duplicate_cell")
            all_ids.add(cid)
            parts = cid.rsplit("/", 2)
            _require(len(parts) == 3 and parts[0] == family and parts[1] in TIERS and parts[2] in ROUTES, "catalog_cell_id")
            size = dict(zip(TIERS, sizes))[parts[1]]
            _require(cell.get("size") == size and cell.get("work_units") == size, "catalog_cell_size")
            _require(cell.get("algorithm_id") == workload.get("algorithm_id"), "catalog_cell_algorithm")
            _require(cell.get("route") in ROUTES, "catalog_cell_route")
            _require(cell.get("source_digest") == source_digest, "catalog_cell_source_digest")
            _require(cell.get("input_digest") == by_size[str(size)], "catalog_cell_input_digest")
            expected = _sha256(reference_generator(size))
            _require(cell.get("expected_output_digest") == expected, "catalog_output_digest")
            _require(cell.get("reference_output_digest") == expected, "catalog_reference_digest")
            _require(cell.get("focus_owner") in FOCUS_OWNERS, "catalog_focus_owner")
            boundary = cell.get("route_boundary")
            _require(isinstance(boundary, dict) and set(boundary) == {"start", "stop"}, "catalog_cell_boundary")

    phase_sweep = catalog.get("compiler_phase_sweep")
    _require(isinstance(phase_sweep, dict), "catalog_phase_sweep")
    _require(phase_sweep.get("token_unit") == "tokens", "catalog_phase_token_unit")
    phase_targets = phase_sweep.get("token_targets")
    _require(phase_targets == {"small": 1_000, "medium": 16_000, "large": 128_000}, "catalog_phase_targets")
    phases = phase_sweep.get("phases")
    _require(isinstance(phases, list) and tuple(item.get("id") for item in phases) == PHASES, "catalog_phases")
    phase_cells = phase_sweep.get("cells")
    _require(isinstance(phase_cells, list) and len(phase_cells) == len(PHASES) * len(TIERS), "catalog_phase_cells")
    for cell in phase_cells:
        cid = cell.get("id")
        _require(isinstance(cid, str) and cid not in all_ids, "catalog_duplicate_cell")
        all_ids.add(cid)
        parts = cid.split("/")
        _require(len(parts) == 3 and parts[0] == "compiler-phase" and parts[1] in PHASES and parts[2] in TIERS, "catalog_phase_cell_id")
        size = phase_targets[parts[2]]
        _require(cell.get("size") == size and cell.get("work_units") == size, "catalog_phase_size")
        generated = generators.phase_source(size)
        output = generators.phase_reference_output(size)
        cpp_generated = generators.phase_cpp_source(size)
        _require(
            cell.get("source_digest") == {"styio": _sha256(generated), "cpp": _sha256(cpp_generated)},
            "catalog_phase_source_digest",
        )
        _require(cell.get("input_digest") == _sha256(b""), "catalog_phase_input_digest")
        _require(cell.get("expected_output_digest") == _sha256(output), "catalog_phase_output_digest")
        _require(cell.get("reference_output_digest") == _sha256(output), "catalog_phase_reference_digest")
        _require(cell.get("route") == "compile-and-run", "catalog_phase_route")
        _require(cell.get("focus_owner") in FOCUS_OWNERS, "catalog_phase_focus_owner")
        boundary = cell.get("route_boundary")
        _require(isinstance(boundary, dict) and set(boundary) == {"start", "stop"}, "catalog_phase_boundary")

    diagnostics = catalog.get("diagnostics")
    _require(isinstance(diagnostics, list) and len(diagnostics) >= 4, "catalog_diagnostics")
    diagnostic_codes = {
        "diagnostic/lex/unterminated-block-comment": "STYIO_LEX_UNTERMINATED_BLOCK_COMMENT",
        "diagnostic/parse/unexpected-token": "STYIO_PARSE_UNEXPECTED_TOKEN",
        "diagnostic/type/fixed-reassignment": "STYIO_TYPE_ERROR",
        "diagnostic/runtime/missing-file": "STYIO_RUNTIME_FILE_OPEN_READ",
    }
    for case in diagnostics:
        source_file = catalog_path.parent / Path(str(case.get("source", "")))
        _require(not Path(str(case.get("source", ""))).is_absolute() and source_file.is_file(), "catalog_diagnostic_source")
        digest = _sha256(source_file.read_bytes())
        _require(case.get("source_digest") == digest and case.get("input_digest") == digest, "catalog_diagnostic_digest")
        _require(case.get("expected_diagnostic_code") == diagnostic_codes.get(case.get("id")), "catalog_diagnostic_code")
        _require(case.get("route") == "compile-and-run", "catalog_diagnostic_route")
    serialized = json.dumps(catalog, sort_keys=True)
    retired_labels = ("full" + "-cli", "cached" + "-jit", "runtime" + "-only", "runtime" + "-helper")
    _require(not any(label in serialized for label in retired_labels), "catalog_retired_route")
    _require(not URL_RE.search(serialized), "catalog_url")


def load_catalog(path: str | Path) -> tuple[dict[str, Any], Path]:
    catalog_path = Path(path).expanduser().resolve()
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError("catalog_unreadable", str(exc)) from exc
    if not isinstance(catalog, dict):
        raise GateError("catalog_shape")
    validate_catalog(catalog, catalog_path)
    return catalog, catalog_path


def _public_key_allowed(key: str) -> bool:
    return not FORBIDDEN_KEY_RE.search(key)


def _public_string_allowed(value: str) -> bool:
    if "\x00" in value or URL_RE.search(value) or ABS_PATH_RE.search(value) or SECRET_VALUE_RE.search(value):
        return False
    # Newlines are only meaningful in unsanitized subprocess text.
    return "\n" not in value and "\r" not in value


def validate_public_report(value: Any, *, strict: bool = True, _path: str = "report") -> None:
    """Recursively reject private keys/values before report serialization."""

    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or (strict and not _public_key_allowed(key)):
                raise PrivacyError("privacy_forbidden_key", _path)
            validate_public_report(child, strict=strict, _path=f"{_path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            validate_public_report(child, strict=strict, _path=f"{_path}[{index}]")
        return
    if isinstance(value, str):
        if strict and not _public_string_allowed(value):
            raise PrivacyError("privacy_forbidden_value", _path)
        return
    if isinstance(value, (int, float, bool)) or value is None:
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise PrivacyError("privacy_nonfinite_number", _path)
        return
    raise PrivacyError("privacy_nonserializable", _path)


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
    """Sample coefficient of variation as a percentage; retain all samples."""

    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(values)
    if mean == 0:
        return 0.0 if all(value == 0 for value in values) else math.inf
    return float(statistics.stdev(values) / abs(mean) * 100.0)


def geometric_mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("geometric mean requires values")
    if any(value <= 0 or not math.isfinite(value) for value in values):
        raise ValueError("geometric mean requires finite positive values")
    return float(math.exp(statistics.fmean(math.log(value) for value in values)))


def paired_log_ratios(styio: Sequence[float], cpp: Sequence[float]) -> list[float]:
    if len(styio) != len(cpp) or not styio:
        raise ValueError("paired samples must have equal non-zero length")
    if any(a <= 0 or b <= 0 for a, b in zip(styio, cpp)):
        raise ValueError("paired samples must be positive")
    return [math.log(a / b) for a, b in zip(styio, cpp)]


# Descriptive aliases make the arithmetic easy to consume from focused tests
# without introducing a second implementation or a route-specific class.
coefficient_of_variation_pct = sample_cv_pct
geometric_mean_ratio = geometric_mean
compute_paired_log_ratios = paired_log_ratios
validate_report_privacy = validate_public_report
sanitize_report = assert_public_report


def calibrate_batch_count(
    estimates_s: Sequence[float],
    *,
    minimum_duration_s: float = CALIBRATION_MIN_DURATION_S,
    max_batch: int = CALIBRATION_MAX_BATCH,
) -> int:
    """Choose one equal work count for both sides of a short cell.

    A calibration is deliberately untimed evidence.  When the faster side is
    already above the minimum duration, one invocation is retained; otherwise
    the same bounded count is used for Styio and C++.  This is the familiar
    Google Benchmark minimum-time batching rule without allowing one side to
    receive a different number of operations.
    """

    if not estimates_s or any(not math.isfinite(float(value)) or value <= 0 for value in estimates_s):
        raise ValueError("calibration estimates must be finite and positive")
    if not math.isfinite(minimum_duration_s) or minimum_duration_s <= 0:
        raise ValueError("minimum_duration_s must be finite and positive")
    if max_batch <= 0:
        raise ValueError("max_batch must be positive")
    # The faster side is the useful lower bound: one equal batch count must
    # lift that side above the minimum-time floor as well.  The slower side
    # receives the same count, preserving equal work and paired ordering.
    fastest = min(float(value) for value in estimates_s)
    if fastest >= minimum_duration_s:
        return 1
    return max(1, min(max_batch, math.ceil(minimum_duration_s / fastest)))


def normalize_batched_elapsed(elapsed_s: float, batch_count: int) -> float:
    """Return per-operation elapsed time for one equal calibrated batch."""

    if not math.isfinite(float(elapsed_s)) or elapsed_s <= 0 or batch_count <= 0:
        raise ValueError("batched elapsed time must be finite and positive")
    return float(elapsed_s) / float(batch_count)


def _safe_stats(values: Sequence[float], suffix: str) -> dict[str, float]:
    return {
        f"median_{suffix}": median(values),
        f"min_{suffix}": float(min(values)),
        f"max_{suffix}": float(max(values)),
        f"cv_pct_{suffix}": sample_cv_pct(values),
    }


def _ratio_dimension(styio: Sequence[float], cpp: Sequence[float], *, reciprocal: bool = False) -> dict[str, Any]:
    left = list(styio)
    right = list(cpp)
    ratios = [b / a if reciprocal else a / b for a, b in zip(left, right)]
    logs = [math.log(value) for value in ratios]
    return {
        "styio_samples": left,
        "cpp_samples": right,
        "styio_median": median(left),
        "cpp_median": median(right),
        "styio_cv_pct": sample_cv_pct(left),
        "cpp_cv_pct": sample_cv_pct(right),
        "median_ratio": median(ratios),
        "geomean_ratio": geometric_mean(ratios),
        "paired_log_ratios": logs,
    }


def _parse_public_version(text: str, default: str = "unknown") -> str:
    match = VERSION_RE.search(text)
    if not match:
        return default
    candidate = match.group(1)
    return candidate if PUBLIC_VERSION.fullmatch(candidate) else default


def _run_capture(
    command: Sequence[str],
    *,
    cwd: Path,
    input_bytes: bytes = b"",
    timeout_s: float = 300.0,
) -> tuple[int, bytes, bytes]:
    try:
        proc = subprocess.run(
            list(command),
            cwd=str(cwd),
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError from exc
    return int(proc.returncode), bytes(proc.stdout), bytes(proc.stderr)


def _compiler_version(executable: Path, cwd: Path) -> tuple[str, str]:
    try:
        code, stdout, stderr = _run_capture([str(executable), "--version"], cwd=cwd)
    except OSError:
        return "unknown", "unknown"
    text = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")
    family = "clang" if "clang" in text.lower() else "unknown"
    return family, _parse_public_version(text)


def _cache_value(cache: Path, key: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}(?::[^=]+)?=(.*)$")
    try:
        for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
            match = pattern.match(line)
            if match:
                return match.group(1).strip()
    except OSError:
        pass
    return ""


def discover_toolchain(styio_root: Path, build_dir: Path, styio_exe: Path | None = None) -> dict[str, Any]:
    cache = build_dir / "CMakeCache.txt"
    if not cache.is_file():
        raise GateError("cmake_cache_missing")
    build_type = _cache_value(cache, "CMAKE_BUILD_TYPE")
    if build_type.lower() != "release":
        raise GateError("release_build_required")
    compiler_raw = _cache_value(cache, "CMAKE_CXX_COMPILER")
    compiler = Path(compiler_raw)
    if not compiler.is_absolute():
        resolved = shutil.which(compiler_raw)
        compiler = Path(resolved) if resolved else compiler
    if not compiler.is_file():
        resolved = shutil.which(compiler.name)
        compiler = Path(resolved) if resolved else compiler
    if not compiler.is_file() or not os.access(compiler, os.X_OK):
        raise GateError("cmake_compiler_missing")
    family, version = _compiler_version(compiler, styio_root)
    if family != "clang":
        raise GateError("clang_toolchain_required")
    system = _cache_value(cache, "CMAKE_SYSTEM_NAME") or "unknown"
    processor = _cache_value(cache, "CMAKE_SYSTEM_PROCESSOR") or _cache_value(cache, "CMAKE_HOST_SYSTEM_PROCESSOR") or "unknown"
    arch = _cache_value(cache, "CMAKE_OSX_ARCHITECTURES")
    target = "-".join(item for item in (arch or processor, system) if item and item != "unknown") or "portable"
    target = re.sub(r"[^A-Za-z0-9._-]+", "-", target).strip("-").lower() or "portable"
    if not PUBLIC_TARGET.fullmatch(target):
        target = "portable"
    if styio_exe is None:
        candidates = (build_dir / "bin" / "styio", styio_root / "build" / "bin" / "styio", styio_root / "build" / "default" / "bin" / "styio")
        styio_exe = next((candidate for candidate in candidates if candidate.is_file() and os.access(candidate, os.X_OK)), None)
    if styio_exe is None or not styio_exe.is_file():
        raise GateError("styio_compiler_missing")
    phase_probe = build_dir / "bin" / "styio_soak_test"
    try:
        code, stdout, stderr = _run_capture([str(styio_exe), "--version"], cwd=styio_root)
        version_text = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")
        styio_version = _parse_public_version(version_text)
    except OSError:
        styio_version = "unknown"
    return {
        "cxx": compiler,
        "styio": styio_exe,
        "compiler_family": family,
        "compiler_version": version,
        "styio_version": styio_version,
        "target_class": target,
        "build_type": "Release",
        "optimization": "O3",
        "lto": False,
        "build_dir": build_dir,
        "phase_probe": phase_probe,
    }


def _load_rss_helper():
    spec = importlib.util.spec_from_file_location("parity_process_tree_rss", HELPER_ROOT / "process_tree_rss.py")
    if spec is None or spec.loader is None:
        raise GateError("rss_helper_missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class CellSpec:
    cell_id: str
    family: str
    tier: str
    size: int
    route: str
    focus_owner: str
    source_styio: Path | None
    source_cpp: Path | None
    input_bytes: bytes
    expected_digest: str
    source_digest: dict[str, str]
    phase: str | None = None


def _all_cells(catalog: Mapping[str, Any], catalog_path: Path) -> list[CellSpec]:
    generators = _load_generators(catalog_path)
    cells: list[CellSpec] = []
    for workload in catalog["workloads"]:
        family = workload["id"]
        source = workload["source"]
        input_spec = workload["input"]
        input_functions = {
            "scalar-compute": generators.scalar_compute_input,
            "collection-reduce": generators.collection_reduce_input,
            "stream-I/O": generators.stream_io_input,
        }
        for cell in workload["cells"]:
            size = int(cell["size"])
            cells.append(
                CellSpec(
                    cell_id=cell["id"],
                    family=family,
                    tier=cell["tier"],
                    size=size,
                    route=cell["route"],
                    focus_owner=cell["focus_owner"],
                    source_styio=catalog_path.parent / source["styio"],
                    source_cpp=catalog_path.parent / source["cpp"],
                    input_bytes=input_functions[family](size),
                    expected_digest=cell["expected_output_digest"],
                    source_digest=dict(cell["source_digest"]),
                )
            )
    phase_targets = catalog["compiler_phase_sweep"]["token_targets"]
    for cell in catalog["compiler_phase_sweep"]["cells"]:
        phase = cell["id"].split("/")[1]
        size = int(cell["size"])
        cells.append(
            CellSpec(
                cell_id=cell["id"],
                family="compiler-phase",
                tier=cell["id"].split("/")[2],
                size=size,
                route="compile-and-run",
                focus_owner=cell["focus_owner"],
                source_styio=None,
                source_cpp=None,
                input_bytes=b"",
                expected_digest=cell["expected_output_digest"],
                source_digest=dict(cell["source_digest"]),
                phase=phase,
            )
        )
    return cells


def _phase_cpp_source(size: int) -> bytes:
    bindings = max(1, size // 10)
    return (
        "#include <cstdint>\n#include <iostream>\nint main(){std::int64_t value=0;"
        f"for(std::int64_t i=1;i<={bindings};++i)value+=i%97;"
        "std::cout<<value<<'\\n';return 0;}\n"
    ).encode("ascii")


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_clean(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _run_timed_process(
    command: Sequence[str],
    *,
    cwd: Path,
    input_path: Path,
    output_path: Path,
    helper: Any,
    timeout_s: float = 300.0,
    env: Mapping[str, str] | None = None,
    sample_process_tree: bool = False,
) -> tuple[float, float, int]:
    sample = helper.run_process(
        command,
        cwd=cwd,
        stdin_path=input_path,
        stdout_path=output_path,
        timeout_s=timeout_s,
        env=env,
        sample_process_tree=sample_process_tree,
    )
    return sample.elapsed_s, sample.peak_rss_kib, sample.returncode


def _run_build_command(
    command: Sequence[str],
    *,
    cwd: Path,
    helper: Any,
    timeout_s: float = 300.0,
    env: Mapping[str, str] | None = None,
    sample_process_tree: bool = False,
) -> tuple[float, float, int]:
    sample = helper.run_process(
        command,
        cwd=cwd,
        timeout_s=timeout_s,
        env=env,
        sample_process_tree=sample_process_tree,
    )
    return sample.elapsed_s, sample.peak_rss_kib, sample.returncode


def _cpp_command(cxx: Path, source: Path, artifact: Path) -> list[str]:
    return [str(cxx), "-std=c++20", "-O3", "-fno-lto", "-DNDEBUG", str(source), "-o", str(artifact)]


def _styio_build_command(styio: Path, source: Path, artifact: Path) -> list[str]:
    return [str(styio), "build", str(source), "-o", str(artifact)]


def _validate_output(
    command: Sequence[str],
    *,
    cwd: Path,
    input_path: Path,
    expected_digest: str,
    helper: Any,
    output_path: Path,
    timeout_s: float = 300.0,
) -> tuple[bool, str]:
    sample = helper.run_process(
        command,
        cwd=cwd,
        stdin_path=input_path,
        stdout_path=output_path,
        timeout_s=timeout_s,
    )
    try:
        digest = _digest_file(output_path)
    except OSError:
        digest = ""
    finally:
        _remove_quietly(output_path)
    if sample.returncode != 0:
        return False, "correctness_nonzero_exit"
    if digest != expected_digest:
        return False, "correctness_output_digest"
    return True, ""


def _prepare_sources(cell: CellSpec, temp: Path) -> tuple[Path, Path]:
    if cell.source_styio is None:
        styio_source = temp / "phase.styio"
        bindings = max(1, cell.size // 10)
        lines = ["v0 = 0\n"] + [f"v{i} = v{i - 1} + {i % 97}\n" for i in range(1, bindings + 1)] + [f">_(v{bindings})\n"]
        styio_source.write_text("".join(lines), encoding="ascii")
    else:
        styio_source = cell.source_styio
    if cell.source_cpp is None:
        cpp_source = temp / "phase.cpp"
        cpp_source.write_bytes(_phase_cpp_source(cell.size))
    else:
        cpp_source = cell.source_cpp
    return styio_source, cpp_source


def _phase_probe_path(toolchain: Mapping[str, Any]) -> Path | None:
    candidate = toolchain.get("phase_probe")
    if isinstance(candidate, Path) and candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def _parse_phase_probe_line(text: str, tier: str) -> dict[str, float]:
    """Parse the probe's compact numeric phase record without retaining text."""

    line = next((item for item in text.splitlines() if item.startswith("[parity-phase]")), "")
    if not line or f"tier={tier}" not in line:
        raise GateError("phase_probe_malformed")
    values: dict[str, float] = {}
    keys = {
        "tokenize": "tokenize_us",
        "parse": "parse_us",
        "semantic-analysis": "semantic_analysis_us",
        "lowering": "lowering_us",
        "llvm-emission": "llvm_emission_us",
    }
    for phase, key in keys.items():
        match = re.search(rf"(?:^|\s){re.escape(key)}=([0-9]+(?:\.[0-9]+)?)", line)
        if match is None:
            raise GateError("phase_probe_malformed")
        value = float(match.group(1)) / 1_000_000.0
        if not math.isfinite(value) or value <= 0:
            raise GateError("phase_probe_malformed")
        values[phase] = value
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
    """Map one Clang time-trace to the five declared attribution buckets."""

    buckets = {phase: 0.0 for phase in PHASES}
    for name, duration in events:
        lower = name.lower()
        if "codegen" in lower or "emit" in lower or "instruction" in lower:
            buckets["llvm-emission"] += duration
        elif "opt" in lower or "backend" in lower or "pass" in lower:
            buckets["lowering"] += duration
        elif "sema" in lower or "semantic" in lower or "instantiate" in lower or "type" in lower:
            buckets["semantic-analysis"] += duration
        elif "parse" in lower or "decl" in lower or "preprocess" in lower:
            buckets["parse"] += duration
        elif "frontend" in lower or "lex" in lower or "source" in lower:
            buckets["tokenize"] += duration
    # Clang versions differ in trace labels.  A missing bucket is filled from
    # the total frontend/backend event, never by running another compilation.
    total = sum(duration for _, duration in events)
    frontend = sum(duration for name, duration in events if "frontend" in name.lower())
    backend = sum(duration for name, duration in events if "backend" in name.lower())
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


def _run_phase_probe_once(
    *,
    helper: Any,
    probe: Path,
    styio_root: Path,
    tier: str,
    output: Path,
    timeout_s: float,
) -> tuple[dict[str, float], float]:
    """Run exactly one isolated Styio compiler pipeline for one phase sample."""

    env = os.environ.copy()
    env["STYIO_PARITY_SWEEP_TIER"] = tier
    # Keep one complete pipeline per isolated invocation.  The phase batch
    # scheduler below, not an in-process loop, establishes equal work with
    # Clang's one-translation-unit invocations.
    env["GTEST_COLOR"] = "0"
    sample = helper.run_process(
        [str(probe), "--gtest_filter=StyioSoakSingleThread.ParityPhaseSweepReport"],
        cwd=styio_root,
        stdout_path=output,
        timeout_s=timeout_s,
        env=env,
    )
    try:
        text = output.read_text(encoding="utf-8", errors="replace")
    finally:
        _remove_quietly(output)
    if sample.returncode != 0:
        raise GateError("phase_probe_failed")
    return _parse_phase_probe_line(text, tier), float(sample.peak_rss_kib)


def _run_clang_phase_once(
    *,
    helper: Any,
    cxx: Path,
    cwd: Path,
    source: Path,
    artifact: Path,
    timeout_s: float,
) -> tuple[dict[str, float], float]:
    """Run exactly one isolated Clang translation unit and classify its trace."""

    trace = artifact.with_suffix(".json")
    command = [
        str(cxx),
        "-std=c++20",
        "-O3",
        "-fno-lto",
        "-DNDEBUG",
        "-ftime-trace",
        "-c",
        str(source),
        "-o",
        str(artifact),
    ]
    sample = helper.run_process(command, cwd=cwd, timeout_s=timeout_s)
    if sample.returncode != 0:
        _remove_quietly(artifact)
        _remove_quietly(trace)
        raise GateError("phase_trace_failed")
    try:
        buckets = _classify_clang_phase(_phase_trace_events(trace))
    finally:
        _remove_quietly(artifact)
        _remove_quietly(trace)
    return buckets, float(sample.peak_rss_kib)


def _phase_round_robin_order(block: int, sample_index: int) -> tuple[str, str]:
    """Return the implementation order for one blocked retained pair."""

    return ("styio", "cpp") if (block + sample_index) % 2 == 0 else ("cpp", "styio")


def _native_run_round_robin_order(block: int, sample_index: int) -> tuple[str, str]:
    """Return the blocked order for one native-run retained pair."""

    return _phase_round_robin_order(block, sample_index)


def _cyclic_rotation_step(repetitions: int) -> int:
    """Choose a coprime rotation so short batches cover distinct positions."""

    if repetitions <= 1:
        return 1
    # Four gives the requested native-build offsets (0, 4, 8) for the frozen
    # eleven-sample cell.  Fall back to the first coprime step for other
    # repetition counts so a non-prime test cell cannot repeat one position.
    for candidate in (4, 3, 5, 2, 1):
        if math.gcd(candidate, repetitions) == 1:
            return candidate
    return 1


def _cyclic_latin_sample_order(block: int, repetitions: int, batch_count: int) -> tuple[int, ...]:
    """Return a cyclic Latin row for one blocked measurement.

    The inner position is deliberately independent of the sample identity.
    With ``batch_count == repetitions`` every retained sample appears once in
    each position across the rows.  Shorter native-build batches use the
    coprime rotation (0, 4, 8 for eleven samples) to avoid pinning samples to
    the same early/late slot while preserving equal work.
    """

    if repetitions <= 0 or batch_count <= 0:
        raise ValueError("repetitions and batch_count must be positive")
    step = _cyclic_rotation_step(repetitions)
    offset = (block % repetitions) if batch_count == repetitions else (block * step) % repetitions
    return tuple((offset + position) % repetitions for position in range(repetitions))


def _sample_schedule_provenance(
    batch_count: int,
    repetitions: int,
    *,
    scope: str = "blocked-operation",
) -> dict[str, Any]:
    """Describe cyclic sample positions without exposing host-specific data."""

    rows = [_cyclic_latin_sample_order(block, repetitions, batch_count) for block in range(batch_count)]
    coverage = {
        str(sample_index): sorted(position for row in rows for position, value in enumerate(row) if value == sample_index)
        for sample_index in range(repetitions)
    }
    return {
        "sample_schedule": "cyclic-latin-v1",
        "sample_schedule_scope": scope,
        "sample_schedule_rotation_step": _cyclic_rotation_step(repetitions),
        "position_coverage": coverage,
    }


def _compile_native_batch_count(route: str, tier: str, repetitions: int, calibrated: int) -> int:
    """Return the evidence-derived equal work count for baseline build routes.

    Compile-and-run and native-build retain a fresh optimized artifact for
    every operation.  Repetitions-sized compile-and-run cells use one audited
    eleven-row Latin block; native-build uses three rotated rows.  The large
    smoke tier stays at one operation.
    """

    if tier != "large" and repetitions >= 11:
        if route == "compile-and-run":
            return COMPILE_AND_RUN_BASELINE_BATCH_COUNT
        if route == "native-build":
            return COMPILE_NATIVE_BASELINE_BATCH_COUNT
    return calibrated


def _run_phase_samples(
    *,
    toolchain: Mapping[str, Any],
    styio_root: Path,
    cwd: Path,
    source: Path,
    tier: str,
    warmups: int,
    repetitions: int,
    timeout_s: float,
    work_root: Path,
    batch_count: int = PHASE_SAMPLE_BATCH_COUNT,
) -> dict[str, Any]:
    """Collect paired phase evidence with equal blocked round-robin work.

    ``batch_count`` is the total number of complete compiler pipelines per
    retained sample for *each* implementation.  The outer block loop keeps
    the two implementations interleaved across all retained samples, and the
    parity of ``block + sample`` alternates AB/BA to spread slow drift evenly.
    Warmups are isolated and discarded before any retained totals are filled.
    """

    probe = _phase_probe_path(toolchain)
    cxx = toolchain.get("cxx")
    if probe is None:
        raise GateError("phase_probe_missing")
    if not isinstance(cxx, Path) or not cxx.is_file():
        raise GateError("clang_toolchain_required")
    if batch_count <= 0 or repetitions <= 0 or warmups < 0:
        raise GateError("phase_batch_invalid")
    helper = _load_rss_helper()
    styio_totals = {phase: [0.0] * repetitions for phase in PHASES}
    cpp_totals = {phase: [0.0] * repetitions for phase in PHASES}
    styio_rss = [0.0] * repetitions
    cpp_rss = [0.0] * repetitions

    def collect(implementation: str, block: int, sample_index: int, label: str) -> None:
        if implementation == "styio":
            values, rss = _run_phase_probe_once(
                helper=helper,
                probe=probe,
                styio_root=styio_root,
                tier=tier,
                output=work_root / f"phase-probe-{label}-{block}-{sample_index}.out",
                timeout_s=timeout_s,
            )
            totals = styio_totals
            rss_values = styio_rss
        else:
            values, rss = _run_clang_phase_once(
                helper=helper,
                cxx=cxx,
                cwd=cwd,
                source=source,
                artifact=work_root / f"clang-phase-{label}-{block}-{sample_index}.o",
                timeout_s=timeout_s,
            )
            totals = cpp_totals
            rss_values = cpp_rss
        if label == "retained":
            for phase in PHASES:
                totals[phase][sample_index] += values[phase]
            rss_values[sample_index] = max(rss_values[sample_index], rss)

    # Warmups are intentionally outside the block/sample totals.  Their
    # balanced order avoids warming only one implementation first.
    for warmup_index in range(warmups):
        order = ("styio", "cpp") if warmup_index % 2 == 0 else ("cpp", "styio")
        for implementation in order:
            collect(implementation, warmup_index, 0, "warmup")

    # Cyclic Latin retained collection: each implementation performs exactly
    # ``batch_count`` complete pipelines for every retained sample, while each
    # sample rotates through every block position when the batch is a full
    # eleven-sample row.
    for block in range(batch_count):
        for sample_index in _cyclic_latin_sample_order(block, repetitions, batch_count):
            order = _phase_round_robin_order(block, sample_index)
            for implementation in order:
                collect(implementation, block, sample_index, "retained")

    styio_samples = {phase: [value / batch_count for value in values] for phase, values in styio_totals.items()}
    cpp_samples = {phase: [value / batch_count for value in values] for phase, values in cpp_totals.items()}
    provenance_common = {
        "sample_count": repetitions,
        "batch_count": batch_count,
        "warmups": warmups,
        "tier": tier,
        "round_robin": "blocked-sample",
        "normalized": True,
        **_sample_schedule_provenance(batch_count, repetitions),
    }
    return {
        "styio_time_s": styio_samples,
        "cpp_time_s": cpp_samples,
        "styio_raw_totals_s": styio_totals,
        "cpp_raw_totals_s": cpp_totals,
        "styio_peak_rss_kib": styio_rss,
        "cpp_peak_rss_kib": cpp_rss,
        "batch_count": batch_count,
        "provenance": {
            **provenance_common,
            "source": "styio_soak_test.ParityPhaseSweepReport",
            "boundary": "isolated-compiler-phase",
            "timing_source": "phase-probe-trace",
            "rss_source": "isolated-phase-process-tree-sample",
        },
        "cpp_provenance": {
            **provenance_common,
            "source": "clang-time-trace",
            "boundary": "clang-frontend-optimizer-codegen",
            "timing_source": "clang-time-trace",
            "rss_source": "isolated-phase-process-tree-sample",
        },
    }


def _phase_evidence_noise_reasons(
    evidence: Mapping[str, Any],
    repetitions: int,
    *,
    max_cv_pct: float | None = STABILITY_CV_LIMIT_PCT,
) -> list[str]:
    """Check every shared phase bucket before any phase cell is selected."""

    reasons: list[str] = []
    for implementation, key in (("styio", "styio_time_s"), ("cpp", "cpp_time_s")):
        by_phase = evidence.get(key)
        if not isinstance(by_phase, Mapping):
            reasons.append("noise_cv")
            continue
        for phase in PHASES:
            values = by_phase.get(phase)
            if not isinstance(values, list) or len(values) != repetitions or any(
                not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0
                for value in values
            ):
                reasons.append("noise_cv")
                continue
            if max_cv_pct is not None and sample_cv_pct(values) > max_cv_pct:
                reasons.append("noise_cv")
    if isinstance(evidence.get("error"), str):
        reasons.append(str(evidence["error"]))
    return sorted(set(reasons))


def _phase_evidence_is_stable(
    evidence: Mapping[str, Any],
    repetitions: int,
    *,
    max_cv_pct: float | None = STABILITY_CV_LIMIT_PCT,
) -> bool:
    return not _phase_evidence_noise_reasons(evidence, repetitions, max_cv_pct=max_cv_pct)


def _phase_attempt_audit_record(
    evidence: Mapping[str, Any],
    *,
    attempt: int,
    repetitions: int,
    max_cv_pct: float | None = STABILITY_CV_LIMIT_PCT,
) -> dict[str, Any]:
    """Serialize one whole-tier phase attempt without paths or subprocess text."""

    styio = evidence.get("styio_time_s") if isinstance(evidence.get("styio_time_s"), Mapping) else {}
    cpp = evidence.get("cpp_time_s") if isinstance(evidence.get("cpp_time_s"), Mapping) else {}
    styio_totals = evidence.get("styio_raw_totals_s") if isinstance(evidence.get("styio_raw_totals_s"), Mapping) else {}
    cpp_totals = evidence.get("cpp_raw_totals_s") if isinstance(evidence.get("cpp_raw_totals_s"), Mapping) else {}
    styio_rss = evidence.get("styio_peak_rss_kib") if isinstance(evidence.get("styio_peak_rss_kib"), list) else []
    cpp_rss = evidence.get("cpp_peak_rss_kib") if isinstance(evidence.get("cpp_peak_rss_kib"), list) else []
    cv_values = {
        implementation: {
            phase: (
                sample_cv_pct(values)
                if isinstance(values, list)
                and len(values) >= 2
                and all(isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0 for value in values)
                else None
            )
            for phase, values in ((phase, source.get(phase, [])) for phase in PHASES)
        }
        for implementation, source in (("styio", styio), ("cpp", cpp))
    }
    reasons = _phase_evidence_noise_reasons(evidence, repetitions, max_cv_pct=max_cv_pct)
    return {
        "attempt": int(attempt),
        "status": "pass" if not reasons else "rejected",
        "time_samples_s": {
            "styio": {phase: list(styio.get(phase, [])) if isinstance(styio.get(phase, []), list) else [] for phase in PHASES},
            "cpp": {phase: list(cpp.get(phase, [])) if isinstance(cpp.get(phase, []), list) else [] for phase in PHASES},
        },
        "raw_time_totals_s": {
            "styio": {phase: list(styio_totals.get(phase, [])) if isinstance(styio_totals.get(phase, []), list) else [] for phase in PHASES},
            "cpp": {phase: list(cpp_totals.get(phase, [])) if isinstance(cpp_totals.get(phase, []), list) else [] for phase in PHASES},
        },
        "peak_rss_samples_kib": {"styio": list(styio_rss), "cpp": list(cpp_rss)},
        "time_cv_pct": cv_values,
        "reason_codes": reasons,
    }


def _measure_phase_cell(
    cell: CellSpec,
    *,
    toolchain: Mapping[str, Any],
    cwd: Path,
    work_root: Path,
    warmups: int,
    repetitions: int,
    timeout_s: float,
    phase_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Assemble one phase cell from shared real probe/Clang samples."""

    styio_samples = list(phase_evidence.get("styio_time_s", {}).get(cell.phase or "", []))
    cpp_samples = list(phase_evidence.get("cpp_time_s", {}).get(cell.phase or "", []))
    styio_rss = list(phase_evidence.get("styio_peak_rss_kib", []))
    cpp_rss = list(phase_evidence.get("cpp_peak_rss_kib", []))
    reasons: list[str] = []
    if isinstance(phase_evidence.get("error"), str):
        reasons.append(str(phase_evidence["error"]))
    cv_limit = STABILITY_CV_LIMIT_PCT if repetitions >= 11 else None
    reasons.extend(_phase_evidence_noise_reasons(phase_evidence, repetitions, max_cv_pct=cv_limit))
    if repetitions >= 11 and phase_evidence.get("stability_selected_attempt") is None and phase_evidence.get("stability_attempts"):
        reasons.append("stability_attempts_exhausted")
    if len(styio_samples) != repetitions or len(cpp_samples) != repetitions:
        reasons.append("missing_phase_samples")
    if len(styio_rss) != repetitions or len(cpp_rss) != repetitions:
        reasons.append("non_isolated_rss")
    if any(not isinstance(value, (int, float)) or value <= 0 for value in (*styio_samples, *cpp_samples)):
        reasons.append("malformed_phase_samples")
    # Phase correctness is checked against the same generated output oracle;
    # actual timing samples come exclusively from the isolated phase sources.
    validation = {"styio": False, "cpp": False}
    styio_source, cpp_source = _prepare_sources(cell, work_root)
    input_path = work_root / "input.bin"
    input_path.write_bytes(cell.input_bytes)
    helper = _load_rss_helper()
    output = work_root / "phase-preflight.out"
    styio_artifact = work_root / "phase-styio-preflight"
    try:
        _, _, styio_code = _run_build_command(
            _styio_build_command(toolchain["styio"], styio_source, styio_artifact),
            cwd=cwd,
            helper=helper,
            timeout_s=timeout_s,
        )
        if styio_code == 0 and styio_artifact.is_file():
            validation["styio"] = _validate_output(
                [str(styio_artifact)],
                cwd=cwd,
                input_path=input_path,
                expected_digest=cell.expected_digest,
                helper=helper,
                output_path=output,
                timeout_s=timeout_s,
            )[0]
        else:
            reasons.append("phase_styio_build_failed")
    except TimeoutError:
        reasons.append("timed_out")
    finally:
        _remove_quietly(styio_artifact)
    artifact = work_root / "phase-cpp-preflight"
    try:
        _, _, code = _run_build_command(
            _cpp_command(toolchain["cxx"], cpp_source, artifact),
            cwd=cwd,
            helper=helper,
            timeout_s=timeout_s,
        )
        if code == 0:
            validation["cpp"] = _validate_output(
                [str(artifact)],
                cwd=cwd,
                input_path=input_path,
                expected_digest=cell.expected_digest,
                helper=helper,
                output_path=output,
                timeout_s=timeout_s,
            )[0]
        else:
            reasons.append("phase_cpp_build_failed")
    except TimeoutError:
        reasons.append("timed_out")
    finally:
        _remove_quietly(artifact)
    if validation != {"styio": True, "cpp": True}:
        reasons.append("correctness_failed")
    orders = ["AB" if index % 2 == 0 else "BA" for index in range(repetitions)]
    status = "pass" if not reasons else "incomplete"
    phase_batch_count = int(phase_evidence.get("batch_count", 1))
    sample_schedule = _sample_schedule_provenance(phase_batch_count, repetitions)
    record: dict[str, Any] = {
        "id": cell.cell_id,
        "family": cell.family,
        "tier": cell.tier,
        "size": cell.size,
        "route": cell.route,
        "focus_owner": cell.focus_owner,
        "phase": cell.phase,
        "status": status,
        "correctness": validation,
        "expected_output_digest": cell.expected_digest,
        "source_digests": cell.source_digest,
        "input_digest": _sha256(cell.input_bytes),
        "balanced_order": orders,
        "repetitions": repetitions,
        "warmups": warmups,
        "batch_count": phase_batch_count,
        **sample_schedule,
        "calibration": {
            "minimum_duration_s": 0.0,
            "estimates_s": {},
            "max_batch": phase_batch_count,
            "cap_exhausted": False,
            "normalized": True,
        },
        "phase_provenance": {
            "styio": phase_evidence.get("styio_provenance", {}),
            "cpp": phase_evidence.get("cpp_provenance", {}),
            "shared_tier_samples": True,
        },
        "stability": {
            "schema": "styio.parity.stability.v1",
            "attempt_limit": int(phase_evidence.get("stability_attempt_limit", 1)),
            "attempt_count": len(phase_evidence.get("stability_attempts", [])),
            "selected_attempt": phase_evidence.get("stability_selected_attempt"),
            "cv_limit_pct": STABILITY_CV_LIMIT_PCT,
            "first_valid": phase_evidence.get("stability_selected_attempt") is not None,
        },
        "stability_attempts": list(phase_evidence.get("stability_attempts", [])),
        "time_samples_s": {"styio": styio_samples, "cpp": cpp_samples},
        "raw_time_totals_s": {
            "styio": {
                phase: list(phase_evidence.get("styio_raw_totals_s", {}).get(phase, []))
                for phase in PHASES
            },
            "cpp": {
                phase: list(phase_evidence.get("cpp_raw_totals_s", {}).get(phase, []))
                for phase in PHASES
            },
        },
        "peak_rss_samples_kib": {"styio": styio_rss, "cpp": cpp_rss},
        "reason_codes": sorted(set(reasons)),
    }
    record["samples"] = {
        "time_s": {"styio": styio_samples, "cpp": cpp_samples},
        "peak_rss_kib": {"styio": styio_rss, "cpp": cpp_rss},
    }
    if status == "pass":
        record["time"] = _ratio_dimension(styio_samples, cpp_samples)
        record["peak_rss"] = _ratio_dimension(
            [max(value, 0.001) for value in styio_rss],
            [max(value, 0.001) for value in cpp_rss],
        )
        record["throughput"] = {
            "styio_samples": [],
            "cpp_samples": [],
            "median_ratio": 1.0,
            "geomean_ratio": 1.0,
            "paired_log_ratios": [],
        }
        record["samples"]["throughput_mib_s"] = {"styio": [0.0] * repetitions, "cpp": [0.0] * repetitions}
        record["paired_log_ratios"] = {
            "time": record["time"]["paired_log_ratios"],
            "throughput": [],
            "peak_rss": record["peak_rss"]["paired_log_ratios"],
        }
        record["median_ratios"] = {
            "time": record["time"]["median_ratio"],
            "throughput": 1.0,
            "peak_rss": record["peak_rss"]["median_ratio"],
        }
    return record


def _measure_cell_once(
    cell: CellSpec,
    *,
    toolchain: Mapping[str, Any],
    cwd: Path,
    work_root: Path,
    warmups: int,
    repetitions: int,
    timeout_s: float,
    phase_evidence: Mapping[str, Any] | None = None,
    calibration_min_duration_s: float = CALIBRATION_MIN_DURATION_S,
) -> dict[str, Any]:
    """Measure one route with correctness-first, equal-work samples.

    The route table is intentionally direct.  A measured compile-and-run
    sample always builds a fresh optimized native artifact and then executes
    it; a native-run sample only executes an artifact built outside timing.
    """

    if cell.family == "compiler-phase":
        if phase_evidence is None:
            return {
                "id": cell.cell_id,
                "family": cell.family,
                "tier": cell.tier,
                "size": cell.size,
                "route": cell.route,
                "focus_owner": cell.focus_owner,
                "phase": cell.phase,
                "status": "incomplete",
                "correctness": {"styio": False, "cpp": False},
                "expected_output_digest": cell.expected_digest,
                "source_digests": cell.source_digest,
                "input_digest": _sha256(cell.input_bytes),
                "balanced_order": [],
                "repetitions": repetitions,
                "warmups": warmups,
                "time_samples_s": {"styio": [], "cpp": []},
                "peak_rss_samples_kib": {"styio": [], "cpp": []},
                "reason_codes": ["phase_evidence_missing"],
            }
        return _measure_phase_cell(
            cell,
            toolchain=toolchain,
            cwd=cwd,
            work_root=work_root,
            warmups=warmups,
            repetitions=repetitions,
            timeout_s=timeout_s,
            phase_evidence=phase_evidence,
        )

    helper = _load_rss_helper()
    styio_source, cpp_source = _prepare_sources(cell, work_root)
    input_path = work_root / "input.bin"
    input_path.write_bytes(cell.input_bytes)
    validation: dict[str, bool] = {}
    preflight_output = work_root / "preflight.out"
    failure_reasons: list[str] = []

    def _failure(reason: str) -> None:
        if reason not in failure_reasons:
            failure_reasons.append(reason)

    # Correctness is validated with disposable native artifacts that follow
    # the declared route boundary.  In particular, compile-and-run must not
    # execute a Styio source through the JIT before the native artifact path.
    validation["styio"] = False
    validation["cpp"] = False

    styio_times: list[float] = []
    cpp_times: list[float] = []
    styio_rss: list[float] = []
    cpp_rss: list[float] = []
    orders: list[str] = []
    batch_harness = False
    def build(implementation: str, label: str) -> tuple[Path | None, float, float, int]:
        artifact = work_root / f"{label}-{implementation}"
        if implementation == "styio":
            command = _styio_build_command(toolchain["styio"], styio_source, artifact)
        else:
            command = _cpp_command(toolchain["cxx"], cpp_source, artifact)
        elapsed, rss, code = _run_build_command(command, cwd=cwd, helper=helper, timeout_s=timeout_s)
        if code != 0 or not artifact.is_file():
            _remove_quietly(artifact)
            return None, elapsed, rss, code
        try:
            artifact.chmod(artifact.stat().st_mode | 0o111)
        except OSError:
            pass
        return artifact, elapsed, rss, code

    def execute_once(
        implementation: str,
        sample_index: int,
        batch_index: int,
        artifact: Path | None = None,
        *,
        sample_process_tree: bool = False,
    ) -> tuple[float, float, int]:
        output = work_root / f"sample-{sample_index}-{batch_index}-{implementation}.out"
        if cell.route == "compile-and-run":
            fresh_artifact = work_root / f"sample-{sample_index}-{batch_index}-{implementation}"
            if implementation == "styio":
                build_command = _styio_build_command(toolchain["styio"], styio_source, fresh_artifact)
            else:
                build_command = _cpp_command(toolchain["cxx"], cpp_source, fresh_artifact)
            built_elapsed, built_rss, built_code = _run_build_command(
                build_command,
                cwd=cwd,
                helper=helper,
                timeout_s=timeout_s,
                sample_process_tree=sample_process_tree,
            )
            if built_code != 0:
                _remove_quietly(fresh_artifact)
                return built_elapsed, built_rss, built_code
            try:
                executed = helper.run_process(
                    [str(fresh_artifact)],
                    cwd=cwd,
                    stdin_path=input_path,
                    stdout_path=output,
                    timeout_s=timeout_s,
                    sample_process_tree=sample_process_tree,
                )
                return (
                    built_elapsed + executed.elapsed_s,
                    max(built_rss, executed.peak_rss_kib),
                    executed.returncode,
                )
            finally:
                _remove_quietly(fresh_artifact)
        if cell.route == "native-build":
            fresh_artifact = work_root / f"sample-{sample_index}-{batch_index}-{implementation}"
            if implementation == "styio":
                command = _styio_build_command(toolchain["styio"], styio_source, fresh_artifact)
            else:
                command = _cpp_command(toolchain["cxx"], cpp_source, fresh_artifact)
            try:
                return _run_build_command(
                    command,
                    cwd=cwd,
                    helper=helper,
                    timeout_s=timeout_s,
                    sample_process_tree=sample_process_tree,
                )
            finally:
                _remove_quietly(fresh_artifact)
        assert cell.route == "native-run"
        if artifact is None:
            return 0.0, 0.0, 127
        return _run_timed_process(
            [str(artifact)],
            cwd=cwd,
            input_path=input_path,
            output_path=output,
            helper=helper,
            timeout_s=timeout_s,
            sample_process_tree=sample_process_tree,
        )

    def _batch_build_helper_command(
        implementation: str,
        sample_index: int,
        batch_count: int,
        *,
        run_after_build: bool,
        output: Path | None,
    ) -> tuple[list[str], Path]:
        """Construct an argv-only fresh-build batch command.

        The helper receives a JSON argv template containing one literal
        ``{artifact}`` token.  It substitutes a private per-iteration path,
        so no shell quoting or artifact reuse can enter the timed boundary.
        """

        artifact_token = "{artifact}"
        template_artifact = Path(artifact_token)
        if implementation == "styio":
            build_template = _styio_build_command(toolchain["styio"], styio_source, template_artifact)
        else:
            build_template = _cpp_command(toolchain["cxx"], cpp_source, template_artifact)
        artifact_dir = work_root / f"batch-build-{sample_index}-{implementation}"
        helper_command = [
            sys.executable,
            str(HELPER_ROOT / "process_tree_rss.py"),
            "--batch-build-run" if run_after_build else "--batch-build",
            "--build-argv-json",
            json.dumps(build_template, separators=(",", ":"), ensure_ascii=True),
            "--artifact-dir",
            str(artifact_dir),
            "--artifact-token",
            artifact_token,
            "--count",
            str(batch_count),
        ]
        if run_after_build:
            if output is None:
                raise ValueError("batch build-run output is required")
            helper_command.extend(("--input", str(input_path), "--output", str(output)))
        return helper_command, artifact_dir

    def execute_batch(
        implementation: str,
        sample_index: int,
        batch_count: int,
        artifact: Path | None = None,
        *,
        validate_outputs: bool = False,
        sample_process_tree: bool = False,
    ) -> tuple[float, float, int]:
        if (
            cell.route in {"compile-and-run", "native-build"}
            and batch_count > 1
            and not sample_process_tree
        ):
            output = work_root / f"sample-{sample_index}-0-{implementation}.out" if cell.route == "compile-and-run" else None
            command, artifact_dir = _batch_build_helper_command(
                implementation,
                sample_index,
                batch_count,
                run_after_build=cell.route == "compile-and-run",
                output=output,
            )
            try:
                sample = helper.run_process(
                    command,
                    cwd=cwd,
                    timeout_s=timeout_s,
                    sample_process_tree=False,
                )
                if (
                    validate_outputs
                    and cell.route == "compile-and-run"
                    and sample.returncode == 0
                    and output is not None
                ):
                    try:
                        if _digest_file(output) != cell.expected_digest:
                            _failure("correctness_output_digest")
                    except OSError:
                        _failure("correctness_output_missing")
                return sample.elapsed_s, sample.peak_rss_kib, sample.returncode
            finally:
                _remove_quietly(output) if output is not None else None
                shutil.rmtree(artifact_dir, ignore_errors=True)
        if batch_harness and batch_count > 1:
            # A longer-running executable benefits from one argv-safe harness
            # interval: sampler/process-launch overhead is then amortized,
            # while every child still receives the same input and its exit is
            # checked by process_tree_rss.py. Very short executables stay on
            # the direct loop below because harness startup jitter would
            # dominate their normalized samples.
            if artifact is None:
                return 0.0, 0.0, 127
            output = work_root / f"sample-{sample_index}-0-{implementation}.out"
            sample = helper.run_process(
                [
                    sys.executable,
                    str(HELPER_ROOT / "process_tree_rss.py"),
                    "--batch-exec",
                    str(artifact),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output),
                    "--count",
                    str(batch_count),
                ],
                cwd=cwd,
                timeout_s=timeout_s,
                sample_process_tree=sample_process_tree,
            )
            _remove_quietly(output)
            return sample.elapsed_s, sample.peak_rss_kib, sample.returncode
        elapsed = 0.0
        peak = 0.0
        code = 0
        for batch_index in range(batch_count):
            one_elapsed, one_peak, one_code = execute_once(
                implementation,
                sample_index,
                batch_index,
                artifact,
                sample_process_tree=sample_process_tree,
            )
            elapsed += one_elapsed
            peak = max(peak, one_peak)
            output = work_root / f"sample-{sample_index}-{batch_index}-{implementation}.out"
            if (
                validate_outputs
                and one_code == 0
                and cell.route in {"compile-and-run", "native-run"}
                and not (cell.route == "native-run" and batch_count > 1 and batch_harness)
            ):
                try:
                    if _digest_file(output) != cell.expected_digest:
                        _failure("correctness_output_digest")
                except OSError:
                    _failure("correctness_output_missing")
            _remove_quietly(output)
            if one_code != 0:
                code = one_code
                break
        return elapsed, peak, code

    # Correctness and artifact construction are explicitly outside measured
    # native-run samples; compile-and-run and native-build intentionally include
    # their fresh source work in each timed sample.
    prebuilt: dict[str, Path] = {}
    if cell.route == "native-run":
        for implementation in ("styio", "cpp"):
            try:
                artifact, _, _, code = build(implementation, "prebuilt")
            except TimeoutError:
                artifact, code = None, 124
                _failure("timed_out")
            if artifact is None:
                _failure(f"{implementation}_build_failed")
            else:
                prebuilt[implementation] = artifact
        if len(prebuilt) == 2:
            for implementation in ("styio", "cpp"):
                try:
                    validation[implementation] = _validate_output(
                        [str(prebuilt[implementation])],
                        cwd=cwd,
                        input_path=input_path,
                        expected_digest=cell.expected_digest,
                        helper=helper,
                        output_path=preflight_output,
                        timeout_s=timeout_s,
                    )[0]
                except TimeoutError:
                    validation[implementation] = False
                    _failure("timed_out")
    elif cell.route in {"compile-and-run", "native-build"}:
        # Both route variants validate a disposable artifact for each language
        # outside retained timing.  The timed route then rebuilds the native
        # artifact per sample, preserving the declared build/run boundary.
        for implementation in ("styio", "cpp"):
            try:
                artifact, _, _, code = build(implementation, "validate")
            except TimeoutError:
                artifact, code = None, 124
                _failure("timed_out")
            if artifact is None:
                _failure(f"{implementation}_build_failed")
                continue
            try:
                validation[implementation] = _validate_output(
                    [str(artifact)],
                    cwd=cwd,
                    input_path=input_path,
                    expected_digest=cell.expected_digest,
                    helper=helper,
                    output_path=preflight_output,
                    timeout_s=timeout_s,
                )[0]
            except TimeoutError:
                _failure("timed_out")
            finally:
                _remove_quietly(artifact)

    if not all(validation.values()):
        _failure("correctness_failed")

    # Calibrate only when a single invocation is genuinely short.  Large
    # compile cells are intentionally kept at one operation to avoid adding a
    # second full build merely to decide that batching is unnecessary.
    calibration_estimates: dict[str, float] = {}
    batch_count = 1
    batch_cap_exhausted = False
    if cell.tier != "large":
        for implementation in ("styio", "cpp"):
            artifact = prebuilt.get(implementation)
            try:
                elapsed, _, code = execute_batch(implementation, -1, 1, artifact)
            except TimeoutError:
                elapsed, code = 0.0, 124
                _failure("timed_out")
            if code == 0 and elapsed > 0:
                calibration_estimates[implementation] = elapsed
            else:
                _failure("calibration_failed")
        if len(calibration_estimates) == 2 and max(calibration_estimates.values()) < SHORT_CELL_MAX_ESTIMATE_S:
            fastest = min(calibration_estimates.values())
            uncapped_batch_count = math.ceil(calibration_min_duration_s / fastest)
            batch_count = calibrate_batch_count(
                tuple(calibration_estimates.values()),
                minimum_duration_s=calibration_min_duration_s,
            )
            batch_cap_exhausted = uncapped_batch_count > CALIBRATION_MAX_BATCH
            if batch_cap_exhausted:
                _failure("stability_batch_cap_exhausted")
    # Keep direct per-operation intervals for short executables, where the
    # harness launch itself is a material fraction of elapsed time. Stream
    # workloads and other longer native-run operations use the argv-safe
    # process-tree batch harness once calibration proves the distinction.
    batch_harness = bool(
        cell.route == "native-run"
        and calibration_estimates
        and max(calibration_estimates.values()) >= 0.050
    )

    # Baseline-sized compile-and-run/native-build cells must receive the same
    # bounded amount of fresh optimized work on each side.  A calibrated count
    # can be very large for a short compiler on a quiet host, which leaves a
    # whole retained sample exposed to one cache/scheduler episode.  The
    # compile-and-run route uses one eleven-row Latin block; native-build uses
    # three coprime-rotated rows.  The large tier remains one fresh artifact
    # for its frozen smoke budget.
    calibrated_batch_count = batch_count
    batch_count = _compile_native_batch_count(cell.route, cell.tier, repetitions, calibrated_batch_count)
    compile_native_blocked = (
        cell.tier != "large"
        and cell.route in {"compile-and-run", "native-build"}
        and repetitions >= 11
    )
    native_run_blocked = cell.route == "native-run" and batch_count > 1
    blocked_measure = native_run_blocked or compile_native_blocked

    # Warmups follow the same route boundaries but never enter retained arrays.
    # Long native-run cells use the same block scheduler as retained samples so
    # one wrapper can never monopolize a complete warmup/sample interval.
    if blocked_measure:
        for warmup_index in range(warmups):
            if compile_native_blocked:
                order = _native_run_round_robin_order(0, warmup_index)
                for implementation in order:
                    try:
                        _elapsed, _rss, code = execute_batch(
                            implementation,
                            warmup_index,
                            batch_count,
                            None,
                            sample_process_tree=False,
                        )
                    except TimeoutError:
                        code = 124
                        _failure("timed_out")
                    if code != 0:
                        _failure("warmup_failed")
                continue
            for block in range(batch_count):
                order = _native_run_round_robin_order(block, warmup_index)
                for implementation in order:
                    artifact = prebuilt.get(implementation)
                    if cell.route == "native-run" and artifact is None:
                        _failure("warmup_missing_artifact")
                        continue
                    try:
                        _elapsed, _rss, code = execute_once(
                            implementation,
                            warmup_index,
                            block,
                            artifact,
                            sample_process_tree=False,
                        )
                    except TimeoutError:
                        code = 124
                        _failure("timed_out")
                    if code != 0:
                        _failure("warmup_failed")
    else:
        for warmup_index in range(warmups):
            order = ("styio", "cpp") if warmup_index % 2 == 0 else ("cpp", "styio")
            artifacts: dict[str, Path | None] = dict(prebuilt)
            if cell.route in {"compile-and-run", "native-build"}:
                artifacts = {}
            for implementation in order:
                artifact = artifacts.get(implementation)
                if cell.route == "native-run" and artifact is None:
                    continue
                if cell.route != "native-run":
                    artifact = work_root / f"warmup-{warmup_index}-{implementation}"
                try:
                    _elapsed, _rss, code = execute_batch(implementation, warmup_index, batch_count, artifact)
                except TimeoutError:
                    code = 124
                    _failure("timed_out")
                if code != 0:
                    _failure("warmup_failed")

    if blocked_measure:
        # Cyclic Latin retained collection.  Build routes use one argv-safe
        # helper per retained sample; that helper performs exactly
        # ``batch_count`` fresh artifacts internally.  Native-run keeps the
        # direct per-block loop because its artifact is already built.
        styio_elapsed_totals = [0.0] * repetitions
        cpp_elapsed_totals = [0.0] * repetitions
        styio_complete = [True] * repetitions
        cpp_complete = [True] * repetitions
        if compile_native_blocked:
            for pair_index in _cyclic_latin_sample_order(0, repetitions, batch_count):
                # Block zero keeps AB/BA balanced by sample while the helper's
                # internal fresh-operation count remains equal on both sides.
                order = _native_run_round_robin_order(0, pair_index)
                orders.append("AB" if order == ("styio", "cpp") else "BA")
                for implementation in order:
                    try:
                        elapsed, _rss, code = execute_batch(
                            implementation,
                            pair_index,
                            batch_count,
                            None,
                            validate_outputs=cell.route == "compile-and-run",
                            sample_process_tree=False,
                        )
                    except TimeoutError:
                        elapsed, code = 0.0, 124
                        _failure("timed_out")
                    complete = styio_complete if implementation == "styio" else cpp_complete
                    elapsed_totals = styio_elapsed_totals if implementation == "styio" else cpp_elapsed_totals
                    if code != 0:
                        complete[pair_index] = False
                        _failure("measured_process_failed")
                        continue
                    elapsed_totals[pair_index] = elapsed
        else:
            for pair_index in range(repetitions):
                orders.append("AB" if pair_index % 2 == 0 else "BA")
            for block in range(batch_count):
                for pair_index in _cyclic_latin_sample_order(block, repetitions, batch_count):
                    order = _native_run_round_robin_order(block, pair_index)
                    for implementation in order:
                        artifact = prebuilt.get(implementation)
                        if cell.route == "native-run" and artifact is None:
                            failure_reasons.append("missing_artifact")
                            (styio_complete if implementation == "styio" else cpp_complete)[pair_index] = False
                            continue
                        try:
                            elapsed, _rss, code = execute_once(
                                implementation,
                                pair_index,
                                block,
                                artifact,
                                sample_process_tree=False,
                            )
                        except TimeoutError:
                            elapsed, code = 0.0, 124
                            _failure("timed_out")
                        complete = styio_complete if implementation == "styio" else cpp_complete
                        elapsed_totals = styio_elapsed_totals if implementation == "styio" else cpp_elapsed_totals
                        if code != 0:
                            complete[pair_index] = False
                            _failure("measured_process_failed")
                            continue
                        output = work_root / f"sample-{pair_index}-{block}-{implementation}.out"
                        _remove_quietly(output)
                        elapsed_totals[pair_index] += elapsed
        for pair_index in range(repetitions):
            if styio_complete[pair_index]:
                styio_times.append(normalize_batched_elapsed(styio_elapsed_totals[pair_index], batch_count))
            if cpp_complete[pair_index]:
                cpp_times.append(normalize_batched_elapsed(cpp_elapsed_totals[pair_index], batch_count))
    else:
        for pair_index in range(repetitions):
            order = ("styio", "cpp") if pair_index % 2 == 0 else ("cpp", "styio")
            orders.append("AB" if order == ("styio", "cpp") else "BA")
            artifacts: dict[str, Path | None] = dict(prebuilt)
            for implementation in order:
                artifact = artifacts.get(implementation)
                if cell.route != "native-run":
                    artifact = work_root / f"pair-{pair_index}-{implementation}"
                if cell.route == "native-run" and artifact is None:
                    failure_reasons.append("missing_artifact")
                    continue
                try:
                    elapsed, rss, code = execute_batch(
                        implementation,
                        pair_index,
                        batch_count,
                        artifact,
                        validate_outputs=True,
                        sample_process_tree=False,
                    )
                except TimeoutError:
                    elapsed, rss, code = 0.0, 0.0, 124
                    _failure("timed_out")
                if code != 0:
                    _failure("measured_process_failed")
                    continue
                if implementation == "styio":
                    styio_times.append(normalize_batched_elapsed(elapsed, batch_count))
                else:
                    cpp_times.append(normalize_batched_elapsed(elapsed, batch_count))

    # RSS is deliberately collected in an equivalent second pass.  The
    # retained time arrays above are observer-free wait4 intervals; replay
    # starts one equivalent route command per retained sample (RSS is a peak,
    # not an additive work quantity), but enables the process-tree sampler and
    # ignores elapsed.  Correctness remains anchored by the independent
    # disposable preflight.
    rss_replay_count = 0
    rss_replay_batch_count = 0
    rss_replay_complete = False
    if len(styio_times) == repetitions and len(cpp_times) == repetitions:
        replay_styio_rss = [0.0] * repetitions
        replay_cpp_rss = [0.0] * repetitions
        replay_styio_complete = [True] * repetitions
        replay_cpp_complete = [True] * repetitions
        for pair_index in range(repetitions):
            order = ("styio", "cpp") if pair_index % 2 == 0 else ("cpp", "styio")
            for implementation in order:
                artifact = prebuilt.get(implementation)
                if cell.route == "native-run" and artifact is None:
                    (replay_styio_complete if implementation == "styio" else replay_cpp_complete)[pair_index] = False
                    _failure("rss_replay_missing_artifact")
                    continue
                try:
                    _elapsed, rss, code = execute_once(
                        implementation,
                        pair_index,
                        0,
                        artifact,
                        sample_process_tree=True,
                    )
                except TimeoutError:
                    _elapsed, rss, code = 0.0, 0.0, 124
                    _failure("rss_replay_timed_out")
                complete = replay_styio_complete if implementation == "styio" else replay_cpp_complete
                replay_peaks = replay_styio_rss if implementation == "styio" else replay_cpp_rss
                output = work_root / f"sample-{pair_index}-0-{implementation}.out"
                _remove_quietly(output)
                if code != 0:
                    complete[pair_index] = False
                    _failure("rss_replay_failed")
                    continue
                replay_peaks[pair_index] = max(replay_peaks[pair_index], rss)
        if all(replay_styio_complete) and all(replay_cpp_complete):
            styio_rss = replay_styio_rss
            cpp_rss = replay_cpp_rss
            rss_replay_count = repetitions
            rss_replay_batch_count = RSS_REPLAY_BATCH_COUNT
            rss_replay_complete = True
        else:
            _failure("rss_replay_incomplete")
    else:
        _failure("rss_replay_skipped")

    if len(styio_times) != repetitions or len(cpp_times) != repetitions:
        _failure("missing_samples")
    status = "pass" if not failure_reasons else "incomplete"
    sample_schedule = _sample_schedule_provenance(
        batch_count,
        repetitions,
        scope="sample-level-harness" if compile_native_blocked else "blocked-operation",
    )
    record: dict[str, Any] = {
        "id": cell.cell_id,
        "family": cell.family,
        "tier": cell.tier,
        "size": cell.size,
        "route": cell.route,
        "focus_owner": cell.focus_owner,
        "phase": cell.phase,
        "status": status,
        "correctness": validation,
        "expected_output_digest": cell.expected_digest,
        "source_digests": cell.source_digest,
        "input_digest": _sha256(cell.input_bytes),
        "balanced_order": orders,
        "repetitions": repetitions,
        "warmups": warmups,
        "batch_count": batch_count,
        **sample_schedule,
        "calibration": {
            "minimum_duration_s": calibration_min_duration_s,
            "estimates_s": calibration_estimates,
            "max_batch": CALIBRATION_MAX_BATCH,
            "cap_exhausted": batch_cap_exhausted,
            "batch_harness": batch_harness,
            "normalized": True,
        },
        "measurement_provenance": {
            "time": {
                "source": "wait4-only",
                "process_tree_sampler": False,
                "time_batch_count": batch_count,
                "batch_count": batch_count,
                "normalized": True,
            },
            "peak_rss": {
                "source": "isolated-replay",
                "process_tree_sampler": True,
                "replay_count": rss_replay_count,
                "replay_operations": rss_replay_count * rss_replay_batch_count,
                "rss_replay_batch_count": rss_replay_batch_count,
                "same_route_identity": True,
                "same_ab_ba_order": True,
                "complete": rss_replay_complete,
            },
        },
        "time_batch_count": batch_count,
        "rss_replay_batch_count": rss_replay_batch_count,
        "time_samples_s": {"styio": styio_times, "cpp": cpp_times},
        "peak_rss_samples_kib": {"styio": styio_rss, "cpp": cpp_rss},
        "reason_codes": sorted(set(failure_reasons)),
    }
    record["samples"] = {
        "time_s": {"styio": styio_times, "cpp": cpp_times},
        "peak_rss_kib": {"styio": styio_rss, "cpp": cpp_rss},
    }
    if len(styio_times) == repetitions and len(cpp_times) == repetitions and all(styio_times) and all(cpp_times):
        time_stats = _ratio_dimension(styio_times, cpp_times)
        rss_values = [max(item, 0.001) for item in styio_rss], [max(item, 0.001) for item in cpp_rss]
        record["time"] = time_stats
        record["peak_rss"] = _ratio_dimension(*rss_values)
        record["throughput_samples_mib_s"] = {
            "styio": [(len(cell.input_bytes) / (1024 * 1024)) / value if value > 0 else 0.0 for value in styio_times],
            "cpp": [(len(cell.input_bytes) / (1024 * 1024)) / value if value > 0 else 0.0 for value in cpp_times],
        }
        if len(cell.input_bytes) > 0:
            record["throughput"] = _ratio_dimension(record["throughput_samples_mib_s"]["styio"], record["throughput_samples_mib_s"]["cpp"], reciprocal=True)
        else:
            record["throughput"] = {"styio_samples": [], "cpp_samples": [], "median_ratio": 1.0, "geomean_ratio": 1.0, "paired_log_ratios": []}
        record["samples"]["throughput_mib_s"] = record["throughput_samples_mib_s"]
        record["paired_log_ratios"] = {
            dimension: record[dimension]["paired_log_ratios"]
            for dimension in ("time", "throughput", "peak_rss")
        }
        record["median_ratios"] = {
            dimension: record[dimension]["median_ratio"]
            for dimension in ("time", "throughput", "peak_rss")
        }
    return record


def _attempt_noise_reasons(record: Mapping[str, Any], *, max_cv_pct: float | None = STABILITY_CV_LIMIT_PCT) -> list[str]:
    """Return stable noise reasons without discarding any retained sample."""

    reasons: list[str] = []
    summary = record.get("time")
    for implementation in ("styio", "cpp"):
        values = record.get("time_samples_s", {}).get(implementation, []) if isinstance(record.get("time_samples_s"), Mapping) else []
        cv = summary.get(f"{implementation}_cv_pct") if isinstance(summary, Mapping) else None
        if max_cv_pct is None:
            continue
        if not isinstance(values, list) or len(values) < 2:
            reasons.append("noise_cv")
            continue
        if not isinstance(cv, (int, float)) or not math.isfinite(float(cv)) or float(cv) > max_cv_pct:
            reasons.append("noise_cv")
    return sorted(set(reasons))


def _attempt_is_first_valid(record: Mapping[str, Any], *, max_cv_pct: float | None = STABILITY_CV_LIMIT_PCT) -> bool:
    """Whether an entire cell attempt is complete and below the fixed CV gate."""

    return record.get("status") == "pass" and not _attempt_noise_reasons(record, max_cv_pct=max_cv_pct)


def select_first_valid_attempt(
    attempts: Sequence[Mapping[str, Any]],
    *,
    max_cv_pct: float | None = STABILITY_CV_LIMIT_PCT,
) -> int | None:
    """Select the first valid attempt in execution order, never the fastest."""

    for index, attempt in enumerate(attempts):
        if _attempt_is_first_valid(attempt, max_cv_pct=max_cv_pct):
            return index
    return None


def _attempt_audit_record(
    record: Mapping[str, Any],
    *,
    attempt: int,
    max_cv_pct: float | None = STABILITY_CV_LIMIT_PCT,
) -> dict[str, Any]:
    """Keep privacy-safe raw evidence for every rejected whole-cell attempt."""

    samples = record.get("time_samples_s") if isinstance(record.get("time_samples_s"), Mapping) else {}
    rss = record.get("peak_rss_samples_kib") if isinstance(record.get("peak_rss_samples_kib"), Mapping) else {}
    time_values = {
        implementation: list(samples.get(implementation, [])) if isinstance(samples.get(implementation, []), list) else []
        for implementation in ("styio", "cpp")
    }
    rss_values = {
        implementation: list(rss.get(implementation, [])) if isinstance(rss.get(implementation, []), list) else []
        for implementation in ("styio", "cpp")
    }
    cv_values: dict[str, float | None] = {}
    summary = record.get("time") if isinstance(record.get("time"), Mapping) else {}
    for implementation in ("styio", "cpp"):
        raw_cv = summary.get(f"{implementation}_cv_pct")
        cv_values[implementation] = float(raw_cv) if isinstance(raw_cv, (int, float)) and math.isfinite(float(raw_cv)) else None
    reasons = sorted(set(str(value) for value in record.get("reason_codes", []) if isinstance(value, str)))
    reasons.extend(reason for reason in _attempt_noise_reasons(record, max_cv_pct=max_cv_pct) if reason not in reasons)
    return {
        "attempt": int(attempt),
        "status": "pass" if _attempt_is_first_valid(record, max_cv_pct=max_cv_pct) else "rejected",
        "time_samples_s": time_values,
        "peak_rss_samples_kib": rss_values,
        "time_cv_pct": cv_values,
        "reason_codes": sorted(set(reasons)),
    }


def _with_stability_attempts(
    record: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
    *,
    selected_index: int | None,
    attempt_limit: int,
    max_cv_pct: float | None = STABILITY_CV_LIMIT_PCT,
) -> dict[str, Any]:
    """Attach bounded, privacy-safe attempt evidence to the selected cell."""

    selected = dict(record)
    selected["stability"] = {
        "schema": "styio.parity.stability.v1",
        "attempt_limit": int(attempt_limit),
        "attempt_count": len(attempts),
        "selected_attempt": int(selected_index + 1) if selected_index is not None else None,
        # Keep the public fixed oracle value in the schema even when a smoke
        # budget intentionally does not enforce it.
        "cv_limit_pct": float(max_cv_pct if max_cv_pct is not None else STABILITY_CV_LIMIT_PCT),
        "first_valid": selected_index is not None,
    }
    selected["stability_attempts"] = [
        _attempt_audit_record(attempt, attempt=index + 1, max_cv_pct=max_cv_pct)
        for index, attempt in enumerate(attempts)
    ]
    if selected_index is None and max_cv_pct is not None:
        reasons = list(selected.get("reason_codes", []))
        reasons.extend(_attempt_noise_reasons(selected, max_cv_pct=max_cv_pct))
        reasons.append("stability_attempts_exhausted")
        selected["reason_codes"] = sorted(set(str(value) for value in reasons if isinstance(value, str)))
        selected["status"] = "incomplete"
    return selected


def _measure_cell(
    cell: CellSpec,
    *,
    toolchain: Mapping[str, Any],
    cwd: Path,
    work_root: Path,
    warmups: int,
    repetitions: int,
    timeout_s: float,
    phase_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Measure a cell with bounded first-valid stability attempts.

    Each attempt is a complete route measurement.  Attempts are evaluated in
    execution order, and only the first one satisfying correctness and the
    fixed time-CV gate is selected; rejected attempts remain in the report's
    privacy-safe audit list.
    """

    # Phase evidence is shared by every phase cell in a tier and is retried as
    # one unit in ``run_command``.  Retrying an individual phase cell here
    # would duplicate a full probe/trace and break provenance.
    if cell.family == "compiler-phase":
        # Whole-tier phase retries are performed before cell assembly so all
        # five phase IDs consume the same selected probe/trace samples.
        return _measure_cell_once(
            cell,
            toolchain=toolchain,
            cwd=cwd,
            work_root=work_root,
            warmups=warmups,
            repetitions=repetitions,
            timeout_s=timeout_s,
            phase_evidence=phase_evidence,
        )
    if repetitions < 11:
        result = _measure_cell_once(
            cell,
            toolchain=toolchain,
            cwd=cwd,
            work_root=work_root,
            warmups=warmups,
            repetitions=repetitions,
            timeout_s=timeout_s,
            phase_evidence=phase_evidence,
        )
        # Three-sample smoke/diagnostic cells still retain and report their CV,
        # but stability is intentionally not inferred from that small budget.
        # Require only complete correctness/process success and fail closed on
        # any route error.
        return _with_stability_attempts(
            result,
            [result],
            selected_index=0 if _attempt_is_first_valid(result, max_cv_pct=None) else None,
            attempt_limit=1,
            max_cv_pct=None,
        )

    attempts: list[dict[str, Any]] = []
    selected_index: int | None = None
    for attempt_index in range(STABILITY_MAX_ATTEMPTS):
        attempt_root = work_root / f"attempt-{attempt_index}"
        attempt_root.mkdir(parents=True, exist_ok=True)
        result = _measure_cell_once(
            cell,
            toolchain=toolchain,
            cwd=cwd,
            work_root=attempt_root,
            warmups=warmups,
            repetitions=repetitions,
            timeout_s=timeout_s,
            phase_evidence=phase_evidence,
            # A rejected noisy attempt receives a larger equal batch on the
            # next complete attempt; no retained sample is removed or chosen
            # by speed.
            calibration_min_duration_s=min(
                STABILITY_MAX_CALIBRATION_DURATION_S,
                CALIBRATION_MIN_DURATION_S * (8**attempt_index),
            ),
        )
        attempts.append(result)
        if _attempt_is_first_valid(result):
            selected_index = attempt_index
            break
    if not attempts:
        raise GateError("stability_attempts_empty")
    selected = attempts[selected_index] if selected_index is not None else attempts[-1]
    return _with_stability_attempts(
        selected,
        attempts,
        selected_index=selected_index,
        attempt_limit=STABILITY_MAX_ATTEMPTS,
    )


def _source_revision(path: Path) -> str:
    """Return a public source revision without exposing checkout metadata."""

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=str(path),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and HASH.fullmatch(value) else "unknown"


def _selected_cells(catalog: Mapping[str, Any], catalog_path: Path, sizes: Sequence[str], focus: str | None, routes: Sequence[str]) -> list[CellSpec]:
    selected: list[CellSpec] = []
    for cell in _all_cells(catalog, catalog_path):
        if cell.tier not in sizes:
            continue
        if cell.route not in routes:
            continue
        if focus:
            if focus == "compiler-pipeline":
                if cell.family != "compiler-phase":
                    continue
            elif focus == "compiler-phase":
                if cell.family != "compiler-phase":
                    continue
            elif cell.focus_owner != focus:
                continue
        selected.append(cell)
    return selected


def _aggregate_cells(cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    dimensions: dict[str, list[float]] = {"time": [], "throughput": [], "peak_rss": []}
    by_route: dict[str, dict[str, list[float]]] = {}
    by_family: dict[str, dict[str, list[float]]] = {}
    for cell in cells:
        if cell.get("status") != "pass":
            continue
        route = str(cell.get("route", ""))
        family = str(cell.get("family", ""))
        by_route.setdefault(route, {key: [] for key in dimensions})
        by_family.setdefault(family, {key: [] for key in dimensions})
        for dimension in dimensions:
            summary = cell.get(dimension)
            if not isinstance(summary, dict):
                continue
            ratio = summary.get("geomean_ratio")
            if isinstance(ratio, (int, float)) and ratio > 0 and math.isfinite(float(ratio)):
                ratio_float = float(ratio)
                dimensions[dimension].append(ratio_float)
                by_route[route][dimension].append(ratio_float)
                by_family[family][dimension].append(ratio_float)

    def summarize(groups: Mapping[str, Mapping[str, Sequence[float]]]) -> dict[str, Any]:
        return {
            group: {
                dimension: {
                    "geomean_ratio": geometric_mean(values) if values else None,
                    "cell_count": len(values),
                }
                for dimension, values in metrics.items()
            }
            for group, metrics in groups.items()
        }

    return {
        "equal_weight": {
            dimension: {
                "geomean_ratio": geometric_mean(values) if values else None,
                "cell_count": len(values),
            }
            for dimension, values in dimensions.items()
        },
        "by_route": summarize(by_route),
        "by_family": summarize(by_family),
    }


def derive_focus_budgets(
    cells: Sequence[Mapping[str, Any]],
    *,
    target_geomean_ratio: float = 1.05,
) -> dict[str, Any]:
    """Assign deterministic, disjoint savings budgets from observed gaps.

    Budgets are expressed in non-negative log-ratio savings.  A cell belongs
    to exactly one focus owner, so each gap is charged once; route fixed-cost
    shares are retained as attribution metadata and never double-counted in
    the owner allocations.  The sum therefore closes exactly to the required
    gap while a focus already below target receives a zero no-regression
    budget.
    """

    if not math.isfinite(float(target_geomean_ratio)) or target_geomean_ratio <= 0:
        raise ValueError("target_geomean_ratio must be finite and positive")
    allocations = {owner: 0.0 for owner in FOCUS_BUDGET_OWNERS}
    source_ids = {owner: [] for owner in FOCUS_BUDGET_OWNERS}
    route_gaps: dict[str, float] = {}
    phase_gaps: dict[str, float] = {}
    for cell in cells:
        if cell.get("status") != "pass":
            continue
        summary = cell.get("time")
        ratio = summary.get("geomean_ratio") if isinstance(summary, Mapping) else None
        if not isinstance(ratio, (int, float)) or not math.isfinite(float(ratio)) or float(ratio) <= 0:
            continue
        gap = max(0.0, math.log(float(ratio) / target_geomean_ratio))
        route = str(cell.get("route", "unknown"))
        route_gaps[route] = route_gaps.get(route, 0.0) + gap
        phase = cell.get("phase")
        if isinstance(phase, str) and phase:
            phase_gaps[phase] = phase_gaps.get(phase, 0.0) + gap
        owner = str(cell.get("focus_owner", "compiler-pipeline"))
        if owner not in allocations:
            owner = "compiler-pipeline"
        allocations[owner] += gap
        if gap > 0:
            source_ids[owner].append(str(cell.get("id", "unknown")))
    for owner in source_ids:
        source_ids[owner] = sorted(set(source_ids[owner]))
    required = sum(allocations.values())
    return {
        "schema": "styio.parity.focus-budget.v1",
        "target_geomean_ratio": float(target_geomean_ratio),
        "unit": "log_ratio_savings",
        "required_closure": float(required),
        "allocations": {
            owner: {
                "required_savings": float(allocations[owner]),
                "source_cell_ids": source_ids[owner],
                "no_regression_target": allocations[owner] == 0.0,
            }
            for owner in FOCUS_BUDGET_OWNERS
        },
        "route_fixed_cost_shares": {
            route: {
                "gap": float(gap),
                "share": float(gap / required) if required > 0 else 0.0,
            }
            for route, gap in sorted(route_gaps.items())
        },
        "phase_gaps": {phase: float(gap) for phase, gap in sorted(phase_gaps.items())},
        "non_overlapping": True,
        "allocation_sum": float(sum(allocations.values())),
    }


def _diagnostic_evidence(
    catalog: Mapping[str, Any],
    catalog_path: Path,
    toolchain: Mapping[str, Any],
    cwd: Path,
    *,
    timeout_s: float = 300.0,
) -> list[dict[str, Any]]:
    helper = _load_rss_helper()
    results: list[dict[str, Any]] = []
    for case in catalog.get("diagnostics", []):
        source = catalog_path.parent / str(case["source"])
        try:
            code, stdout, stderr = _run_capture(
                [str(toolchain["styio"]), "--error-format", "jsonl", "--file", str(source)],
                cwd=cwd,
                timeout_s=timeout_s,
            )
            diagnostic_match = DIAGNOSTIC_CODE.search((stdout + b"\n" + stderr).decode("utf-8", errors="replace"))
            observed_code = diagnostic_match.group(0) if diagnostic_match else "unknown"
            result = {
                "id": case["id"],
                "status": "pass" if code == case["expected_exit_code"] and observed_code == case["expected_diagnostic_code"] else "fail",
                "expected_exit_code": int(case["expected_exit_code"]),
                "observed_exit_code": int(code),
                "expected_diagnostic_code": case["expected_diagnostic_code"],
                "observed_diagnostic_code": observed_code,
                "reason_codes": [] if code == case["expected_exit_code"] and observed_code == case["expected_diagnostic_code"] else ["diagnostic_mismatch"],
            }
        except TimeoutError:
            result = {
                "id": case["id"],
                "status": "fail",
                "expected_exit_code": int(case["expected_exit_code"]),
                "expected_diagnostic_code": case["expected_diagnostic_code"],
                "reason_codes": ["timed_out"],
            }
        except (OSError, subprocess.SubprocessError):
            result = {
                "id": case["id"],
                "status": "fail",
                "expected_exit_code": int(case["expected_exit_code"]),
                "expected_diagnostic_code": case["expected_diagnostic_code"],
                "reason_codes": ["diagnostic_execution_failed"],
            }
        results.append(result)
    del helper  # Keep import validation explicit; diagnostics are not timed.
    return results


def build_report(
    *,
    catalog: Mapping[str, Any],
    catalog_path: Path,
    toolchain: Mapping[str, Any],
    cells: Sequence[Mapping[str, Any]],
    diagnostics: Sequence[Mapping[str, Any]],
    sizes: Sequence[str],
    routes: Sequence[str],
    focus: str | None,
    warmups: int,
    repetitions: int,
    styio_root: Path,
    benchmark_root: Path,
    timeout_s: float = 300.0,
) -> dict[str, Any]:
    selected_ids = [str(cell["id"]) for cell in cells]
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": REPORT_VERSION,
        "runner_version": RUNNER_VERSION,
        "contract_digest": contract_digest(catalog),
        "catalog_id": catalog["catalog_id"],
        "catalog_version": catalog["catalog_version"],
        "status": "evidence",
        "verification": "not-evaluated",
        "selection": {
            "sizes": list(sizes),
            "routes": list(routes),
            "focus": focus or "all",
            "cell_ids": selected_ids,
        },
        "settings": {
            "warmups": warmups,
            "repetitions": repetitions,
            "timeout_s": timeout_s,
            "pairing": "balanced-ab-ba",
            "optimization": "O3",
            "lto": False,
            "output_validation": "digest-before-timing",
            "rss": "process-tree-peak-kib",
        },
        "toolchain": {
            "compiler_family": toolchain["compiler_family"],
            "compiler_version": toolchain["compiler_version"],
            "styio_version": toolchain["styio_version"],
            "target_class": toolchain["target_class"],
            "build_type": toolchain["build_type"],
            "optimization": toolchain["optimization"],
            "lto": toolchain["lto"],
        },
        "source_revisions": {
            "styio": _source_revision(styio_root),
            "benchmark": _source_revision(benchmark_root),
        },
        "cells": list(cells),
        "diagnostics": list(diagnostics),
        "aggregates": _aggregate_cells(cells),
        "focus_budgets": derive_focus_budgets(cells),
        "reason_codes": sorted({reason for cell in cells for reason in cell.get("reason_codes", [])}),
    }


def _parse_sizes(raw: str) -> list[str]:
    values = [value.strip().lower() for value in raw.split(",") if value.strip()]
    if not values or any(value not in TIERS for value in values):
        raise GateError("invalid_sizes")
    return [tier for tier in TIERS if tier in values]


def _parse_routes(raw: str) -> list[str]:
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if raw == "all":
        return list(ROUTES)
    if not values or any(value not in ROUTES for value in values):
        raise GateError("invalid_routes")
    return [route for route in ROUTES if route in values]


def _verify_cells(
    report: Mapping[str, Any],
    catalog: Mapping[str, Any],
    catalog_path: Path,
    *,
    max_cv_pct: float | None,
    require_all: bool,
    strict: bool,
) -> list[str]:
    reasons: list[str] = []
    cells = report.get("cells")
    if not isinstance(cells, list):
        return ["missing_cells"]
    selection = report.get("selection") if isinstance(report.get("selection"), dict) else {}
    selected_ids = selection.get("cell_ids") if isinstance(selection.get("cell_ids"), list) else []
    expected_ids = set(selected_ids)
    if require_all:
        expected_ids = {cell.cell_id for cell in _all_cells(catalog, catalog_path)}
    actual_ids = {cell.get("id") for cell in cells if isinstance(cell, dict)}
    if expected_ids - actual_ids:
        reasons.append("missing_cells")
    if len(actual_ids) != len(cells):
        reasons.append("duplicate_cells")
    for cell in cells:
        if not isinstance(cell, dict):
            reasons.append("malformed_cell")
            continue
        if cell.get("status") != "pass":
            reasons.extend(str(value) for value in cell.get("reason_codes", []) if isinstance(value, str))
            reasons.append("incomplete_cell")
            continue
        if cell.get("correctness") != {"styio": True, "cpp": True}:
            reasons.append("correctness_failed")
        repetitions = cell.get("repetitions")
        order = cell.get("balanced_order")
        if not isinstance(repetitions, int) or repetitions <= 0:
            reasons.append("invalid_repetitions")
            continue
        if not isinstance(order, list) or len(order) != repetitions or any(value not in {"AB", "BA"} for value in order):
            reasons.append("unbalanced_pairs")
        elif any(value != ("AB" if index % 2 == 0 else "BA") for index, value in enumerate(order)):
            reasons.append("unbalanced_pairs")
        batch_count = cell.get("batch_count", 1)
        if not isinstance(batch_count, int) or batch_count <= 0:
            reasons.append("invalid_batch_count")
        if str(cell.get("family")) == "compiler-phase":
            provenance = cell.get("phase_provenance")
            if not isinstance(provenance, dict) or provenance.get("shared_tier_samples") is not True:
                reasons.append("missing_phase_provenance")
        for implementation in ("styio", "cpp"):
            samples = cell.get("time_samples_s", {}).get(implementation, [])
            rss = cell.get("peak_rss_samples_kib", {}).get(implementation, [])
            if not isinstance(samples, list) or len(samples) != repetitions or any(not isinstance(value, (int, float)) or value <= 0 for value in samples):
                reasons.append("missing_samples")
            if not isinstance(rss, list) or len(rss) != repetitions or any(not isinstance(value, (int, float)) or value < 0 for value in rss):
                reasons.append("missing_rss_samples")
            # Smoke/diagnostic budgets (<11 retained samples) expose CV for
            # observability but never turn it into a stability gate.  Baseline
            # and final budgets retain the fixed 5% fail-closed check.
            if max_cv_pct is not None and repetitions >= 11:
                cv = cell.get("time", {}).get(f"{implementation}_cv_pct")
                if not isinstance(cv, (int, float)) or not math.isfinite(float(cv)) or float(cv) > max_cv_pct:
                    reasons.append("noise_cv")
        if strict and cell.get("reason_codes"):
            reasons.extend(str(value) for value in cell["reason_codes"] if isinstance(value, str))
        for dimension in ("time", "throughput", "peak_rss"):
            summary = cell.get(dimension)
            if summary is None and dimension == "throughput" and cell.get("size", 0) == 0:
                continue
            if not isinstance(summary, dict):
                reasons.append("missing_statistics")
                continue
            ratio = summary.get("geomean_ratio")
            if not isinstance(ratio, (int, float)) or not math.isfinite(float(ratio)) or float(ratio) <= 0:
                reasons.append("invalid_statistics")
    return sorted(set(reasons))


def _verify_focus_budgets(report: Mapping[str, Any], *, focus: str | None = None) -> list[str]:
    budgets = report.get("focus_budgets")
    if not isinstance(budgets, Mapping):
        return ["missing_focus_budget"]
    if budgets.get("schema") != "styio.parity.focus-budget.v1" or budgets.get("non_overlapping") is not True:
        return ["malformed_focus_budget"]
    allocations = budgets.get("allocations")
    if not isinstance(allocations, Mapping):
        return ["malformed_focus_budget"]
    required = budgets.get("required_closure")
    allocation_sum = budgets.get("allocation_sum")
    if not isinstance(required, (int, float)) or not isinstance(allocation_sum, (int, float)):
        return ["malformed_focus_budget"]
    values: list[float] = []
    for owner in FOCUS_BUDGET_OWNERS:
        item = allocations.get(owner)
        if not isinstance(item, Mapping):
            return ["missing_focus_budget"]
        value = item.get("required_savings")
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
            return ["malformed_focus_budget"]
        values.append(float(value))
    if abs(sum(values) - float(required)) > 1e-12 or abs(sum(values) - float(allocation_sum)) > 1e-12:
        return ["focus_budget_sum"]
    if focus:
        owner = "compiler-pipeline" if focus == "compiler-pipeline" else focus
        if owner not in allocations:
            return ["missing_focus_budget"]
    return []


def verify_report(
    report: Mapping[str, Any],
    catalog: Mapping[str, Any],
    catalog_path: Path,
    *,
    mode: str = "smoke",
    privacy: str = "strict",
    require_all: bool = False,
    max_cv_pct: float | None = None,
    max_geomean_ratio: float | None = None,
    max_case_ratio: float | None = None,
    baseline: Mapping[str, Any] | None = None,
    focus: str | None = None,
    max_phase_regression_pct: float | None = None,
    max_normalized_growth: float | None = None,
) -> dict[str, Any]:
    """Verify one report and return a public decision object."""

    strict = privacy == "strict"
    validate_public_report(report, strict=strict)
    reasons: list[str] = []
    if report.get("schema") != REPORT_SCHEMA or report.get("schema_version") != REPORT_VERSION:
        reasons.append("report_schema")
    if report.get("contract_digest") != contract_digest(catalog):
        reasons.append("contract_digest_mismatch")
    if mode == "smoke":
        # Smoke is the bounded diagnostic budget (typically 1/3 samples):
        # retain CV in evidence but do not apply the 5% stability oracle.
        max_cv_pct = None
    elif max_cv_pct is None and mode in {"baseline", "focused-budget", "final"}:
        max_cv_pct = 5.0
    reasons.extend(_verify_cells(report, catalog, catalog_path, max_cv_pct=max_cv_pct, require_all=require_all, strict=strict))
    if mode in {"focused-budget", "baseline", "final"}:
        reasons.extend(_verify_focus_budgets(report, focus=focus))
    diagnostics = report.get("diagnostics")
    if require_all and (not isinstance(diagnostics, list) or any(item.get("status") != "pass" for item in diagnostics if isinstance(item, dict))):
        reasons.append("diagnostic_failure")

    if mode == "final":
        max_geomean_ratio = 1.05 if max_geomean_ratio is None else max_geomean_ratio
        max_case_ratio = 1.10 if max_case_ratio is None else max_case_ratio
    if max_geomean_ratio is not None:
        global_stats = report.get("aggregates", {}).get("equal_weight", {})
        for dimension in ("time", "throughput", "peak_rss"):
            ratio = global_stats.get(dimension, {}).get("geomean_ratio") if isinstance(global_stats.get(dimension), dict) else None
            if ratio is None or float(ratio) > max_geomean_ratio:
                reasons.append("geomean_threshold")
    if max_case_ratio is not None:
        for cell in report.get("cells", []):
            if not isinstance(cell, dict) or cell.get("status") != "pass":
                continue
            for dimension in ("time", "throughput", "peak_rss"):
                summary = cell.get(dimension)
                ratio = summary.get("median_ratio") if isinstance(summary, dict) else None
                if ratio is None or float(ratio) > max_case_ratio:
                    reasons.append("case_threshold")
    if mode == "focused-budget" and baseline is not None:
        baseline_cells = {item.get("id"): item for item in baseline.get("cells", []) if isinstance(item, dict)}
        budget_owner = "compiler-pipeline" if focus == "compiler-pipeline" else focus
        required_savings = 0.0
        if budget_owner:
            budget_item = report.get("focus_budgets", {}).get("allocations", {}).get(budget_owner, {})
            value = budget_item.get("required_savings") if isinstance(budget_item, Mapping) else None
            if isinstance(value, (int, float)) and math.isfinite(float(value)) and value >= 0:
                required_savings = float(value)
        achieved_savings = 0.0
        for cell in report.get("cells", []):
            previous = baseline_cells.get(cell.get("id"))
            if not previous:
                continue
            current_ratio = cell.get("time", {}).get("geomean_ratio")
            previous_ratio = previous.get("time", {}).get("geomean_ratio")
            if max_phase_regression_pct is not None and isinstance(current_ratio, (int, float)) and isinstance(previous_ratio, (int, float)) and previous_ratio > 0:
                if float(current_ratio) / float(previous_ratio) > 1 + max_phase_regression_pct / 100.0:
                    reasons.append("phase_regression_threshold")
            if max_normalized_growth is not None and isinstance(current_ratio, (int, float)) and isinstance(previous_ratio, (int, float)) and previous_ratio > 0:
                if float(current_ratio) / float(previous_ratio) > max_normalized_growth:
                    reasons.append("normalized_growth_threshold")
            if budget_owner and cell.get("focus_owner") == budget_owner and isinstance(current_ratio, (int, float)) and isinstance(previous_ratio, (int, float)) and current_ratio > 0 and previous_ratio > 0:
                achieved_savings += max(0.0, math.log(float(previous_ratio) / float(current_ratio)))
        if budget_owner and achieved_savings + 1e-12 < required_savings:
            reasons.append("focus_budget_unmet")

    decision = "pass" if not reasons else "fail"
    return {
        "schema": "styio.parity.verification.v1",
        "schema_version": 1,
        "decision": decision,
        "mode": mode,
        "reason_codes": sorted(set(reasons)),
        "checked_cells": len(report.get("cells", [])) if isinstance(report.get("cells"), list) else 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run and verify Styio/C++ parity-v1 evidence.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    catalog = subparsers.add_parser("catalog-check", help="Validate the frozen parity catalog.")
    catalog.add_argument("--contract", default=str(CATALOG_DEFAULT))

    run = subparsers.add_parser("run", help="Record paired evidence without deciding parity.")
    run.add_argument("--contract", default=str(CATALOG_DEFAULT))
    run.add_argument("--styio-root", default="../styio")
    run.add_argument("--build-dir", default="build/default")
    run.add_argument("--styio-exe", default="")
    run.add_argument("--out-dir", required=True)
    run.add_argument("--sizes", default="small,medium,large")
    run.add_argument("--routes", default="all")
    run.add_argument("--focus", default="")
    run.add_argument("--warmups", type=int, default=3)
    run.add_argument("--repetitions", type=int, default=11)
    run.add_argument("--timeout-s", type=float, default=300.0)

    verify = subparsers.add_parser("verify", help="Apply strict correctness/noise/privacy/threshold gates.")
    verify.add_argument("--contract", default=str(CATALOG_DEFAULT))
    verify.add_argument("--report", required=True)
    verify.add_argument("--baseline", default="")
    verify.add_argument("--mode", choices=("smoke", "baseline", "focused-budget", "final"), default="final")
    verify.add_argument("--focus", default="")
    verify.add_argument("--privacy", choices=("strict", "off"), default="strict")
    verify.add_argument("--require-all", action="store_true")
    verify.add_argument("--max-cv-pct", type=float, default=None)
    verify.add_argument("--max-geomean-ratio", type=float, default=None)
    verify.add_argument("--max-case-ratio", type=float, default=None)
    verify.add_argument("--max-phase-regression-pct", type=float, default=None)
    verify.add_argument("--max-normalized-growth", type=float, default=None)
    return parser


def _resolve_cli_path(raw: str, *, base: Path | None = None) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return (base or Path.cwd()) / path


def run_command(args: argparse.Namespace) -> int:
    catalog, catalog_path = load_catalog(args.contract)
    if args.warmups < 0 or args.repetitions <= 0:
        raise GateError("invalid_sampling_budget")
    if args.timeout_s <= 0 or not math.isfinite(float(args.timeout_s)):
        raise GateError("invalid_timeout")
    sizes = _parse_sizes(args.sizes)
    routes = _parse_routes(args.routes)
    styio_root = _resolve_cli_path(args.styio_root).resolve()
    build_dir = _resolve_cli_path(args.build_dir).resolve()
    styio_exe = _resolve_cli_path(args.styio_exe).resolve() if args.styio_exe else None
    toolchain = discover_toolchain(styio_root, build_dir, styio_exe)
    focus = args.focus.strip() or None
    allowed_focuses = FOCUS_OWNERS | {"compiler-pipeline", "compiler-phase"}
    if focus and focus not in allowed_focuses:
        raise GateError("invalid_focus")
    selected = _selected_cells(catalog, catalog_path, sizes, focus, routes)
    if not selected:
        raise GateError("no_selected_cells")
    out_dir = _resolve_cli_path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    measured: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="parity-gate-") as temp_raw:
        temp_root = Path(temp_raw)
        phase_evidence_by_tier: dict[str, dict[str, Any]] = {}
        phase_cells_by_tier: dict[str, CellSpec] = {}
        for phase_cell in selected:
            if phase_cell.family == "compiler-phase":
                phase_cells_by_tier.setdefault(phase_cell.tier, phase_cell)
        for tier, phase_cell in phase_cells_by_tier.items():
            phase_attempt_limit = STABILITY_MAX_ATTEMPTS if args.repetitions >= 11 else 1
            phase_cv_limit = STABILITY_CV_LIMIT_PCT if args.repetitions >= 11 else None
            phase_batch_count = PHASE_SAMPLE_BATCH_COUNTS.get(tier, PHASE_SAMPLE_BATCH_COUNT)
            phase_attempts: list[dict[str, Any]] = []
            selected_phase: dict[str, Any] | None = None
            selected_attempt: int | None = None
            for attempt_index in range(phase_attempt_limit):
                try:
                    tier_root = temp_root / f"phase-{tier}-attempt-{attempt_index}"
                    tier_root.mkdir(parents=True, exist_ok=True)
                    _, cpp_source = _prepare_sources(phase_cell, tier_root)
                    phase_candidate = _run_phase_samples(
                        toolchain=toolchain,
                        styio_root=styio_root,
                        cwd=styio_root,
                        source=cpp_source,
                        tier=tier,
                        warmups=args.warmups,
                        repetitions=args.repetitions,
                        timeout_s=args.timeout_s,
                        work_root=tier_root,
                        batch_count=phase_batch_count,
                    )
                    phase_candidate["styio_provenance"] = phase_candidate.pop("provenance")
                except (GateError, OSError, subprocess.SubprocessError, TimeoutError, ValueError) as exc:
                    # Do not expose exception text; every phase cell remains
                    # in the report with a stable failure reason.
                    phase_candidate = {"error": getattr(exc, "reason_code", "phase_evidence_failed")}
                phase_attempts.append(
                    _phase_attempt_audit_record(
                        phase_candidate,
                        attempt=attempt_index + 1,
                        repetitions=args.repetitions,
                        max_cv_pct=phase_cv_limit,
                    )
                )
                if _phase_evidence_is_stable(
                    phase_candidate,
                    args.repetitions,
                    max_cv_pct=phase_cv_limit,
                ):
                    selected_phase = phase_candidate
                    selected_attempt = attempt_index + 1
                    break
                selected_phase = phase_candidate
            if selected_phase is None:
                selected_phase = {"error": "phase_evidence_failed"}
            selected_phase["stability_attempts"] = phase_attempts
            selected_phase["stability_attempt_limit"] = phase_attempt_limit
            selected_phase["stability_selected_attempt"] = selected_attempt
            phase_evidence_by_tier[tier] = selected_phase
        for index, cell in enumerate(selected):
            cell_root = temp_root / f"cell-{index}"
            cell_root.mkdir(parents=True, exist_ok=True)
            try:
                measured.append(
                    _measure_cell(
                        cell,
                        toolchain=toolchain,
                        cwd=styio_root,
                        work_root=cell_root,
                        warmups=args.warmups,
                        repetitions=args.repetitions,
                        timeout_s=args.timeout_s,
                        phase_evidence=phase_evidence_by_tier.get(cell.tier) if cell.family == "compiler-phase" else None,
                    )
                )
            except (GateError, OSError, subprocess.SubprocessError, TimeoutError, ValueError) as exc:
                # Preserve a stable incomplete cell rather than silently
                # dropping it.  No exception text is copied to evidence.
                measured.append(
                    {
                        "id": cell.cell_id,
                        "family": cell.family,
                        "tier": cell.tier,
                        "size": cell.size,
                        "route": cell.route,
                        "focus_owner": cell.focus_owner,
                        "phase": cell.phase,
                        "status": "incomplete",
                        "correctness": {"styio": False, "cpp": False},
                        "expected_output_digest": cell.expected_digest,
                        "source_digests": cell.source_digest,
                        "input_digest": _sha256(cell.input_bytes),
                        "balanced_order": [],
                        "repetitions": args.repetitions,
                        "warmups": args.warmups,
                        "time_samples_s": {"styio": [], "cpp": []},
                        "peak_rss_samples_kib": {"styio": [], "cpp": []},
                        "reason_codes": ["cell_execution_failed"],
                    }
                )
    diagnostics = _diagnostic_evidence(
        catalog,
        catalog_path,
        toolchain,
        styio_root,
        timeout_s=args.timeout_s,
    )
    report = build_report(
        catalog=catalog,
        catalog_path=catalog_path,
        toolchain=toolchain,
        cells=measured,
        diagnostics=diagnostics,
        sizes=sizes,
        routes=routes,
        focus=focus,
        warmups=args.warmups,
        repetitions=args.repetitions,
        timeout_s=args.timeout_s,
        styio_root=styio_root,
        benchmark_root=ROOT,
    )
    write_public_json(out_dir / "results.json", report)
    # A compact human-readable summary is generated from public fields only.
    summary_lines = [
        "# Styio parity evidence",
        "",
        f"- Status: `{report['status']}`",
        f"- Catalog: `{report['catalog_id']}` v{report['catalog_version']}",
        f"- Cells: `{len(measured)}`",
        f"- Warmups / repetitions: `{args.warmups}` / `{args.repetitions}`",
        "",
        "| Cell | Route | Status | Time geomean |",
        "| --- | --- | --- | ---: |",
    ]
    for cell in measured:
        summary = cell.get("time", {})
        ratio = summary.get("geomean_ratio", "n/a") if isinstance(summary, dict) else "n/a"
        summary_lines.append(f"| {cell.get('id', 'unknown')} | {cell.get('route', 'unknown')} | {cell.get('status', 'unknown')} | {ratio} |")
    (out_dir / "summary.md").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": "evidence", "cells": len(measured), "outcome": "recorded"}, sort_keys=True))
    return 0


def verify_command(args: argparse.Namespace) -> int:
    catalog, catalog_path = load_catalog(args.contract)
    report_path = _resolve_cli_path(args.report).resolve()
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError("report_unreadable", str(exc)) from exc
    if not isinstance(report, dict):
        raise GateError("report_shape")
    baseline: Mapping[str, Any] | None = None
    if args.baseline:
        baseline_path = _resolve_cli_path(args.baseline).resolve()
        try:
            baseline_value = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GateError("baseline_unreadable", str(exc)) from exc
        if not isinstance(baseline_value, dict):
            raise GateError("baseline_shape")
        baseline = baseline_value
    decision = verify_report(
        report,
        catalog,
        catalog_path,
        mode=args.mode,
        privacy=args.privacy,
        require_all=args.require_all,
        max_cv_pct=args.max_cv_pct,
        max_geomean_ratio=args.max_geomean_ratio,
        max_case_ratio=args.max_case_ratio,
        baseline=baseline,
        focus=args.focus.strip() or None,
        max_phase_regression_pct=args.max_phase_regression_pct,
        max_normalized_growth=args.max_normalized_growth,
    )
    # Verification output itself is also passed through the public serializer.
    assert_public_report(decision, strict=True)
    print(json.dumps(decision, sort_keys=True))
    return 0 if decision["decision"] == "pass" else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.subcommand == "catalog-check":
            catalog, _ = load_catalog(args.contract)
            print(json.dumps({"status": "pass", "catalog_id": catalog["catalog_id"], "catalog_version": catalog["catalog_version"], "contract_digest": contract_digest(catalog)}, sort_keys=True))
            return 0
        if args.subcommand == "run":
            return run_command(args)
        if args.subcommand == "verify":
            return verify_command(args)
        raise GateError("unknown_subcommand")
    except PrivacyError as exc:
        print(json.dumps({"status": "fail", "reason_codes": [exc.reason_code]}, sort_keys=True), file=sys.stderr)
        return 2
    except GateError as exc:
        print(json.dumps({"status": "fail", "reason_codes": [exc.reason_code]}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
