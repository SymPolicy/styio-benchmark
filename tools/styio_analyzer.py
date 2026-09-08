#!/usr/bin/env python3
"""Styio Analyzer v1: compare two Styio toolchains on the parity-v2 corpus.

compare executes selected families on the three catalog routes and writes
privacy-safe JSON/Markdown.  A performance regression does not change the
compare exit code.  verify recomputes statistics offline; success means the
report is consistent, not that the candidate passed a performance gate.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CATALOG_DEFAULT = ROOT / "workloads" / "parity-v2" / "contract.json"
REPORT_SCHEMA = "styio.analyzer.comparison.v1"
VERDICT_SCHEMA = "styio.analyzer.verdict.v1"
REPORT_VERSION = 1
RUNNER_VERSION = "styio-analyzer-1"
COMPARISON_KIND = "styio-revision"
ROUTES = ("compile-and-run", "native-build", "native-run")
SCALES = ("smoke", "development", "reference")
NATIVE_CACHE_CHILD = {"STYIO_NATIVE_CACHE": "0"}
RUNTIME_MARKER = Path("src") / "StyioExtern" / "ExternLib.cpp"
CXX_SELECTION = frozenset({"native_cxx_overlay", "embedded_cmake", "toolchain_tree", "path_lookup"})
HELP_EPILOG = """\
compare exits 0 when every selected cell was executed and required evidence is
valid.  Observed improved, regressed, no_detected_change, or inconclusive
performance does not change that exit code.

verify exits 0 when the report matches this schema, discloses coverage, and
recomputes from raw samples.  verify success is not performance acceptance of
the candidate.
"""


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"missing_{name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_core = _load_module("styio_measurement_core", Path(__file__).resolve().parent / "measurement_core.py")
_gate = _load_module("standard_parity_gate", Path(__file__).resolve().parent / "standard_parity_gate.py")

ReasonError = _core.ReasonError
PrivacyError = _core.PrivacyError


def catalog_family_ids(catalog: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(item["id"]) for item in catalog.get("workloads", []))


def select_families(catalog: Mapping[str, Any], requested: Sequence[str] | None) -> list[str]:
    available = catalog_family_ids(catalog)
    if not requested:
        return list(available)
    selected: list[str] = []
    seen: set[str] = set()
    for family in requested:
        if not isinstance(family, str) or not family.strip():
            raise ReasonError("empty_family_selection")
        name = family.strip()
        if name not in available:
            raise ReasonError("unknown_family")
        if name not in seen:
            selected.append(name)
            seen.add(name)
    if not selected:
        raise ReasonError("empty_family_selection")
    return selected


def select_cells(catalog: Mapping[str, Any], families: Sequence[str], scale: str) -> list[dict[str, Any]]:
    if scale not in SCALES:
        raise ReasonError("unknown_scale")
    cells: list[dict[str, Any]] = []
    for family in families:
        shard = _gate.deterministic_shard(catalog, family, scale)
        for cell in shard:
            if cell.get("route") in ROUTES:
                cells.append(dict(cell))
    if not cells:
        raise ReasonError("empty_selection")
    return cells


def resolve_styio_compiler(build_dir: Path, *, role: str) -> Path:
    """Resolve one Styio compiler from its own build directory only."""

    del role
    if not build_dir.is_dir():
        raise ReasonError("styio_compiler_missing")
    for candidate in (build_dir / "bin" / "styio", build_dir / "styio"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise ReasonError("styio_compiler_missing")


def _git_identity(root: Path) -> dict[str, Any]:
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"revision": "untraceable", "dirty": None, "traceable": False}
    hex_rev = revision.stdout.decode("ascii", errors="replace").strip()
    if revision.returncode != 0 or not hex_rev or any(ch not in "0123456789abcdef" for ch in hex_rev.lower()):
        return {"revision": "untraceable", "dirty": None, "traceable": False}
    return {
        "revision": hex_rev.lower()[:40],
        "dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
        "traceable": True,
    }


def toolchain_identity(compiler: Path, root: Path, build_dir: Path, *, role: str) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [str(compiler), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        version_text = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        version_text = ""
    try:
        artifact_digest = _core.sha256(compiler.read_bytes())
    except OSError:
        artifact_digest = "untraceable"
    identity = _git_identity(root)
    return {
        "role": role,
        "styio_version": _core.public_version(version_text),
        "revision": identity["revision"],
        "dirty": identity["dirty"],
        "traceable": identity["traceable"],
        "artifact_digest": artifact_digest,
        "backend": "native-aot",
        "build_configuration": public_build_configuration(build_dir),
    }


def _looks_like_clang_cxx(command: str) -> bool:
    base = Path(command).name.lower()
    return "clang++" in base or "clang-cl" in base


def read_cmake_cache(build_dir: Path) -> dict[str, str]:
    """Read public CMake keys from an existing build credential file."""

    values: dict[str, str] = {}
    for candidate in (build_dir / "CMakeCache.txt", build_dir.parent / "CMakeCache.txt"):
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//") or ":" not in stripped:
                continue
            key, rest = stripped.split(":", 1)
            if "=" not in rest:
                continue
            values[key] = rest.split("=", 1)[1]
        if values:
            return values
    return values


def public_build_configuration(build_dir: Path) -> dict[str, Any] | None:
    cache = read_cmake_cache(build_dir)
    build_type = cache.get("CMAKE_BUILD_TYPE", "")
    flags_key = "CMAKE_CXX_FLAGS_" + build_type.upper()
    if build_type not in {"Debug", "Release", "RelWithDebInfo", "MinSizeRel"} or flags_key not in cache:
        return None
    try:
        result = {
            "build_type": build_type,
            "cxx_flags": shlex.split(cache.get("CMAKE_CXX_FLAGS", "") + " " + cache[flags_key]),
            "linker_flags": shlex.split(cache.get("CMAKE_EXE_LINKER_FLAGS", "") + " " + cache.get("CMAKE_EXE_LINKER_FLAGS_" + build_type.upper(), "")),
            "target_architectures": cache.get("CMAKE_OSX_ARCHITECTURES", "").split(";") if cache.get("CMAKE_OSX_ARCHITECTURES") else ["compiler-default"],
            "coverage": cache.get("STYIO_ENABLE_COVERAGE", "OFF"),
        }
        # Custom definitions and linker arguments may contain private values.
        # Publish only the standard configuration we can describe safely.
        public_flags = {"-O0", "-O1", "-O2", "-O3", "-Os", "-Oz", "-g", "-DNDEBUG", "-fno-lto", "-flto", "-flto=thin"}
        if any(flag not in public_flags for flag in result["cxx_flags"] + result["linker_flags"]):
            return None
        _core.assert_public_report(result)
        return result
    except (ValueError, PrivacyError):
        return None


def identify_cxx_binary(compiler: Path) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            [str(compiler), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        text = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        text = ""
    try:
        digest = _core.sha256(compiler.read_bytes())
    except OSError:
        digest = "untraceable"
    return {
        "compiler_family": "clang",
        "compiler_version": _core.public_version(text),
        "present": "clang" in text.lower(),
        "optimization": "O3",
        "lto": False,
        "threads": 1,
        "artifact_digest": digest,
        "driver_mode": (
            "clang-cl" if "clang-cl" in compiler.name.lower()
            else "clang++" if "clang++" in compiler.name.lower()
            else "clang" if compiler.name.lower().startswith("clang")
            else "unknown"
        ),
        "target": next(
            (line.partition(":")[2].strip() for line in text.splitlines() if line.startswith("Target:")),
            "unknown",
        ),
    }


def _first_existing_cxx(candidates: Sequence[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.absolute()
    return None


def _find_clang_in_root(root: Path) -> Path | None:
    if not root:
        return None
    names = ("clang++", "clang++-18", "clang-cl")
    return _first_existing_cxx([directory / name for directory in (root / "bin", root) for name in names])


def resolve_native_cxx(
    *,
    styio_compiler: Path,
    build_dir: Path,
    overlay: Mapping[str, str] | None = None,
) -> tuple[Path | None, str]:
    """Resolve the Clang Styio would invoke, matching src/main.cpp order."""

    merged = {}
    if overlay:
        merged.update({str(key): str(value) for key, value in overlay.items()})
    env_compiler = merged.get("STYIO_NATIVE_CXX") or os.environ.get("STYIO_NATIVE_CXX")
    if env_compiler:
        path = Path(env_compiler)
        if path.is_file() and os.access(path, os.X_OK):
            return path.absolute(), "native_cxx_overlay"
        return None, "unresolved"
    cache = read_cmake_cache(build_dir)
    cmake_cxx = cache.get("CMAKE_CXX_COMPILER") or cache.get("STYIO_CMAKE_CXX_COMPILER") or ""
    if cmake_cxx and _looks_like_clang_cxx(cmake_cxx):
        path = Path(cmake_cxx)
        if path.is_file() and os.access(path, os.X_OK):
            return path.absolute(), "embedded_cmake"
    toolchain_roots = []
    env_root = merged.get("STYIO_NATIVE_TOOLCHAIN_ROOT") or os.environ.get("STYIO_NATIVE_TOOLCHAIN_ROOT")
    if env_root:
        toolchain_roots.append(Path(env_root))
    cache_root = cache.get("STYIO_NATIVE_TOOLCHAIN_ROOT")
    if cache_root:
        toolchain_roots.append(Path(cache_root))
    exe = styio_compiler.resolve()
    toolchain_roots.extend(
        (
            exe.parent / "native-toolchain",
            exe.parent.parent / "native-toolchain",
            exe.parent.parent / "lib" / "styio" / "native-toolchain",
        )
    )
    for root in toolchain_roots:
        found = _find_clang_in_root(root)
        if found is not None:
            return found, "toolchain_tree"
    for name in ("clang++", "clang++-18", "clang-cl"):
        located = shutil.which(name)
        if located:
            path = Path(located)
            if path.is_file() and os.access(path, os.X_OK):
                return path.absolute(), "path_lookup"
    return None, "unresolved"


def runtime_binding_status(
    root: Path, compiler: Path, source: Path, *, timeout_s: float, driver_mode: str = "clang++"
) -> dict[str, Any]:
    """Observe native build inputs once, outside measurement, without linking.

    Missing build metadata cannot rule out a still-valid embedded source root.
    The temporary CXX records only public facts, never the command or paths.
    """
    marker = "present" if (root / RUNTIME_MARKER).is_file() else "missing"
    unknown = {"runtime_binding": "unconfirmed", "runtime_marker": marker, "binding_basis": "none"}
    with tempfile.TemporaryDirectory(prefix="analyzer-binding-") as directory:
        area = Path(directory)
        evidence = area / "binding.json"
        observer = area / (driver_mode if driver_mode in {"clang", "clang++", "clang-cl"} else "clang++")
        observer.write_text(
            f"#!{sys.executable}\n" + textwrap.dedent(f"""\
                import json, sys
                from pathlib import Path
                args = sys.argv[1:]
                if args == ["--version"]:
                    print("clang version 0.0.0")
                    raise SystemExit(0)
                runtime = [Path(arg) for arg in args if Path(arg).name == "ExternLib.cpp"]
                if runtime:
                    target = "compiler-default"
                    for i, arg in enumerate(args):
                        if arg in ("-target", "--target") and i + 1 < len(args):
                            target = args[i + 1]
                        elif arg.startswith("--target="):
                            target = arg.split("=", 1)[1]
                    lto = "disabled"
                    for arg in args:
                        if arg == "-fno-lto":
                            lto = "disabled"
                        elif arg == "-flto" or arg.startswith("-flto="):
                            lto = arg.partition("=")[2] or "full"
                    Path({str(evidence)!r}).write_text(json.dumps({{
                        "matches": all(p.resolve() == Path({str((root / RUNTIME_MARKER).resolve())!r}) for p in runtime),
                        "native_configuration": {{
                            "optimization": next((arg[1:] for arg in reversed(args) if arg in ("-O0", "-O1", "-O2", "-O3", "-Os", "-Oz", "-Ofast")), "unknown"),
                            "lto": lto,
                            "target": target,
                        }},
                    }}))
                raise SystemExit(1)
                """),
            encoding="utf-8",
        )
        observer.chmod(0o700)
        try:
            subprocess.run(
                _core.compile_command(compiler, source, area / "probe.bin", styio=True),
                cwd=root,
                env=_core.merge_child_env({**NATIVE_CACHE_CHILD, "STYIO_NATIVE_CXX": str(observer)}),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=timeout_s,
            )
            observed = json.loads(evidence.read_text(encoding="utf-8"))
            _core.assert_public_report(observed)
        except (OSError, ValueError, subprocess.TimeoutExpired, PrivacyError):
            return unknown
    return {
        "runtime_binding": "confirmed" if observed["matches"] and marker == "present" else "mismatch",
        "runtime_marker": marker,
        "binding_basis": "native_build_probe",
        "native_configuration": observed["native_configuration"],
    }


def external_from_resolved(compiler: Path | None, selection: str) -> dict[str, Any]:
    if compiler is None:
        return {
            "compiler_family": "clang",
            "compiler_version": "unknown",
            "present": False,
            "optimization": "O3",
            "lto": False,
            "threads": 1,
            "selection": selection,
            "artifact_digest": "untraceable",
            "pinned": False,
        }
    identity = identify_cxx_binary(compiler)
    identity["selection"] = selection
    identity["pinned"] = selection != "unresolved"
    return identity


def external_clang_identity() -> dict[str, Any]:
    """PATH-only identity kept for missing-toolchain disclosure before resolution."""

    cxx = shutil.which("clang++") or shutil.which("clang")
    if not cxx:
        return external_from_resolved(None, "unresolved")
    return external_from_resolved(Path(cxx), "path_lookup")


def assess_comparability(
    *,
    baseline_cxx: Path | None,
    candidate_cxx: Path | None,
    baseline_binding: Mapping[str, Any],
    candidate_binding: Mapping[str, Any],
    baseline_identity: Mapping[str, Any],
    candidate_identity: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if baseline_cxx is None or candidate_cxx is None:
        reasons.append("external_compiler_unresolved")
    elif baseline_identity.get("artifact_digest") != candidate_identity.get("artifact_digest"):
        reasons.append("external_compiler_mismatch")
    elif baseline_identity.get("present") is not True or candidate_identity.get("present") is not True:
        reasons.append("clang_toolchain_missing")
    if not all(_digest_hex(identity.get("artifact_digest")) for identity in (baseline_identity, candidate_identity)):
        reasons.append("external_identity_unconfirmed")
    builds = [binding.get("build_configuration") for binding in (baseline_binding, candidate_binding)]
    if any(not isinstance(config, Mapping) for config in builds):
        reasons.append("build_configuration_unconfirmed")
    elif builds[0] != builds[1]:
        reasons.append("build_configuration_mismatch")
    for key in ("driver_mode", "target"):
        if baseline_identity.get(key) in {None, "unknown"} or candidate_identity.get(key) in {None, "unknown"}:
            reasons.append("external_configuration_unconfirmed")
        elif baseline_identity[key] != candidate_identity[key]:
            reasons.append("external_configuration_mismatch")
    configurations = [binding.get("native_configuration") for binding in (baseline_binding, candidate_binding)]
    if any(
        not isinstance(config, Mapping)
        or set(config) != {"optimization", "lto", "target"}
        or config.get("optimization") not in {"O0", "O1", "O2", "O3", "Os", "Oz", "Ofast"}
        or config.get("lto") not in {"disabled", "full", "thin"}
        or not config.get("target")
        for config in configurations
    ):
        reasons.append("native_configuration_unconfirmed")
    elif configurations[0] != configurations[1]:
        reasons.append("native_configuration_mismatch")
    for binding in (baseline_binding, candidate_binding):
        status = binding.get("runtime_binding")
        if status == "mismatch":
            reasons.append("runtime_binding_mismatch")
        elif status != "confirmed":
            reasons.append("runtime_binding_unconfirmed")
    unique = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return (not unique, unique)


def child_env_for_side(cxx: Path | None) -> dict[str, str]:
    env = dict(NATIVE_CACHE_CHILD)
    if cxx is not None:
        env["STYIO_NATIVE_CXX"] = str(cxx)
    return env


def _numbers_close(reported: Any, expected: Any) -> bool:
    if reported is None and expected is None:
        return True
    if isinstance(reported, bool) or isinstance(expected, bool):
        return False
    if not isinstance(reported, (int, float)) or not isinstance(expected, (int, float)):
        return False
    return math.isclose(float(reported), float(expected), rel_tol=1e-12, abs_tol=1e-15)


def _digest_hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _observed_samples(value: Any) -> list[float] | None:
    """Validate what was retained independently of the requested sample count."""
    return _core.positive_samples(value, len(value)) if isinstance(value, list) else None


def capability_disclosure(catalog: Mapping[str, Any], selected_families: Sequence[str]) -> dict[str, Any]:
    supported = list(catalog_family_ids(catalog))
    blocked = sorted(str(case["id"]) for case in catalog.get("unsupported_cases", []))
    return {
        "corpus_kind": "project-microkernel",
        "official_suite": False,
        "supported_family_count": len(supported),
        "supported_family_ids": supported,
        "selected_family_ids": list(selected_families),
        "selected_is_full_catalog": set(selected_families) == set(supported),
        "blocked_case_count": len(blocked),
        "blocked_case_ids": blocked,
        "blocked_cases_scored": False,
    }


def measurement_metadata(warmups: int, repetitions: int, run_class: str) -> dict[str, Any]:
    return {
        "warmups": warmups,
        "repetitions": repetitions,
        "minimum_sample_time_s": _core.MIN_SAMPLE_DURATION_S,
        "minimum_time_scope": "workload-route-cells",
        "calibration_target_time_s": _core.CALIBRATION_TARGET_DURATION_S,
        "maximum_batch_count": _core.MAX_BATCH_COUNT,
        "max_cv_pct": _core.MAX_CV_PCT,
        "observer_free_timing": True,
        "isolated_memory_replay": True,
        "sample_policy": "retain-all",
        "pair_order": "deterministic-random-interleaving-v1",
        "native_cache": "disabled",
        "native_cache_scope": "measured-child-only",
        "confidence_interval": {
            "level": _core.CONFIDENCE_LEVEL,
            "method": "paired-hierarchical-percentile-bootstrap",
            "resamples": _core.BOOTSTRAP_RESAMPLES,
        },
        "run_control_class": run_class,
        "ratio_direction": "candidate_over_baseline",
    }


def _annotate_cell(record: dict[str, Any], *, comparable: bool) -> dict[str, Any]:
    correctness = record.get("correctness") if isinstance(record.get("correctness"), Mapping) else {}
    baseline_ok = correctness.get("baseline") is True
    candidate_ok = correctness.get("candidate") is True
    if not baseline_ok or not candidate_ok:
        record["collection_complete"] = False
        record["time_comparison_status"] = "unavailable"
        record["time_comparison_reason"] = "correctness_failed"
        record["rss_comparison_status"] = "unavailable"
        record["rss_comparison_reason"] = "correctness_failed"
        return record
    time_samples = record.get("time_samples_s") if isinstance(record.get("time_samples_s"), Mapping) else {}
    rss_samples = record.get("peak_rss_samples_kib") if isinstance(record.get("peak_rss_samples_kib"), Mapping) else {}
    repetitions = record.get("repetitions") if isinstance(record.get("repetitions"), int) else 0
    baseline_time = _observed_samples(time_samples.get("baseline")) or None
    candidate_time = _observed_samples(time_samples.get("candidate")) or None
    baseline_rss = _observed_samples(rss_samples.get("baseline")) or None
    candidate_rss = _observed_samples(rss_samples.get("candidate")) or None
    batch = record.get("batch") if isinstance(record.get("batch"), Mapping) else {}
    raw_samples = record.get("raw_batch_time_samples_s") if isinstance(record.get("raw_batch_time_samples_s"), Mapping) else {}
    raw_by_name = {name: _observed_samples(raw_samples.get(name)) or [] for name in ("baseline", "candidate")}
    floor = _core.retained_floor_from_raw(raw_by_name)
    time_ci = record.get("time", {}).get("confidence_interval") if isinstance(record.get("time"), Mapping) else None
    rss_ci = record.get("peak_rss", {}).get("confidence_interval") if isinstance(record.get("peak_rss"), Mapping) else None
    time_status, time_reason = _core.classify_paired_status(
        numerator=candidate_time,
        denominator=baseline_time,
        confidence_interval=time_ci,
        retained_floor_met=floor,
        expected_repetitions=repetitions,
        comparable=comparable,
    )
    rss_status, rss_reason = _core.classify_paired_status(
        numerator=candidate_rss,
        denominator=baseline_rss,
        confidence_interval=rss_ci,
        retained_floor_met=True,
        expected_repetitions=repetitions,
        comparable=comparable,
    )
    record["time_comparison_status"] = time_status
    record["time_comparison_reason"] = time_reason
    record["rss_comparison_status"] = rss_status
    record["rss_comparison_reason"] = rss_reason
    artifact = record.get("artifact_bytes") if isinstance(record.get("artifact_bytes"), Mapping) else {}
    artifact_ok = (
        isinstance(artifact.get("baseline"), int)
        and isinstance(artifact.get("candidate"), int)
        and not isinstance(artifact.get("baseline"), bool)
        and not isinstance(artifact.get("candidate"), bool)
        and artifact["baseline"] > 0
        and artifact["candidate"] > 0
        and artifact.get("comparison_status") == "direct_observation"
    )
    record["collection_complete"] = (
        record.get("status") == "pass"
        and all(values is not None and len(values) == repetitions for values in (baseline_time, candidate_time, baseline_rss, candidate_rss))
        and floor
        and artifact_ok
    )
    return record


def _cell_failed_collection(record: Mapping[str, Any]) -> bool:
    if record.get("correctness") != {"baseline": True, "candidate": True}:
        return True
    if record.get("status") != "pass":
        return True
    return record.get("collection_complete") is not True


def render_summary(report: Mapping[str, Any]) -> str:
    selection = report.get("selection") if isinstance(report.get("selection"), Mapping) else {}
    capabilities = report.get("capabilities") if isinstance(report.get("capabilities"), Mapping) else {}
    toolchains = report.get("toolchains") if isinstance(report.get("toolchains"), Mapping) else {}
    lines = [
        "# Styio Analyzer v1 comparison",
        "",
        "This summary is derived from results.json.  compare success means required",
        "evidence was collected.  verify success means the report recomputes.",
        "Neither result is a performance acceptance of the candidate.",
        "",
        f"Comparison kind: {report.get('comparison_kind', COMPARISON_KIND)}",
        f"Catalog: {report.get('catalog_id')} digest {report.get('contract_digest')}",
        f"Corpus: project microkernels; not an official suite.",
        f"Scale: {selection.get('scale')}; official={selection.get('scale_official')}",
        f"Run class: {report.get('measurement', {}).get('run_control_class')}",
        f"Selected families: {', '.join(selection.get('families', [])) or 'none'}",
        f"Full catalog selected: {capabilities.get('selected_is_full_catalog')}",
        f"Requested cells: {len(selection.get('requested_cell_ids', []))}",
        f"Observed cells: {len(selection.get('observed_cell_ids', []))}",
        f"Incomplete cells: {len(selection.get('incomplete_cell_ids', []))}",
        f"Blocked capabilities (not scored): {', '.join(capabilities.get('blocked_case_ids', []))}",
        f"Collection complete: {report.get('collection_complete')}",
        f"Performance claim: {report.get('performance_claim')}",
        "",
        "## Toolchains",
        "",
        f"Baseline version {toolchains.get('baseline', {}).get('styio_version')} "
        f"revision {toolchains.get('baseline', {}).get('revision')} "
        f"dirty {toolchains.get('baseline', {}).get('dirty')} "
        f"artifact {toolchains.get('baseline', {}).get('artifact_digest')}",
        f"Candidate version {toolchains.get('candidate', {}).get('styio_version')} "
        f"revision {toolchains.get('candidate', {}).get('revision')} "
        f"dirty {toolchains.get('candidate', {}).get('dirty')} "
        f"artifact {toolchains.get('candidate', {}).get('artifact_digest')}",
        f"External clang {toolchains.get('external', {}).get('compiler_version')} "
        f"selection={toolchains.get('external', {}).get('selection')} "
        f"comparable={toolchains.get('comparable')}",
        f"Runtime binding baseline={toolchains.get('baseline', {}).get('runtime_binding')} "
        f"candidate={toolchains.get('candidate', {}).get('runtime_binding')}",
        "",
        "## Cells",
        "",
    ]
    for cell in report.get("cells", []):
        if not isinstance(cell, Mapping):
            continue
        time = cell.get("time") if isinstance(cell.get("time"), Mapping) else {}
        rss = cell.get("peak_rss") if isinstance(cell.get("peak_rss"), Mapping) else {}
        artifact = cell.get("artifact_bytes") if isinstance(cell.get("artifact_bytes"), Mapping) else {}
        lines.extend(
            [
                f"### {cell.get('id')}",
                f"- route: {cell.get('route')}",
                f"- correctness baseline={cell.get('correctness', {}).get('baseline')} "
                f"candidate={cell.get('correctness', {}).get('candidate')}",
                f"- status: {cell.get('status')}; reasons: {', '.join(cell.get('reason_codes', []) or ['none'])}",
                f"- time medians baseline={time.get('baseline_median')} candidate={time.get('candidate_median')} "
                f"ratio={time.get('geomean_ratio')} cv_b={time.get('baseline_cv_pct')} cv_c={time.get('candidate_cv_pct')}",
                f"- time interval {time.get('confidence_interval', {}).get('lower')} .. "
                f"{time.get('confidence_interval', {}).get('upper')} "
                f"status={cell.get('time_comparison_status')} ({cell.get('time_comparison_reason')})",
                f"- rss ratio={rss.get('geomean_ratio')} status={cell.get('rss_comparison_status')} "
                f"({cell.get('rss_comparison_reason')})",
                f"- artifact bytes baseline={artifact.get('baseline')} candidate={artifact.get('candidate')} "
                f"delta={artifact.get('delta')} ratio={artifact.get('ratio')} "
                f"status={artifact.get('comparison_status')}",
                "",
            ]
        )
    incomplete = selection.get("incomplete", [])
    if incomplete:
        lines.extend(["## Incomplete", ""])
        for item in incomplete:
            if isinstance(item, Mapping):
                lines.append(f"- {item.get('id')}: {item.get('reason')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def compare_toolchains(
    catalog: Mapping[str, Any],
    catalog_path: Path,
    *,
    baseline_root: Path,
    baseline_build_dir: Path,
    candidate_root: Path,
    candidate_build_dir: Path,
    families: Sequence[str] | None,
    scale: str,
    run_class: str,
    output_dir: Path | None,
    warmups: int = _core.DEFAULT_WARMUPS,
    repetitions: int = _core.REQUIRED_REPETITIONS,
    timeout_s: float = 300.0,
    event_sink: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    if run_class not in {"development", "controlled"}:
        raise ReasonError("invalid_run_control")
    if repetitions < 1 or warmups < 0:
        raise ReasonError("invalid_sample_count")
    selected_families = select_families(catalog, families)
    cells = select_cells(catalog, selected_families, scale)
    requested_ids = [str(cell["id"]) for cell in cells]
    try:
        baseline_compiler = resolve_styio_compiler(baseline_build_dir, role="baseline")
        candidate_compiler = resolve_styio_compiler(candidate_build_dir, role="candidate")
    except ReasonError as exc:
        disclosure = {
            "schema": REPORT_SCHEMA,
            "schema_version": REPORT_VERSION,
            "comparison_kind": COMPARISON_KIND,
            "catalog_id": catalog.get("catalog_id"),
            "contract_digest": _core.contract_digest(catalog),
            "runner_version": RUNNER_VERSION,
            "selection": {
                "families": list(selected_families),
                "scale": scale,
                "scale_official": bool(catalog.get("scales", {}).get(scale, {}).get("official")),
                "requested_cell_ids": requested_ids,
                "observed_cell_ids": [],
                "incomplete_cell_ids": requested_ids,
                "incomplete": [{"id": cell_id, "reason": exc.reason_code} for cell_id in requested_ids],
                "subset_of_catalog": set(selected_families) != set(catalog_family_ids(catalog)),
            },
            "toolchains": {
                "baseline": {
                    "role": "baseline",
                    "traceable": False,
                    "artifact_digest": "untraceable",
                    "runtime_binding": "unconfirmed",
                    "runtime_marker": "missing",
                    "binding_basis": "none",
                },
                "candidate": {
                    "role": "candidate",
                    "traceable": False,
                    "artifact_digest": "untraceable",
                    "runtime_binding": "unconfirmed",
                    "runtime_marker": "missing",
                    "binding_basis": "none",
                },
                "external": external_clang_identity(),
                "comparable": False,
                "incomparable_reasons": [exc.reason_code],
            },
            "measurement": measurement_metadata(warmups, repetitions, run_class),
            "capabilities": capability_disclosure(catalog, selected_families),
            "cells": [],
            "collection_complete": False,
            "performance_claim": "not-evaluated",
        }
        _core.assert_public_report(disclosure)
        if output_dir is not None:
            _core.write_public_json(output_dir / "results.json", disclosure)
            _core.write_public_text(output_dir / "summary.md", render_summary(disclosure))
        disclosure["_collection_failed"] = True
        return disclosure
    if baseline_compiler == candidate_compiler and baseline_build_dir.resolve() != candidate_build_dir.resolve():
        raise ReasonError("toolchain_isolation")
    baseline_id = toolchain_identity(baseline_compiler, baseline_root, baseline_build_dir, role="baseline")
    candidate_id = toolchain_identity(candidate_compiler, candidate_root, candidate_build_dir, role="candidate")
    baseline_cxx, baseline_selection = resolve_native_cxx(
        styio_compiler=baseline_compiler, build_dir=baseline_build_dir
    )
    candidate_cxx, candidate_selection = resolve_native_cxx(
        styio_compiler=candidate_compiler, build_dir=candidate_build_dir
    )
    baseline_cxx_id = external_from_resolved(baseline_cxx, baseline_selection)
    candidate_cxx_id = external_from_resolved(candidate_cxx, candidate_selection)
    probe_source = catalog_path.parent / next(
        item["source"]["styio"] for item in catalog["workloads"] if item["id"] == cells[0]["family"]
    )
    baseline_binding = runtime_binding_status(baseline_root, baseline_compiler, probe_source, timeout_s=timeout_s, driver_mode=baseline_cxx_id.get("driver_mode", "unknown"))
    candidate_binding = (
        dict(baseline_binding)
        if baseline_compiler == candidate_compiler and baseline_root.resolve() == candidate_root.resolve()
        else runtime_binding_status(candidate_root, candidate_compiler, probe_source, timeout_s=timeout_s, driver_mode=candidate_cxx_id.get("driver_mode", "unknown"))
    )
    baseline_id.update(baseline_binding)
    candidate_id.update(candidate_binding)
    baseline_id["native_cxx_version"] = baseline_cxx_id.get("compiler_version")
    candidate_id["native_cxx_version"] = candidate_cxx_id.get("compiler_version")
    baseline_id["native_cxx"] = baseline_cxx_id
    candidate_id["native_cxx"] = candidate_cxx_id
    comparable, incomparable_reasons = assess_comparability(
        baseline_cxx=baseline_cxx,
        candidate_cxx=candidate_cxx,
        baseline_binding=baseline_id,
        candidate_binding=candidate_id,
        baseline_identity=baseline_cxx_id,
        candidate_identity=candidate_cxx_id,
    )
    if comparable:
        clang = dict(baseline_cxx_id)
    elif baseline_cxx is not None and candidate_cxx is None:
        clang = dict(baseline_cxx_id)
    elif candidate_cxx is not None and baseline_cxx is None:
        clang = dict(candidate_cxx_id)
    elif baseline_cxx_id.get("artifact_digest") == candidate_cxx_id.get("artifact_digest"):
        clang = dict(baseline_cxx_id)
    else:
        clang = {
            "compiler_family": "clang",
            "compiler_version": "mismatch" if baseline_cxx and candidate_cxx else "unknown",
            "present": bool(baseline_cxx_id.get("present") or candidate_cxx_id.get("present")),
            "optimization": "O3",
            "lto": False,
            "threads": 1,
            "selection": baseline_selection if baseline_selection == candidate_selection else "mismatch",
            "artifact_digest": "untraceable",
            "pinned": False,
        }
    if comparable:
        clang["optimization"] = baseline_binding["native_configuration"]["optimization"]
        clang["lto"] = baseline_binding["native_configuration"]["lto"] != "disabled"
    generators = _gate._load_generators(catalog_path)
    report_cells: list[dict[str, Any]] = []
    incomplete: list[dict[str, str]] = []
    collection_failed = False
    for cell in cells:
        family = str(cell["family"])
        source = next(item["source"]["styio"] for item in catalog["workloads"] if item["id"] == family)
        styio_source = catalog_path.parent / source
        input_bytes = generators.input_for(family, int(cell["work_units"]))
        expected_output = generators.reference_for(family, int(cell["work_units"]))
        left = _core.SideSpec(
            "baseline",
            baseline_compiler,
            styio_source,
            baseline_root,
            True,
            child_env_for_side(baseline_cxx),
        )
        right = _core.SideSpec(
            "candidate",
            candidate_compiler,
            styio_source,
            candidate_root,
            True,
            child_env_for_side(candidate_cxx),
        )
        try:
            record = _core.measure_paired_cell(
                cell,
                left=left,
                right=right,
                input_bytes=input_bytes,
                expected_output=expected_output,
                expected_digest=str(cell["expected_output_digest"]),
                warmups=warmups,
                repetitions=repetitions,
                timeout_s=timeout_s,
                runner_version=RUNNER_VERSION,
                ratio_numerator="candidate",
                ratio_denominator="baseline",
                collect_artifact_bytes=True,
                event_sink=event_sink,
                include_side_names=True,
            )
        except _core.ReasonError as exc:
            record = _core.incomplete_cell(
                cell,
                left_name="baseline",
                right_name="candidate",
                correctness={"baseline": False, "candidate": False},
                warmups=warmups,
                repetitions=repetitions,
                reason=exc.reason_code,
                measurement_stage="preflight",
            )
        record = _annotate_cell(record, comparable=comparable)
        if _cell_failed_collection(record):
            collection_failed = True
            incomplete.append({"id": str(record.get("id")), "reason": ",".join(record.get("reason_codes") or ["incomplete"])})
        report_cells.append(record)
    observed_ids = [str(cell.get("id")) for cell in report_cells]
    collection_complete = not collection_failed and set(observed_ids) == set(requested_ids)
    report = {
        "schema": REPORT_SCHEMA,
        "schema_version": REPORT_VERSION,
        "comparison_kind": COMPARISON_KIND,
        "catalog_id": catalog.get("catalog_id"),
        "contract_digest": _core.contract_digest(catalog),
        "runner_version": RUNNER_VERSION,
        "selection": {
            "families": list(selected_families),
            "scale": scale,
            "scale_official": bool(catalog.get("scales", {}).get(scale, {}).get("official")),
            "requested_cell_ids": requested_ids,
            "observed_cell_ids": observed_ids,
            "incomplete_cell_ids": [item["id"] for item in incomplete],
            "incomplete": incomplete,
            "subset_of_catalog": set(selected_families) != set(catalog_family_ids(catalog)),
        },
        "toolchains": {
            "baseline": baseline_id,
            "candidate": candidate_id,
            "external": clang,
            "comparable": comparable,
            "incomparable_reasons": incomparable_reasons,
        },
        "measurement": measurement_metadata(warmups, repetitions, run_class),
        "capabilities": capability_disclosure(catalog, selected_families),
        "cells": report_cells,
        "collection_complete": collection_complete,
        "performance_claim": "not-evaluated",
    }
    _core.assert_public_report(report)
    if output_dir is not None:
        _core.write_public_json(output_dir / "results.json", report)
        _core.write_public_text(output_dir / "summary.md", render_summary(report))
    report["_collection_failed"] = collection_failed
    return report


def _stat_equal(reported: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        return isinstance(reported, Mapping) and set(reported) == set(expected) and all(
            _stat_equal(reported[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(reported, list) and len(reported) == len(expected) and all(
            _stat_equal(left, right) for left, right in zip(reported, expected)
        )
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return _numbers_close(reported, expected)
    return type(reported) is type(expected) and reported == expected


def _compare_ratio_fields(reported: Any, expected: Mapping[str, Any], *, kind: str) -> list[str]:
    reasons = []
    if not _stat_equal(reported, expected):
        reasons.append("ratio_mismatch" if kind == "time" else "memory_ratio_mismatch")
    if not isinstance(reported, Mapping) or not _stat_equal(reported.get("confidence_interval"), expected["confidence_interval"]):
        reasons.append("interval_mismatch")
    return reasons


def _recompute_cell(cell: Mapping[str, Any], *, expected_repetitions: int, comparable: bool) -> dict[str, Any]:
    reasons: list[str] = []
    names = ("baseline", "candidate")
    repetitions = cell.get("repetitions")
    if type(repetitions) is not int or repetitions < 1 or repetitions != expected_repetitions:
        return {"reasons": ["sample_count_mismatch"]}

    def samples(field: str) -> dict[str, list[float]]:
        value = cell.get(field)
        empty = {name: [] for name in names}
        if value is None:
            return empty
        if not isinstance(value, Mapping) or set(value) != set(names):
            reasons.append("samples_malformed")
            return empty
        result = {}
        for name in names:
            observed = _observed_samples(value[name])
            if observed is None or len(observed) > repetitions:
                reasons.append("samples_malformed")
                observed = []
            result[name] = observed
        return result

    time = samples("time_samples_s")
    raw = samples("raw_batch_time_samples_s")
    rss = samples("peak_rss_samples_kib")
    unpaired = samples("unpaired_time_samples_s")
    unpaired_raw = samples("unpaired_raw_batch_time_samples_s")
    unpaired_rss = samples("unpaired_rss_samples_kib")
    batch = cell.get("batch") if isinstance(cell.get("batch"), Mapping) else {}
    batch_count = batch.get("count")
    has_time = any(time.values()) or any(raw.values()) or any(unpaired.values()) or any(unpaired_raw.values())
    valid_batch = type(batch_count) is int and batch_count > 0
    if (has_time or batch) and not valid_batch:
        reasons.append("batch_count_malformed")
    for normalized, totals in ((time, raw), (unpaired, unpaired_raw)):
        for name in names:
            if len(normalized[name]) != len(totals[name]):
                reasons.append("samples_malformed")
            elif valid_batch and any(
                not _numbers_close(value, total / batch_count)
                for value, total in zip(normalized[name], totals[name])
            ):
                reasons.append("normalization_mismatch")
    for pairs in (time, raw, rss):
        if len(pairs["baseline"]) != len(pairs["candidate"]):
            reasons.append("unpaired_sample_mismatch")
    floor = _core.retained_floor_from_raw(raw)
    if (has_time or batch) and batch.get("retained_floor_met") is not floor:
        reasons.append("retained_floor_mismatch")

    orders = _core.interleaved_pair_orders(
        str(cell.get("id")), repetitions, "retained", left="baseline", right="candidate", runner_version=RUNNER_VERSION
    )
    retained_count = len(time["baseline"])
    completed_orders = orders[:retained_count]
    if has_time or "pair_orders" in cell or "sample_schedule" in cell:
        expected_codes = [f"{first},{second}" for first, second in completed_orders]
        if cell.get("pair_orders") != expected_codes:
            reasons.append("sample_schedule")
        codes = ["AB" if order == names else "BA" for order in completed_orders]
        expected_schedule = {
            "strategy": "deterministic-random-interleaving-v1",
            "ab_count": codes.count("AB"),
            "ba_count": codes.count("BA"),
            "schedule_digest": _core.sha256("".join(codes).encode("ascii")),
        }
        if cell.get("sample_schedule") != expected_schedule:
            reasons.append("sample_schedule")
    rss_orders = _core.interleaved_pair_orders(
        str(cell.get("id")), repetitions, "rss", left="baseline", right="candidate", runner_version=RUNNER_VERSION
    )
    for extras, count, schedule in ((unpaired, retained_count, orders), (unpaired_rss, len(rss["baseline"]), rss_orders)):
        extra_count = sum(map(len, extras.values()))
        if extra_count and (extra_count != 1 or count >= repetitions or not extras[schedule[count][0]]):
            reasons.append("unpaired_sample_mismatch")

    recomputed: dict[str, Any] = {}
    for field, values, kind in (("time", time, "time"), ("peak_rss", rss, "rss")):
        full = all(len(values[name]) == repetitions for name in names)
        if full:
            dimension = _core.ratio_dimension(values["candidate"], values["baseline"], left_name="candidate", right_name="baseline")
            recomputed[field] = dimension
            reasons.extend(_compare_ratio_fields(cell.get(field), dimension, kind=kind))
        elif cell.get(field) not in (None, {}):
            reasons.append("statistics_without_complete_samples")
    if cell.get("throughput") is not None:
        if "time" in recomputed:
            expected = _core.ratio_dimension(time["candidate"], time["baseline"], reciprocal=True, left_name="candidate", right_name="baseline")
            if not _stat_equal(cell["throughput"], expected):
                reasons.append("throughput_mismatch")
        else:
            reasons.append("statistics_without_complete_samples")
    if cell.get("status") == "pass" and any(len(values[name]) != repetitions for values in (time, raw, rss) for name in names):
        reasons.append("samples_malformed")
    correctness_ok = cell.get("correctness") == {"baseline": True, "candidate": True}
    for kind, values, field, floor_met in (("time", time, "time", floor), ("rss", rss, "peak_rss", True)):
        if correctness_ok:
            status, reason = _core.classify_paired_status(
                numerator=values["candidate"] or None,
                denominator=values["baseline"] or None,
                confidence_interval=(recomputed.get(field) or {}).get("confidence_interval"),
                retained_floor_met=floor_met,
                expected_repetitions=repetitions,
                comparable=comparable,
            )
        else:
            status, reason = "unavailable", "correctness_failed"
        if cell.get(f"{kind}_comparison_status") != status or cell.get(f"{kind}_comparison_reason") != reason:
            reasons.append("status_mismatch" if kind == "time" else "rss_status_mismatch")
        recomputed[f"{kind}_comparison_status"] = status
        recomputed[f"{kind}_comparison_reason"] = reason

    artifact = cell.get("artifact_bytes")
    if isinstance(artifact, Mapping) and artifact.get("comparison_status") == "direct_observation":
        baseline_bytes, candidate_bytes = artifact.get("baseline"), artifact.get("candidate")
        if type(baseline_bytes) is not int or type(candidate_bytes) is not int or baseline_bytes <= 0 or candidate_bytes <= 0:
            reasons.append("artifact_values_malformed")
        else:
            if artifact.get("delta") != candidate_bytes - baseline_bytes:
                reasons.append("artifact_delta_mismatch")
            if not _numbers_close(artifact.get("ratio"), candidate_bytes / baseline_bytes):
                reasons.append("artifact_ratio_mismatch")
            if artifact.get("origin") != "untimed-native-artifact":
                reasons.append("artifact_origin_mismatch")
    elif artifact not in (None, {}):
        if not isinstance(artifact, Mapping) or artifact.get("comparison_status") != "unavailable" or not artifact.get("reason") or any(key in artifact for key in ("baseline", "candidate", "ratio", "delta")):
            reasons.append("artifact_values_malformed")
    recomputed["reasons"] = reasons
    return recomputed


def _verify_toolchain_evidence(toolchains: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    comparable = toolchains.get("comparable") is True
    incomparable = toolchains.get("incomparable_reasons")
    if not isinstance(incomparable, list):
        incomparable = []
    external = toolchains.get("external") if isinstance(toolchains.get("external"), Mapping) else {}
    baseline = toolchains.get("baseline") if isinstance(toolchains.get("baseline"), Mapping) else {}
    candidate = toolchains.get("candidate") if isinstance(toolchains.get("candidate"), Mapping) else {}
    evidence_ok = (
        external.get("present") is True
        and _digest_hex(external.get("artifact_digest"))
        and external.get("selection") in CXX_SELECTION
        and baseline.get("runtime_binding") == "confirmed"
        and candidate.get("runtime_binding") == "confirmed"
        and baseline.get("binding_basis") == "native_build_probe"
        and candidate.get("binding_basis") == "native_build_probe"
        and baseline.get("runtime_marker") == "present"
        and candidate.get("runtime_marker") == "present"
        and external.get("pinned") is True
        and not incomparable
    )
    if comparable and not evidence_ok:
        reasons.append("toolchain_binding_unconfirmed")
    if comparable and incomparable:
        reasons.append("comparable_mismatch")
    if not comparable and not incomparable:
        reasons.append("comparable_mismatch")
    if comparable and baseline.get("native_cxx_version") not in {None, external.get("compiler_version")}:
        reasons.append("external_compiler_mismatch")
    if comparable and candidate.get("native_cxx_version") not in {None, external.get("compiler_version")}:
        reasons.append("external_compiler_mismatch")
    identities = [side.get("native_cxx") for side in (baseline, candidate)]
    if all(isinstance(identity, Mapping) for identity in identities):
        verified, derived_reasons = assess_comparability(
            baseline_cxx=Path("observed") if identities[0].get("pinned") else None,
            candidate_cxx=Path("observed") if identities[1].get("pinned") else None,
            baseline_binding=baseline,
            candidate_binding=candidate,
            baseline_identity=identities[0],
            candidate_identity=identities[1],
        )
        if verified != comparable or set(derived_reasons) != set(incomparable):
            reasons.append("comparable_mismatch")
        if comparable:
            expected_external = dict(identities[0])
            config = baseline["native_configuration"]
            expected_external.update(optimization=config["optimization"], lto=config["lto"] != "disabled")
            if external != expected_external:
                reasons.append("external_configuration_mismatch")
    elif comparable:
        reasons.append("toolchain_binding_unconfirmed")
    return reasons


def verify_report(
    report: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    privacy: str = "strict",
) -> dict[str, Any]:
    if privacy not in {"strict", "off"}:
        raise ReasonError("invalid_privacy_mode")
    if privacy == "strict":
        _core.assert_public_report(report)
    reasons: list[str] = []
    if report.get("schema") != REPORT_SCHEMA or report.get("schema_version") != REPORT_VERSION:
        reasons.append("report_schema")
    if report.get("comparison_kind") != COMPARISON_KIND:
        reasons.append("comparison_kind")
    if report.get("runner_version") != RUNNER_VERSION:
        reasons.append("runner_version")
    if report.get("contract_digest") != _core.contract_digest(catalog):
        reasons.append("contract_mismatch")
    if report.get("catalog_id") != catalog.get("catalog_id"):
        reasons.append("contract_mismatch")
    if report.get("performance_claim") != "not-evaluated":
        reasons.append("performance_claim")
    selection = report.get("selection") if isinstance(report.get("selection"), Mapping) else {}
    cells = report.get("cells") if isinstance(report.get("cells"), list) else []
    requested = selection.get("requested_cell_ids")
    if not isinstance(requested, list) or any(not isinstance(item, str) for item in requested):
        requested = []
        reasons.append("selection_mismatch")
    observed = [str(cell.get("id")) for cell in cells if isinstance(cell, Mapping)]
    if len(requested) != len(set(requested)) or len(observed) != len(set(observed)):
        reasons.append("duplicate_cell_id")
    if selection.get("observed_cell_ids") != observed:
        reasons.append("coverage_mismatch")
    families = selection.get("families")
    scale = selection.get("scale")
    expected_cells: list[dict[str, Any]] = []
    expected_ids: list[str] = []
    selected: list[str] = []
    if isinstance(families, list) and isinstance(scale, str):
        try:
            selected = select_families(catalog, families)
            if not families or selected != families:
                reasons.append("family_coverage")
            expected_cells = select_cells(catalog, selected, scale)
            expected_ids = [str(cell["id"]) for cell in expected_cells]
            if set(requested) != set(expected_ids):
                reasons.append("selection_mismatch")
        except ReasonError:
            reasons.append("family_coverage")
    else:
        reasons.append("family_coverage")
    catalog_by_id = {str(cell["id"]): cell for cell in expected_cells}
    incomplete_listed = selection.get("incomplete_cell_ids")
    if not isinstance(incomplete_listed, list) or any(not isinstance(item, str) for item in incomplete_listed):
        incomplete_listed = []
        reasons.append("incomplete_disclosure")
    incomplete_records = selection.get("incomplete") if isinstance(selection.get("incomplete"), list) else []
    incomplete_record_ids = [
        str(item.get("id"))
        for item in incomplete_records
        if isinstance(item, Mapping) and item.get("id") and item.get("reason")
    ]
    observed_set = set(observed)
    expected_set = set(expected_ids)
    incomplete_set = set(incomplete_listed)
    for cell_id in expected_ids:
        if cell_id not in observed_set and cell_id not in incomplete_set:
            reasons.append("incomplete_disclosure")
            break
    for cell in cells:
        if not isinstance(cell, Mapping):
            reasons.append("cell_malformed")
            continue
        catalog_cell = catalog_by_id.get(str(cell.get("id")))
        if catalog_cell is None:
            reasons.append("selection_mismatch")
            continue
        if (
            cell.get("work_units") != catalog_cell.get("work_units")
            or cell.get("algorithm_id") != catalog_cell.get("algorithm_id")
            or cell.get("expected_output_digest") != catalog_cell.get("expected_output_digest")
            or cell.get("family") != catalog_cell.get("family")
            or cell.get("scale") != catalog_cell.get("scale")
            or cell.get("route") != catalog_cell.get("route")
            or cell.get("source_digests") != catalog_cell.get("source_digest")
            or cell.get("input_digest") != catalog_cell.get("input_digest")
            or cell.get("required") != catalog_cell.get("required")
            or cell.get("focus_owner") != catalog_cell.get("focus_owner")
        ):
            reasons.append("cell_identity_mismatch")
    capabilities = report.get("capabilities") if isinstance(report.get("capabilities"), Mapping) else {}
    expected_caps = capability_disclosure(catalog, selected)
    if capabilities != expected_caps:
        reasons.append("capability_disclosure")
    if capabilities.get("selected_is_full_catalog") is True and not expected_caps["selected_is_full_catalog"]:
        reasons.append("subset_presented_as_full")
    if selection.get("subset_of_catalog") is not (not expected_caps["selected_is_full_catalog"]):
        reasons.append("coverage_mismatch")
    if isinstance(scale, str) and selection.get("scale_official") is not bool(catalog.get("scales", {}).get(scale, {}).get("official")):
        reasons.append("coverage_mismatch")
    measurement = report.get("measurement") if isinstance(report.get("measurement"), Mapping) else {}
    expected_reps = measurement.get("repetitions") if isinstance(measurement.get("repetitions"), int) else _core.REQUIRED_REPETITIONS
    warmups = measurement.get("warmups")
    if type(expected_reps) is not int or expected_reps < 1 or type(warmups) is not int or warmups < 0 or measurement.get("run_control_class") not in {"development", "controlled"}:
        reasons.append("measurement_configuration")
    elif not _stat_equal(measurement, measurement_metadata(warmups, expected_reps, measurement["run_control_class"])):
        reasons.append("measurement_configuration")
    toolchains = report.get("toolchains") if isinstance(report.get("toolchains"), Mapping) else {}
    comparable = toolchains.get("comparable") is True
    reasons.extend(_verify_toolchain_evidence(toolchains))
    failed_ids: set[str] = set()
    for cell in cells:
        if not isinstance(cell, Mapping):
            continue
        recomputed = _recompute_cell(cell, expected_repetitions=expected_reps, comparable=comparable)
        reasons.extend(recomputed["reasons"])
        if cell.get("warmups") != warmups:
            reasons.append("measurement_configuration")
        annotated = _annotate_cell(dict(cell), comparable=comparable)
        if cell.get("collection_complete") is not annotated["collection_complete"]:
            reasons.append("cell_collection_complete_mismatch")
        if _cell_failed_collection(annotated):
            failed_ids.add(str(cell.get("id")))
        if cell.get("status") == "pass":
            for sample_map in (cell.get("time_samples_s"), cell.get("peak_rss_samples_kib")):
                if not isinstance(sample_map, Mapping):
                    continue
                for values in sample_map.values():
                    if isinstance(values, list) and any(isinstance(item, float) and not math.isfinite(item) for item in values):
                        reasons.append("nonfinite_number")
    unexecuted = expected_set - observed_set
    derived_incomplete = set(failed_ids) | unexecuted
    if len(incomplete_listed) != len(set(incomplete_listed)) or len(incomplete_record_ids) != len(set(incomplete_record_ids)):
        reasons.append("duplicate_cell_id")
    if set(incomplete_listed) != derived_incomplete:
        reasons.append("incomplete_disclosure")
    if set(incomplete_record_ids) != derived_incomplete:
        reasons.append("incomplete_disclosure")
    failed_by_id = {str(cell.get("id")): cell for cell in cells if isinstance(cell, Mapping) and str(cell.get("id")) in failed_ids}
    for item in incomplete_records:
        if isinstance(item, Mapping) and item.get("id") in failed_by_id:
            expected_reason = ",".join(failed_by_id[item["id"]].get("reason_codes") or ["incomplete"])
            if item.get("reason") != expected_reason:
                reasons.append("incomplete_disclosure")
    derived_complete = not derived_incomplete and expected_set == observed_set and bool(expected_set)
    if report.get("collection_complete") is not derived_complete:
        reasons.append("collection_complete_mismatch")
    verdict = {
        "schema": VERDICT_SCHEMA,
        "decision": "pass" if not reasons else "fail",
        "reason_codes": sorted(set(reasons)),
        "cell_count": len(cells),
        "incomplete_cell_count": len(derived_incomplete),
        "collection_complete": derived_complete,
        "performance_claim": "not-evaluated",
        "note": "verify success is report consistency, not candidate performance acceptance",
    }
    _core.assert_public_report(verdict)
    return verdict


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare two Styio builds on the parity-v2 corpus.",
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="operation", required=True)
    compare = sub.add_parser("compare", help="Execute paired Styio/Styio measurements.", epilog=HELP_EPILOG)
    compare.add_argument("--contract", default=str(CATALOG_DEFAULT))
    compare.add_argument("--baseline-root", required=True)
    compare.add_argument("--baseline-build-dir", required=True)
    compare.add_argument("--candidate-root", required=True)
    compare.add_argument("--candidate-build-dir", required=True)
    compare.add_argument("--family", action="append", dest="families")
    compare.add_argument("--scale", choices=SCALES, default="smoke")
    compare.add_argument("--run-class", choices=("development", "controlled"), default="development")
    compare.add_argument("--out-dir", required=True)
    compare.add_argument("--warmups", type=int, default=_core.DEFAULT_WARMUPS)
    compare.add_argument("--repetitions", type=int, default=_core.REQUIRED_REPETITIONS)
    compare.add_argument("--timeout-s", type=float, default=300.0)
    verify = sub.add_parser("verify", help="Recompute a comparison report offline.", epilog=HELP_EPILOG)
    verify.add_argument("--contract", default=str(CATALOG_DEFAULT))
    verify.add_argument("--report", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        catalog, catalog_path = _gate.load_catalog(args.contract)
        if args.operation == "compare":
            if os.environ.get("STYIO_NATIVE_CACHE") is not None:
                # Child overlays set the flag; the parent process is left unchanged.
                pass
            report = compare_toolchains(
                catalog,
                catalog_path,
                baseline_root=Path(args.baseline_root).resolve(),
                baseline_build_dir=Path(args.baseline_build_dir).resolve(),
                candidate_root=Path(args.candidate_root).resolve(),
                candidate_build_dir=Path(args.candidate_build_dir).resolve(),
                families=args.families,
                scale=args.scale,
                run_class=args.run_class,
                output_dir=Path(args.out_dir).resolve(),
                warmups=args.warmups,
                repetitions=args.repetitions,
                timeout_s=args.timeout_s,
            )
            failed = bool(report.pop("_collection_failed", False))
            print(
                json.dumps(
                    {
                        "schema": "styio.analyzer.compare.result.v1",
                        "decision": "fail" if failed else "pass",
                        "collection_complete": report.get("collection_complete"),
                        "performance_claim": "not-evaluated",
                    },
                    sort_keys=True,
                )
            )
            return 2 if failed else 0
        payload = json.loads(Path(args.report).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ReasonError("report_shape")
        verdict = verify_report(payload, catalog)
        print(json.dumps(verdict, sort_keys=True))
        return 0 if verdict["decision"] == "pass" else 2
    except (ReasonError, PrivacyError, OSError, ValueError, json.JSONDecodeError) as exc:
        reason = exc.reason_code if isinstance(exc, ReasonError) else "runtime_error"
        print(json.dumps({"schema": "styio.analyzer.error.v1", "decision": "fail", "reason_code": reason}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
