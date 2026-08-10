#!/usr/bin/env python3
"""Run Styio's external core performance corpus and emit JSON evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path


SCHEMA = "styio.core_benchmark_result.v1"


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parents[1]
    default_styio = os.environ.get("STYIO_COMPILER_EXE")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=script_dir / "manifest.json")
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    parser.add_argument("--styio", type=Path, default=Path(default_styio) if default_styio else None)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def load_manifest(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("schema") != "styio.core_benchmark_manifest.v1":
        raise SystemExit(f"unsupported core benchmark manifest schema: {manifest.get('schema')}")
    workloads = manifest.get("workloads")
    if not isinstance(workloads, list) or not workloads:
        raise SystemExit("core benchmark manifest must contain at least one workload")
    return manifest


def run_workload(styio: Path, source: Path, stdin_text: str) -> dict:
    started = time.perf_counter_ns()
    completed = subprocess.run(
        [str(styio), "--file", str(source)],
        input=stdin_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    ended = time.perf_counter_ns()
    return {
        "duration_ns": ended - started,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        "stderr": completed.stderr,
    }


def summarize(samples: list[dict]) -> dict:
    durations = [sample["duration_ns"] for sample in samples]
    return {
        "samples": samples,
        "sample_count": len(samples),
        "min_ns": min(durations),
        "median_ns": int(statistics.median(durations)),
        "max_ns": max(durations),
    }


def main() -> int:
    args = parse_args()
    if args.iterations <= 0:
        raise SystemExit("--iterations must be positive")
    if args.styio is None or not args.styio.is_file():
        raise SystemExit(f"styio executable not found: {args.styio}")

    repo_root = args.repo_root.resolve()
    manifest_path = args.manifest.resolve()
    manifest = load_manifest(manifest_path)
    try:
        manifest_id = manifest_path.relative_to(repo_root).as_posix()
    except ValueError:
        raise SystemExit("core benchmark manifest must be inside --repo-root")
    results = {
        "schema": SCHEMA,
        "manifest": manifest_id,
        "repo_root": "styio-benchmark",
        "styio": "styio",
        "iterations": args.iterations,
        "workloads": [],
    }

    for workload in manifest["workloads"]:
        name = workload.get("name")
        source = (repo_root / workload.get("source", "")).resolve()
        if not name:
            raise SystemExit("core benchmark workload is missing name")
        if repo_root not in source.parents:
            raise SystemExit(f"core benchmark source escapes repository root for {name}")
        if not source.is_file():
            raise SystemExit(f"core benchmark source does not exist for {name}: {source}")
        stdin_text = workload.get("stdin", "")
        expected_stdout = workload.get("expected_stdout")
        samples = []
        for _ in range(args.iterations):
            sample = run_workload(args.styio.resolve(), source, stdin_text)
            if sample["exit_code"] != 0:
                sys.stderr.write(sample["stderr"])
                raise SystemExit(f"{name} failed with exit code {sample['exit_code']}")
            if expected_stdout is not None and sample["stdout"] != expected_stdout:
                raise SystemExit(
                    f"{name} stdout mismatch: expected {expected_stdout!r}, got {sample['stdout']!r}"
                )
            samples.append(sample)
        results["workloads"].append({
            "name": name,
            "case": workload.get("case", name),
            "source": workload["source"],
            "input_sha256": hashlib.sha256(stdin_text.encode("utf-8")).hexdigest(),
            "expected_stdout_sha256": hashlib.sha256(
                (expected_stdout or "").encode("utf-8")
            ).hexdigest(),
            "tags": workload.get("tags", []),
            **summarize(samples),
        })

    rendered = json.dumps(results, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
