#!/usr/bin/env python3
"""Smoke test for styio_core_bench JSON output.

This test intentionally validates smoke-level contract only:
 - styio_core_bench starts successfully
 - exits with code 0
 - writes valid JSON using the v1 schema
 - emits route_cache and ir_alloc samples
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=False)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--measured", type=int, default=1)
    return parser.parse_args()


def expect_bool(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def parse_and_validate(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"benchmark output file missing: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    expect_bool(isinstance(payload, dict), "benchmark JSON payload must be an object")
    expect_bool(payload.get("schema") == "styio.benchmark.v1",
                f"unexpected schema: {payload.get('schema')}")
    return payload


def require_sample_group(payload: dict) -> None:
    samples = payload.get("samples")
    expect_bool(isinstance(samples, list), "samples must be a list")
    expect_bool(len(samples) > 0, "benchmark produced no samples")

    route_cache_samples = [s for s in samples if s.get("phase") == "route_cache"]
    ir_alloc_samples = [s for s in samples if s.get("phase") == "ir_alloc"]
    expect_bool(len(route_cache_samples) > 0, "route_cache sample is missing")
    expect_bool(len(ir_alloc_samples) > 0, "ir_alloc sample is missing")

    for s in route_cache_samples:
        expect_bool("route_cache_scan_count" in s, "route_cache sample missing scan count")
        expect_bool("route_cache_miss_count" in s, "route_cache sample missing miss count")

    for s in ir_alloc_samples:
        expect_bool("ir_bytes_allocated" in s, "ir_alloc sample missing bytes_allocated")
        expect_bool("ir_node_count" in s, "ir_alloc sample missing node_count")


def main() -> int:
    args = parse_args()
    if args.warmup < 1 or args.measured < 1:
        raise SystemExit("--warmup and --measured must be positive")
    output = args.output or Path(tempfile.mkdtemp(prefix="styio-core-bench-smoke-")) / "core-bench.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["STYIO_BENCH_WARMUP"] = str(args.warmup)
    env["STYIO_BENCH_MEASURED"] = str(args.measured)

    command = [str(args.binary), "--output", str(output)]
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        raise SystemExit(
            f"styio_core_bench failed with code {completed.returncode}:\n{completed.stderr}"
        )

    payload = parse_and_validate(output)
    require_sample_group(payload)
    print(f"smoke validated: {len(payload['samples'])} samples in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
