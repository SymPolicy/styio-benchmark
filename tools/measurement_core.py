#!/usr/bin/env python3
"""Shared sampling, statistics, and privacy helpers for parity and Analyzer.

This module owns the party-agnostic measurement algorithm.  Styio/C++ parity
and Styio/Styio Analyzer bind side names through adapters; they must not keep
a second copy of calibration, interleaving, normalization, or bootstrap code.
"""

from __future__ import annotations

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
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_REPETITIONS = 11
DEFAULT_WARMUPS = 3
MIN_SAMPLE_DURATION_S = 0.5
CALIBRATION_TARGET_DURATION_S = 0.75
MAX_BATCH_COUNT = 20_000
CONFIDENCE_LEVEL = 0.95
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 0x53545949
MAX_CV_PCT = 5.0
CPP_FLAGS = ("-std=c++20", "-O3", "-DNDEBUG", "-fno-lto")
HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
PUBLIC_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,63}$")
URL_RE = re.compile(r"(?:https?|file|ssh|git)://", re.IGNORECASE)
ABS_PATH_RE = re.compile(
    r"(?:^[A-Za-z]:[\\/]|^/|(?:^|/)(?:Users|home|private|tmp|var|opt|Volumes|mnt)/)",
    re.IGNORECASE,
)
SECRET_RE = re.compile(
    r"(?:-----BEGIN|(?:bearer|basic)\s+|(?:sk|ghp|gho|github_pat|xox[baprs])-|AKIA[0-9A-Z]{12,})",
    re.IGNORECASE,
)
FORBIDDEN_KEY_RE = re.compile(
    r"(?:^|[_-])(host(?:name)?|user(?:name)?|machine|environment|env|command|argv|path|dir|cwd|root|exec(?:utable)?_path|build_dir|source_path|file_path|absolute_path|url|uri|endpoint|secret|password|credential|api_key|access_token|authorization|stderr|stdout|stack|trace)(?:$|[_-])",
    re.IGNORECASE,
)


class ReasonError(RuntimeError):
    """Fail-closed error carrying a privacy-safe stable reason code."""

    def __init__(self, reason_code: str, message: str = "") -> None:
        self.reason_code = reason_code
        super().__init__(message or reason_code)


class PrivacyError(ReasonError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def contract_digest(contract: Mapping[str, Any]) -> str:
    return sha256(canonical_json(contract))


def public_key_allowed(key: str) -> bool:
    return not FORBIDDEN_KEY_RE.search(key)


def public_string_allowed(value: str) -> bool:
    return not (
        "\x00" in value
        or "\n" in value
        or "\r" in value
        or URL_RE.search(value)
        or ABS_PATH_RE.search(value)
        or SECRET_RE.search(value)
    )


def validate_public_report(value: Any, *, strict: bool = True, _location: str = "report") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or (strict and not public_key_allowed(key)):
                raise PrivacyError("privacy_forbidden_key", _location)
            validate_public_report(child, strict=strict, _location=f"{_location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            validate_public_report(child, strict=strict, _location=f"{_location}[{index}]")
    elif isinstance(value, str):
        if strict and not public_string_allowed(value):
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


def write_public_text(path: Path, text: str) -> None:
    if not public_string_allowed(text.replace("\n", " ").replace("\r", " ")):
        # Markdown may contain newlines; reject only embedded private tokens.
        for line in text.splitlines():
            if line and not public_string_allowed(line):
                raise PrivacyError("privacy_forbidden_value", "summary")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


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


def percentile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values or not 0.0 <= probability <= 1.0:
        raise ValueError("percentile requires values and a probability")
    position = probability * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return float(sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction)


def bootstrap_geomean_ratio_ci(
    paired_log_groups: Sequence[Sequence[float]],
    *,
    confidence_level: float = CONFIDENCE_LEVEL,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Return a deterministic hierarchical percentile-bootstrap interval."""

    groups = [tuple(float(value) for value in group) for group in paired_log_groups]
    if not groups or any(not group for group in groups):
        raise ValueError("bootstrap requires non-empty paired groups")
    if any(not math.isfinite(value) for group in groups for value in group):
        raise ValueError("bootstrap requires finite log-ratios")
    if not 0.0 < confidence_level < 1.0 or resamples <= 0:
        raise ValueError("invalid bootstrap configuration")
    generator = random.Random(seed)
    estimates: list[float] = []
    group_count = len(groups)
    for _ in range(resamples):
        cell_means: list[float] = []
        for _cell_index in range(group_count):
            group = groups[generator.randrange(group_count)]
            cell_means.append(statistics.fmean(group[generator.randrange(len(group))] for _ in range(len(group))))
        estimates.append(math.exp(statistics.fmean(cell_means)))
    estimates.sort()
    tail = (1.0 - confidence_level) / 2.0
    return {
        "level": confidence_level,
        "method": "paired-hierarchical-percentile-bootstrap",
        "resamples": resamples,
        "lower": percentile(estimates, tail),
        "upper": percentile(estimates, 1.0 - tail),
    }


def paired_log_ratios(left: Sequence[float], right: Sequence[float], *, reciprocal: bool = False) -> list[float]:
    if len(left) != len(right) or not left or any(a <= 0 or b <= 0 for a, b in zip(left, right)):
        raise ValueError("paired samples must have equal positive length")
    return [math.log((b / a) if reciprocal else (a / b)) for a, b in zip(left, right)]


def ratio_dimension(
    left: Sequence[float],
    right: Sequence[float],
    *,
    reciprocal: bool = False,
    left_name: str = "styio",
    right_name: str = "cpp",
) -> dict[str, Any]:
    left_values, right_values = list(left), list(right)
    if len(left_values) != len(right_values) or not left_values:
        raise ValueError("ratio dimension requires equal non-empty samples")
    ratios = [(b / a) if reciprocal else (a / b) for a, b in zip(left_values, right_values)]
    logs = [math.log(value) for value in ratios]
    return {
        f"{left_name}_samples": left_values,
        f"{right_name}_samples": right_values,
        f"{left_name}_median": median(left_values),
        f"{right_name}_median": median(right_values),
        f"{left_name}_cv_pct": sample_cv_pct(left_values),
        f"{right_name}_cv_pct": sample_cv_pct(right_values),
        "median_ratio": median(ratios),
        "geomean_ratio": geometric_mean(ratios),
        "paired_log_ratios": logs,
        "confidence_interval": bootstrap_geomean_ratio_ci([logs]),
    }


def calibrate_batch_count(
    estimates_s: Sequence[float],
    *,
    target_duration_s: float = CALIBRATION_TARGET_DURATION_S,
    max_batch: int = MAX_BATCH_COUNT,
) -> int:
    """Choose one equal-work batch count from the faster implementation."""

    if not estimates_s or any(not math.isfinite(float(value)) or value <= 0 for value in estimates_s):
        raise ValueError("calibration estimates must be finite and positive")
    if not math.isfinite(target_duration_s) or target_duration_s <= 0 or max_batch <= 0:
        raise ValueError("invalid calibration configuration")
    fastest = min(float(value) for value in estimates_s)
    return max(1, min(max_batch, math.ceil(target_duration_s / fastest)))


def normalize_batched_elapsed(elapsed_s: float, batch_count: int) -> float:
    if not math.isfinite(float(elapsed_s)) or elapsed_s <= 0 or batch_count <= 0:
        raise ValueError("batched elapsed time must be finite and positive")
    return float(elapsed_s) / float(batch_count)


def interleaved_pair_orders(
    identity: str,
    count: int,
    stage: str,
    *,
    left: str = "styio",
    right: str = "cpp",
    runner_version: str,
) -> tuple[tuple[str, str], ...]:
    """Create a reproducible, balanced random-interleaving schedule."""

    if count < 0:
        raise ValueError("schedule count must be non-negative")
    orders = [(left, right) if index % 2 == 0 else (right, left) for index in range(count)]
    digest = hashlib.sha256(f"{runner_version}|{identity}|{stage}".encode("utf-8")).digest()
    random.Random(int.from_bytes(digest[:8], "big")).shuffle(orders)
    return tuple(orders)


def positive_samples(value: Any, repetitions: int) -> list[float] | None:
    if not isinstance(value, list) or len(value) != repetitions:
        return None
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or float(item) <= 0
        or not math.isfinite(float(item))
        for item in value
    ):
        return None
    return [float(item) for item in value]


def batched_output_matches(output: Path, expected: bytes, count: int, *, repeated: bool) -> bool:
    """Validate a batch without loading a potentially repeated output at once."""

    expected_count = count if repeated else 1
    try:
        with output.open("rb") as stream:
            for _ in range(expected_count):
                if stream.read(len(expected)) != expected:
                    return False
            return stream.read(1) == b""
    except OSError:
        return False


def load_rss_helper() -> Any:
    helper_path = ROOT / "native-cpp" / "standard_process_tree_rss.py"
    spec = importlib.util.spec_from_file_location("shared_process_tree_rss", helper_path)
    if spec is None or spec.loader is None:
        raise ReasonError("rss_helper_missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def merge_child_env(extra: Mapping[str, str] | None) -> dict[str, str] | None:
    if extra is None:
        return None
    merged = os.environ.copy()
    merged.update({str(key): str(value) for key, value in extra.items()})
    return merged


def run_captured(
    command: Sequence[str],
    *,
    cwd: Path,
    input_bytes: bytes = b"",
    timeout_s: float = 300.0,
    env: Mapping[str, str] | None = None,
) -> tuple[float, int, bytes, bytes]:
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            list(command),
            cwd=str(cwd),
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_s,
            env=None if env is None else dict(env),
        )
    except subprocess.TimeoutExpired as exc:
        raise ReasonError("timed_out") from exc
    elapsed = max(time.perf_counter() - start, 1e-9)
    return elapsed, int(proc.returncode), bytes(proc.stdout), bytes(proc.stderr)


def compile_command(compiler: Path, source: Path, artifact: Path, *, styio: bool) -> list[str]:
    if styio:
        return [str(compiler), "build", str(source), "-o", str(artifact)]
    return [str(compiler), *CPP_FLAGS, str(source), "-o", str(artifact)]


def public_version(text: str) -> str:
    match = re.search(r"(?<![0-9])([0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-+][A-Za-z0-9.-]+)?)", text)
    if not match or not PUBLIC_VERSION.fullmatch(match.group(1)):
        return "unknown"
    return match.group(1)


def classify_paired_status(
    *,
    numerator: Sequence[float] | None,
    denominator: Sequence[float] | None,
    confidence_interval: Mapping[str, Any] | None,
    retained_floor_met: bool,
    expected_repetitions: int,
    comparable: bool = True,
    max_cv_pct: float = MAX_CV_PCT,
) -> tuple[str, str]:
    """Return (status, reason) for a candidate/baseline cost ratio."""

    if not comparable:
        return "inconclusive", "incomparable"
    if numerator is None or denominator is None:
        return "unavailable", "samples_missing"
    if len(numerator) != expected_repetitions or len(denominator) != expected_repetitions:
        return "inconclusive", "insufficient_pairs"
    if not retained_floor_met:
        return "inconclusive", "minimum_sample_time"
    if sample_cv_pct(numerator) > max_cv_pct or sample_cv_pct(denominator) > max_cv_pct:
        return "inconclusive", "noise_cv"
    if not isinstance(confidence_interval, Mapping):
        return "inconclusive", "interval_missing"
    lower = confidence_interval.get("lower")
    upper = confidence_interval.get("upper")
    if (
        isinstance(lower, bool)
        or isinstance(upper, bool)
        or not isinstance(lower, (int, float))
        or not isinstance(upper, (int, float))
        or not math.isfinite(float(lower))
        or not math.isfinite(float(upper))
    ):
        return "inconclusive", "interval_missing"
    if float(upper) < 1.0:
        return "improved", "interval_below_one"
    if float(lower) > 1.0:
        return "regressed", "interval_above_one"
    return "no_detected_change", "interval_contains_one"


class SideSpec:
    """One measured participant: compiler, source, work directory, and env overlay."""

    def __init__(
        self,
        name: str,
        compiler: Path,
        source: Path,
        cwd: Path,
        is_styio: bool,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self.compiler = compiler
        self.source = source
        self.cwd = cwd
        self.is_styio = is_styio
        self.env = env

    def compile_argv(self, artifact: Path) -> list[str]:
        return compile_command(self.compiler, self.source, artifact, styio=self.is_styio)

    def child_env(self) -> dict[str, str] | None:
        return merge_child_env(self.env)


def incomplete_cell(
    cell: Mapping[str, Any],
    *,
    left_name: str,
    right_name: str,
    correctness: Mapping[str, bool],
    warmups: int,
    repetitions: int,
    reason: str,
    measurement_stage: str = "preflight",
) -> dict[str, Any]:
    return {
        "id": cell["id"],
        "family": cell["family"],
        "scale": cell["scale"],
        "route": cell["route"],
        "required": bool(cell.get("required")),
        "work_units": cell.get("work_units"),
        "algorithm_id": cell.get("algorithm_id"),
        "focus_owner": cell.get("focus_owner"),
        "status": "incomplete",
        "correctness": dict(correctness),
        "repetitions": repetitions,
        "warmups": warmups,
        "source_digests": cell.get("source_digest"),
        "input_digest": cell.get("input_digest"),
        "expected_output_digest": cell.get("expected_output_digest"),
        "reason_codes": [reason],
        "side_names": [left_name, right_name],
        "measurement_stage": measurement_stage,
    }


def retained_floor_from_raw(raw_by_name: Mapping[str, Sequence[float]]) -> bool:
    """Recompute the retained-batch duration floor from raw batch totals."""

    values = list(raw_by_name.values())
    if not values or any(not side for side in values):
        return False
    return all(median(side) >= MIN_SAMPLE_DURATION_S for side in values)


def measure_paired_cell(
    cell: Mapping[str, Any],
    *,
    left: SideSpec,
    right: SideSpec,
    input_bytes: bytes,
    expected_output: bytes,
    expected_digest: str,
    warmups: int,
    repetitions: int,
    timeout_s: float,
    runner_version: str,
    ratio_numerator: str | None = None,
    ratio_denominator: str | None = None,
    collect_artifact_bytes: bool = False,
    event_sink: list[dict[str, str]] | None = None,
    include_side_names: bool = False,
) -> dict[str, Any]:
    """Run one catalog cell as an interleaved pair with observer-free timing.

    RSS is collected only in a later equivalent replay.  Callers map side names
    (`styio`/`cpp` or `baseline`/`candidate`) through ``SideSpec``.
    """

    sides = (left, right)
    names = (left.name, right.name)
    numerator_name = ratio_numerator or left.name
    denominator_name = ratio_denominator or right.name
    if {numerator_name, denominator_name} != set(names):
        raise ReasonError("invalid_ratio_sides")
    implementation_samples: dict[str, list[float]] = {left.name: [], right.name: []}
    raw_batch_samples: dict[str, list[float]] = {left.name: [], right.name: []}
    rss_samples: dict[str, list[float]] = {left.name: [], right.name: []}
    correctness = {left.name: False, right.name: False}
    identity = str(cell["id"])
    route = str(cell["route"])
    family = str(cell["family"])
    helper = load_rss_helper()
    helper_script = ROOT / "native-cpp" / "process_tree_rss.py"

    def emit(phase: str, side: str, action: str) -> None:
        if event_sink is not None:
            event_sink.append({"phase": phase, "side": side, "action": action, "route": route})

    with tempfile.TemporaryDirectory(prefix="paired-measure-cell-") as temporary:
        temp = Path(temporary)
        artifacts: dict[str, Path] = {}
        work: dict[str, Path] = {}
        inputs: dict[str, Path] = {}
        artifact_bytes: dict[str, int] = {}
        for side in sides:
            area = temp / side.name
            area.mkdir(parents=True, exist_ok=True)
            work[side.name] = area
            input_path = area / "input.bin"
            input_path.write_bytes(input_bytes)
            inputs[side.name] = input_path

        for side in sides:
            artifact = work[side.name] / "preflight.bin"
            emit("preflight", side.name, "build")
            _, code, _, _ = run_captured(
                side.compile_argv(artifact),
                cwd=side.cwd,
                timeout_s=timeout_s,
                env=side.child_env(),
            )
            if code != 0 or not artifact.is_file():
                return incomplete_cell(
                    cell,
                    left_name=left.name,
                    right_name=right.name,
                    correctness=correctness,
                    warmups=warmups,
                    repetitions=repetitions,
                    reason="compile_failed",
                    measurement_stage="preflight",
                )
            artifacts[side.name] = artifact
            if collect_artifact_bytes:
                artifact_bytes[side.name] = int(artifact.stat().st_size)
            emit("preflight", side.name, "run")
            _, run_code, run_output, _ = run_captured(
                [str(artifact)],
                cwd=side.cwd,
                input_bytes=input_bytes,
                timeout_s=timeout_s,
                env=side.child_env(),
            )
            correctness[side.name] = run_code == 0 and sha256(run_output) == expected_digest
            if not correctness[side.name]:
                return incomplete_cell(
                    cell,
                    left_name=left.name,
                    right_name=right.name,
                    correctness=correctness,
                    warmups=warmups,
                    repetitions=repetitions,
                    reason="correctness_output_digest",
                    measurement_stage="preflight",
                )

        def timed_batch(side: SideSpec, label: str, index: int, batch_count: int) -> float:
            area = work[side.name]
            output = area / f"timed-{label}-{index}.out"
            artifact_area = area / f"timed-{label}-{index}-artifacts"
            if route == "native-run":
                command = [
                    sys.executable,
                    str(helper_script),
                    "--batch-exec",
                    str(artifacts[side.name]),
                    "--input",
                    str(inputs[side.name]),
                    "--output",
                    str(output),
                    "--count",
                    str(batch_count),
                ]
                action = "batch-exec"
            else:
                command = [
                    sys.executable,
                    str(helper_script),
                    "--batch-build-run" if route == "compile-and-run" else "--batch-build",
                    "--build-argv-json",
                    json.dumps(side.compile_argv(Path("{artifact}")), separators=(",", ":"), ensure_ascii=True),
                    "--artifact-dir",
                    str(artifact_area),
                    "--artifact-token",
                    "{artifact}",
                    "--count",
                    str(batch_count),
                ]
                if route == "compile-and-run":
                    command.extend(("--input", str(inputs[side.name]), "--output", str(output)))
                action = "batch-build-run" if route == "compile-and-run" else "batch-build"
            emit("timing" if label in {"retained", "warmup", "calibration"} else label, side.name, action)
            try:
                try:
                    sample = helper.run_process(
                        command,
                        cwd=side.cwd,
                        timeout_s=timeout_s,
                        env=side.child_env(),
                        sample_process_tree=False,
                    )
                except helper.ProcessTimedOut as exc:
                    raise ReasonError("timed_out") from exc
                if sample.returncode != 0:
                    raise ReasonError("measured_process_failed")
                if route != "native-build" and not batched_output_matches(
                    output,
                    expected_output,
                    batch_count,
                    repeated=route == "native-run",
                ):
                    raise ReasonError("correctness_output_digest")
                return max(float(sample.elapsed_s), 1e-9)
            finally:
                output.unlink(missing_ok=True)
                shutil.rmtree(artifact_area, ignore_errors=True)

        calibration_attempts: list[dict[str, Any]] = []
        batch_count = 1
        by_name = {left.name: left, right.name: right}
        unpaired_time: dict[str, list[float]] = {left.name: [], right.name: []}
        unpaired_raw_time: dict[str, list[float]] = {left.name: [], right.name: []}
        unpaired_rss: dict[str, list[float]] = {left.name: [], right.name: []}
        failure_reason: str | None = None
        failure_stage = "calibration"
        warmup_orders: tuple[tuple[str, str], ...] = ()
        retained_orders: tuple[tuple[str, str], ...] = ()
        rss_orders: tuple[tuple[str, str], ...] = ()

        def rss_replay(side: SideSpec, index: int) -> float:
            artifact = artifacts[side.name]
            area = work[side.name]
            output = area / f"rss-{index}.out"
            fresh = area / f"rss-{index}.bin"
            try:
                if route == "native-run":
                    emit("rss", side.name, "run")
                    try:
                        sample = helper.run_process(
                            [str(artifact)],
                            cwd=side.cwd,
                            stdin_path=inputs[side.name],
                            stdout_path=output,
                            timeout_s=timeout_s,
                            env=side.child_env(),
                            sample_process_tree=True,
                        )
                    except helper.ProcessTimedOut as exc:
                        raise ReasonError("timed_out") from exc
                    if sample.returncode != 0 or not batched_output_matches(output, expected_output, 1, repeated=False):
                        raise ReasonError("correctness_output_digest")
                    return max(float(sample.peak_rss_kib), 0.001)
                emit("rss", side.name, "build")
                try:
                    build_sample = helper.run_process(
                        side.compile_argv(fresh),
                        cwd=side.cwd,
                        timeout_s=timeout_s,
                        env=side.child_env(),
                        sample_process_tree=True,
                    )
                except helper.ProcessTimedOut as exc:
                    raise ReasonError("timed_out") from exc
                if build_sample.returncode != 0 or not fresh.is_file():
                    raise ReasonError("compile_failed")
                peak = max(float(build_sample.peak_rss_kib), 0.001)
                if route == "compile-and-run":
                    emit("rss", side.name, "run")
                    try:
                        run_sample = helper.run_process(
                            [str(fresh)],
                            cwd=side.cwd,
                            stdin_path=inputs[side.name],
                            stdout_path=output,
                            timeout_s=timeout_s,
                            env=side.child_env(),
                            sample_process_tree=True,
                        )
                    except helper.ProcessTimedOut as exc:
                        raise ReasonError("timed_out") from exc
                    if run_sample.returncode != 0 or not batched_output_matches(output, expected_output, 1, repeated=False):
                        raise ReasonError("correctness_output_digest")
                    peak = max(peak, float(run_sample.peak_rss_kib))
                return max(peak, 0.001)
            finally:
                output.unlink(missing_ok=True)
                fresh.unlink(missing_ok=True)

        try:
            while True:
                elapsed_by_implementation: dict[str, float] = {}
                for name in names:
                    elapsed_by_implementation[name] = timed_batch(
                        by_name[name], "calibration", len(calibration_attempts), batch_count
                    )
                calibration_attempts.append({"batch_count": batch_count, "elapsed_s": elapsed_by_implementation})
                fastest_total = min(elapsed_by_implementation.values())
                if fastest_total >= CALIBRATION_TARGET_DURATION_S or batch_count >= MAX_BATCH_COUNT:
                    break
                if batch_count == 1:
                    next_count = calibrate_batch_count(tuple(elapsed_by_implementation.values()))
                else:
                    next_count = math.ceil(batch_count * CALIBRATION_TARGET_DURATION_S / fastest_total)
                batch_count = min(MAX_BATCH_COUNT, max(batch_count + 1, next_count))

            warmup_orders = interleaved_pair_orders(
                identity, warmups, "warmup", left=left.name, right=right.name, runner_version=runner_version
            )
            retained_orders = interleaved_pair_orders(
                identity, repetitions, "retained", left=left.name, right=right.name, runner_version=runner_version
            )
            rss_orders = interleaved_pair_orders(
                identity, repetitions, "rss", left=left.name, right=right.name, runner_version=runner_version
            )
            failure_stage = "warmup"
            for warmup_index, order in enumerate(warmup_orders):
                for name in order:
                    timed_batch(by_name[name], "warmup", warmup_index, batch_count)
            failure_stage = "retained"
            for index, order in enumerate(retained_orders):
                elapsed_by_implementation = {}
                try:
                    for name in order:
                        elapsed_by_implementation[name] = timed_batch(by_name[name], "retained", index, batch_count)
                except ReasonError:
                    for name, elapsed in elapsed_by_implementation.items():
                        unpaired_raw_time[name].append(elapsed)
                        unpaired_time[name].append(normalize_batched_elapsed(elapsed, batch_count))
                    raise
                for name in names:
                    elapsed = elapsed_by_implementation[name]
                    raw_batch_samples[name].append(elapsed)
                    implementation_samples[name].append(normalize_batched_elapsed(elapsed, batch_count))
            failure_stage = "rss"
            for index, order in enumerate(rss_orders):
                by_implementation: dict[str, float] = {}
                try:
                    for name in order:
                        by_implementation[name] = rss_replay(by_name[name], index)
                except ReasonError:
                    for name, value in by_implementation.items():
                        unpaired_rss[name].append(value)
                    raise
                for name in names:
                    rss_samples[name].append(by_implementation[name])
            failure_stage = "complete"
        except ReasonError as exc:
            failure_reason = exc.reason_code

        time_complete = all(len(implementation_samples[name]) == repetitions for name in names)
        rss_complete = all(len(rss_samples[name]) == repetitions for name in names)
        retained_floor_met = retained_floor_from_raw(raw_batch_samples)
        time_ratio = None
        memory_ratio = None
        throughput_ratio = None
        if time_complete:
            time_ratio = ratio_dimension(
                implementation_samples[numerator_name],
                implementation_samples[denominator_name],
                left_name=numerator_name,
                right_name=denominator_name,
            )
            throughput_ratio = ratio_dimension(
                implementation_samples[numerator_name],
                implementation_samples[denominator_name],
                reciprocal=True,
                left_name=numerator_name,
                right_name=denominator_name,
            )
        if rss_complete:
            memory_ratio = ratio_dimension(
                rss_samples[numerator_name],
                rss_samples[denominator_name],
                left_name=numerator_name,
                right_name=denominator_name,
            )
        collection_ok = failure_reason is None and time_complete and rss_complete and retained_floor_met
        reason_codes: list[str] = []
        if failure_reason:
            reason_codes.append(failure_reason)
        elif not retained_floor_met and time_complete:
            reason_codes.append("minimum_sample_time")
        elif not time_complete or not rss_complete:
            reason_codes.append("samples_incomplete")
        completed_orders = retained_orders[:len(implementation_samples[left.name])]
        schedule_codes = ["AB" if order == (left.name, right.name) else "BA" for order in completed_orders]
        record: dict[str, Any] = {
            "id": cell["id"],
            "family": family,
            "scale": cell["scale"],
            "route": route,
            "required": bool(cell.get("required")),
            "work_units": cell["work_units"],
            "algorithm_id": cell.get("algorithm_id"),
            "focus_owner": cell.get("focus_owner"),
            "status": "pass" if collection_ok else "incomplete",
            "correctness": correctness,
            "repetitions": repetitions,
            "warmups": warmups,
            "source_digests": cell.get("source_digest"),
            "input_digest": cell.get("input_digest"),
            "expected_output_digest": expected_digest,
            "time_samples_s": implementation_samples,
            "raw_batch_time_samples_s": raw_batch_samples,
            "peak_rss_samples_kib": rss_samples,
            "batch": {
                "count": batch_count,
                "minimum_sample_time_s": MIN_SAMPLE_DURATION_S,
                "target_sample_time_s": CALIBRATION_TARGET_DURATION_S,
                "maximum_count": MAX_BATCH_COUNT,
                "calibration_attempts": calibration_attempts,
                "retained_floor_met": retained_floor_met,
                "equal_work": True,
            },
            "reason_codes": reason_codes,
            "measurement_stage": failure_stage,
        }
        if time_ratio is not None:
            record["time"] = time_ratio
        if memory_ratio is not None:
            record["peak_rss"] = memory_ratio
        if throughput_ratio is not None:
            record["throughput"] = throughput_ratio
        if any(unpaired_time[name] for name in names):
            record["unpaired_time_samples_s"] = unpaired_time
            record["unpaired_raw_batch_time_samples_s"] = unpaired_raw_time
        if any(unpaired_rss[name] for name in names):
            record["unpaired_rss_samples_kib"] = unpaired_rss
        if retained_orders:
            record["sample_schedule"] = {
                "strategy": "deterministic-random-interleaving-v1",
                "ab_count": schedule_codes.count("AB"),
                "ba_count": schedule_codes.count("BA"),
                "schedule_digest": sha256("".join(schedule_codes).encode("ascii")),
            }
        if include_side_names:
            record["side_names"] = [left.name, right.name]
            if retained_orders:
                record["pair_orders"] = [f"{first},{second}" for first, second in completed_orders]
        if collect_artifact_bytes:
            if set(artifact_bytes) == set(names):
                baseline_bytes = artifact_bytes.get("baseline", artifact_bytes[left.name])
                candidate_bytes = artifact_bytes.get("candidate", artifact_bytes[right.name])
                ratio = None
                if baseline_bytes > 0:
                    ratio = candidate_bytes / baseline_bytes
                record["artifact_bytes"] = {
                    left.name: artifact_bytes[left.name],
                    right.name: artifact_bytes[right.name],
                    "delta": artifact_bytes[right.name] - artifact_bytes[left.name] if left.name != right.name else 0,
                    "ratio": ratio,
                    "origin": "untimed-native-artifact",
                    "comparison_status": "direct_observation",
                }
            else:
                record["artifact_bytes"] = {
                    "comparison_status": "unavailable",
                    "reason": "artifact_size_missing",
                }
        return record

    raise ReasonError("measurement_workspace")


def recompute_ratio_from_samples(
    numerator: Sequence[float],
    denominator: Sequence[float],
    *,
    numerator_name: str,
    denominator_name: str,
) -> dict[str, Any]:
    return ratio_dimension(
        numerator,
        denominator,
        left_name=numerator_name,
        right_name=denominator_name,
    )
