#!/usr/bin/env python3
"""Read-only host environment probe for benchmark reproducibility.

Reports machine-level timing stability factors without leaking identity:
no hostname, no user, no absolute paths, no environment variables, and no
network access. Timestamps are UTC.

Exit codes:
  0  advisory report (warnings are informational)
  1  --strict and at least one warning
  2  malformed arguments or internal failure
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Callable

VERSION = "1"


def probe_platform_supported() -> tuple[str, str, str]:
    supported = sys.platform in ("linux", "darwin")
    observed = sys.platform
    if supported:
        return "pass", observed, "Controlled local timing runs are supported on this platform."
    return "warn", observed, "Timing stability is only verified on Linux and macOS; treat results with caution."


def probe_logical_cpus() -> tuple[str, str, str]:
    count = os.cpu_count()
    if count is None:
        return "unavailable", "unknown", "Cannot determine CPU count; run on a controlled machine."
    if count >= 4:
        return "pass", str(count), "At least 4 logical CPUs are available for benchmark runs."
    return "warn", str(count), "Fewer than 4 logical CPUs can amplify scheduler noise; consider a larger machine."


def probe_cpu_affinity() -> tuple[str, str, str]:
    try:
        affinity = os.sched_getaffinity(0)
    except AttributeError:
        return "unavailable", "not supported on this platform", "Affinity cannot be inspected here."
    except OSError:
        return "unavailable", "cannot read affinity", "Affinity cannot be inspected here."
    total = os.cpu_count() or 0
    if total and len(affinity) == total:
        return "pass", str(len(affinity)), "All logical CPUs are available to the benchmark process."
    return "warn", str(len(affinity)), "The process is pinned to a subset of CPUs; timing may be biased."


def probe_background_load() -> tuple[str, str, str]:
    try:
        load = os.getloadavg()[0]
    except (AttributeError, OSError):
        return "unavailable", "cannot read load average", "Background load cannot be inspected here."
    total = os.cpu_count() or 0
    if total and load < total * 0.9:
        return "pass", f"{load:.2f}", "One-minute load average is below the CPU count."
    return "warn", f"{load:.2f}", "High background load degrades timing stability; stop other work before measuring."


def _read_text_limited(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(4096).strip()
    except OSError:
        return None


def probe_cpu_governor() -> tuple[str, str, str]:
    value = _read_text_limited("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    if value is None:
        return "unavailable", "governor not exposed", "CPU frequency governor cannot be inspected here."
    if value == "performance":
        return "pass", value, "Performance governor keeps frequency stable for timing runs."
    return "warn", value, "Non-performance governor varies frequency; pin it before wall-clock measurements."


def probe_turbo_boost() -> tuple[str, str, str]:
    no_turbo = _read_text_limited("/sys/devices/system/cpu/intel_pstate/no_turbo")
    if no_turbo is not None:
        if no_turbo == "1":
            return "pass", "disabled", "Turbo boost is disabled; timings are more stable."
        return "warn", "enabled", "Turbo boost is enabled; frequencies vary under load."
    boost = _read_text_limited("/sys/devices/system/cpu/cpufreq/boost")
    if boost is not None:
        if boost == "0":
            return "pass", "disabled", "Turbo boost is disabled; timings are more stable."
        return "warn", "enabled", "Turbo boost is enabled; frequencies vary under load."
    return "unavailable", "not exposed", "Turbo boost state cannot be inspected here."


def probe_power_source() -> tuple[str, str, str]:
    if sys.platform == "darwin":
        try:
            proc = subprocess.run(
                ["pmset", "-g", "batt"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unavailable", "cannot read battery state", "Power source cannot be inspected here."
        output = (proc.stdout or "").lower()
        if "ac power" in output or "charging" in output:
            return "pass", "AC power", "Running on AC power; frequency scaling is stable."
        if "battery power" in output:
            return "warn", "battery", "Running on battery; CPU frequency may be throttled."
        return "unavailable", "unknown", "Power source cannot be determined here."
    mains_found = False
    for name in sorted(os.listdir("/sys/class/power_supply") if os.path.isdir("/sys/class/power_supply") else []):
        kind = _read_text_limited(f"/sys/class/power_supply/{name}/type")
        if kind == "Mains":
            mains_found = True
            online = _read_text_limited(f"/sys/class/power_supply/{name}/online")
            if online == "1":
                return "pass", "AC power", "Running on AC power; frequency scaling is stable."
    if mains_found:
        return "warn", "offline", "The system is not on AC power; frequency may be throttled."
    return "unavailable", "no power supply exposed", "Power source cannot be inspected here."


CHECK_PROBES: dict[str, dict[str, Any]] = {
    "platform_supported": {
        "label": "Supported platform",
        "probe": probe_platform_supported,
        "recommendation": "Run controlled timing comparisons on Linux or macOS.",
    },
    "logical_cpus": {
        "label": "Logical CPUs",
        "probe": probe_logical_cpus,
        "recommendation": "Use a machine with at least 4 logical CPUs.",
    },
    "cpu_affinity": {
        "label": "CPU affinity",
        "probe": probe_cpu_affinity,
        "recommendation": "Let the benchmark use every logical CPU.",
    },
    "background_load": {
        "label": "Background load",
        "probe": probe_background_load,
        "recommendation": "Stop other workloads before wall-clock measurement.",
    },
    "cpu_governor": {
        "label": "CPU governor",
        "probe": probe_cpu_governor,
        "recommendation": "Pin the governor to performance for stable timings.",
    },
    "turbo_boost": {
        "label": "Turbo boost",
        "probe": probe_turbo_boost,
        "recommendation": "Disable turbo boost for the most reproducible wall-clock numbers.",
    },
    "power_source": {
        "label": "Power source",
        "probe": probe_power_source,
        "recommendation": "Measure on AC power, never on battery.",
    },
}


def run_checks(probes: dict[str, dict[str, Any]] | None = None) -> list[dict[str, str]]:
    active = CHECK_PROBES if probes is None else probes
    checks: list[dict[str, str]] = []
    for probe_id, spec in active.items():
        probe: Callable[[], tuple[str, str, str]] = spec["probe"]
        try:
            status, observed, reason = probe()
        except Exception:  # noqa: BLE001 - best-effort probes must degrade, never crash.
            status, observed, reason = "unavailable", "probe failed", "The probe could not complete; treat as unknown."
        checks.append(
            {
                "id": probe_id,
                "label": spec.get("label", probe_id),
                "status": status,
                "observed": observed,
                "recommendation": reason,
            }
        )
    return checks


def build_payload(checks: list[dict[str, str]]) -> dict[str, Any]:
    summary = {"pass": 0, "warn": 0, "unavailable": 0}
    for check in checks:
        summary[check["status"]] += 1
    return {
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": summary,
        "checks": checks,
    }


def render_text(payload: dict[str, Any]) -> str:
    lines = [
        "Benchmark environment check",
        f"generated_at (UTC): {payload['generated_at']}",
        f"summary: {payload['summary']['pass']} pass, {payload['summary']['warn']} warn, {payload['summary']['unavailable']} unavailable",
        "",
    ]
    for check in payload["checks"]:
        lines.append(f"- [{check['status']}] {check['label']}: {check['observed']} -- {check['recommendation']}")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe local timing-stability factors before a benchmark run (read-only, privacy-safe)."
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--strict", action="store_true", help="exit 1 when any check warns")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    checks = run_checks()
    payload = build_payload(checks)
    if args.format == "json":
        print(json.dumps(payload, indent=2))
    else:
        print(render_text(payload), end="")
    if args.strict and payload["summary"]["warn"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
