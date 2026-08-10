#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FAST_TEST_MODULES = [
    "async-runtime/test_async_runtime_statistics.py",
    "native-cpp/test_native_cpp_bench.py",
    "tools/test_benchmark_tools.py",
    "tests/test_benchmark_capability_contract.py",
]

CAPABILITY_FIXTURES = [
    "async-baseline.json",
    "async-candidate.json",
    "native-baseline.json",
    "native-candidate.json",
]

STATISTIC_FIELDS = ("sample_count", "median", "ci95_low", "ci95_high", "cv", "quality")
COMPARISON_FIELDS = ("baseline_identity", "u", "p_value", "alpha", "significant", "direction", "status")


def fail(message: str) -> None:
    print(f"benchmark-golden-gate: {message}", file=sys.stderr)
    raise SystemExit(1)


def sanitize_diagnostic(output: str | bytes) -> str:
    if isinstance(output, bytes):
        text = output.decode(errors="replace")
    else:
        text = output
    replacements: set[tuple[str, str]] = set()
    for path, marker in (
        (ROOT, "<repo>"),
        (Path.home(), "<home>"),
        (Path(tempfile.gettempdir()), "<tmp>"),
        (Path(sys.prefix), "<python>"),
    ):
        for candidate in (path, path.resolve()):
            raw = str(candidate)
            if len(raw) > 1:
                replacements.add((raw, marker))
    for raw, marker in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        text = text.replace(raw, marker)
    return text


def run(command: list[str], *, label: str, timeout: int = 300, env_extra: dict[str, str] | None = None) -> None:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    with tempfile.TemporaryDirectory(prefix="styio-benchmark-pycache-") as pycache:
        env["PYTHONPYCACHEPREFIX"] = pycache
        print(f"benchmark-golden-gate: running {label}", flush=True)
        try:
            proc = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            output = sanitize_diagnostic(exc.stdout or "")
            if output:
                print(output, file=sys.stderr)
            fail(f"{label} timed out after {timeout}s")
    if proc.returncode != 0:
        if proc.stdout:
            print(sanitize_diagnostic(proc.stdout), file=sys.stderr)
        fail(f"{label} failed")
    if proc.stdout:
        print(sanitize_diagnostic(proc.stdout), end="")


def require_nonempty(path: Path) -> None:
    if not path.is_file():
        fail(f"missing required artifact {path.relative_to(ROOT)}")
    if path.stat().st_size == 0:
        fail(f"empty required artifact {path.relative_to(ROOT)}")


def check_json(path: Path) -> object:
    require_nonempty(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{path.relative_to(ROOT)} is not valid JSON: {exc}")


def check_report_dir(path: Path) -> None:
    require_nonempty(path / "benchmarks.csv")
    require_nonempty(path / "summary.md")
    results = check_json(path / "results.json")
    if not isinstance(results, dict):
        fail(f"{(path / 'results.json').relative_to(ROOT)} must contain a JSON object")
    if (path / "metadata.json").exists():
        metadata = check_json(path / "metadata.json")
        if not isinstance(metadata, dict):
            fail(f"{(path / 'metadata.json').relative_to(ROOT)} must contain a JSON object")
    elif not isinstance(results.get("metadata"), dict):
        require_nonempty(path / "metadata.tsv")
    if (path / "sections.tsv").exists():
        require_nonempty(path / "sections.tsv")


def check_report_fixtures() -> None:
    report_dirs = [
        path
        for root in (ROOT / "reports", ROOT / "async-runtime" / "reports")
        if root.exists()
        for path in root.iterdir()
        if path.is_dir()
    ]
    if not report_dirs:
        fail("no benchmark report fixtures found")
    for path in sorted(report_dirs):
        check_report_dir(path)


def check_capability_fixtures() -> None:
    fixture_dir = ROOT / "tests" / "fixtures" / "benchmark-capabilities"
    if not fixture_dir.is_dir():
        fail(f"missing capability fixture directory {fixture_dir.relative_to(ROOT)}")
    for name in CAPABILITY_FIXTURES:
        path = fixture_dir / name
        payload = check_json(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            fail(f"{path.relative_to(ROOT)} must contain a JSON object with a results list")
        if not payload["results"]:
            fail(f"{path.relative_to(ROOT)} contains no results")
        for result in payload["results"]:
            if not isinstance(result, dict):
                fail(f"{path.relative_to(ROOT)} contains a non-object result")
            statistics = result.get("statistics")
            if statistics is not None:
                for stat in _iter_statistics(statistics):
                    for field in STATISTIC_FIELDS:
                        if field not in stat:
                            fail(f"{path.relative_to(ROOT)} statistic object missing {field}")
            comparisons = result.get("comparisons")
            if comparisons is not None:
                for comparison in _iter_comparisons(comparisons):
                    for field in COMPARISON_FIELDS:
                        if field not in comparison:
                            fail(f"{path.relative_to(ROOT)} comparison object missing {field}")
            comparison = result.get("comparison")
            if comparison is not None:
                for field in COMPARISON_FIELDS:
                    if field not in comparison:
                        fail(f"{path.relative_to(ROOT)} comparison object missing {field}")


def _iter_statistics(statistics: object):
    if isinstance(statistics, dict):
        for value in statistics.values():
            if isinstance(value, dict):
                if "sample_count" in value:
                    yield value
                else:
                    yield from _iter_statistics(value)
            else:
                yield value


def _iter_comparisons(comparisons: object):
    if isinstance(comparisons, dict):
        yield from comparisons.values()
    elif isinstance(comparisons, list):
        yield from comparisons


def check_dependencies() -> None:
    pyproject = ROOT / "pyproject.toml"
    require_nonempty(pyproject)
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    if not match:
        fail("pyproject.toml has no dependencies list")
    dependencies = [item.strip() for item in match.group(1).split(",") if item.strip()]
    if dependencies:
        fail(f"production dependencies must stay empty, found: {', '.join(dependencies)}")


def check_workflow() -> None:
    workflow = ROOT / ".github" / "workflows" / "tests.yml"
    require_nonempty(workflow)
    text = workflow.read_text(encoding="utf-8")
    for job in ("test-smoke", "test-golden-standard"):
        if f"  {job}:" not in text:
            fail(f"workflow is missing job {job}")
    if "needs: [test-smoke]" not in text:
        fail("workflow test-golden-standard must depend on test-smoke")
    forbidden = ["run-async-bench.py", "run-native-cpp-bench.py", "--case baseline", "--case stress"]
    for pattern in forbidden:
        if pattern in text:
            fail(f"workflow must not execute wall-clock benchmarks, found {pattern!r}")
    if "compileall" not in text:
        fail("workflow test-smoke must byte-compile benchmark sources")


def run_tool_smoke() -> None:
    env_check = ROOT / "tools" / "benchmark-env-check.py"
    compare = ROOT / "tools" / "benchmark-compare.py"
    require_nonempty(env_check)
    require_nonempty(compare)
    run([sys.executable, str(env_check), "--format", "json"], label="benchmark-env-check smoke")
    baseline = ROOT / "tests" / "fixtures" / "benchmark-capabilities" / "async-baseline.json"
    candidate = ROOT / "tests" / "fixtures" / "benchmark-capabilities" / "async-candidate.json"
    run(
        [sys.executable, str(compare), "--baseline", str(baseline), "--candidate", str(candidate)],
        label="benchmark-compare smoke",
    )


def run_fast_tests() -> None:
    run(
        [sys.executable, "-m", "pytest", "-q", *FAST_TEST_MODULES],
        label="fast capability tests",
        timeout=1200,
        env_extra={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )


def main() -> int:
    for relative in ("async-runtime", "native-cpp", "tools", "tests", "scripts"):
        if not (ROOT / relative).exists():
            fail(f"missing source directory {relative}")
    run(
        [sys.executable, "-m", "compileall", "-q", "async-runtime", "native-cpp", "tools", "tests", "scripts"],
        label="byte-compile benchmark sources",
    )
    check_report_fixtures()
    check_capability_fixtures()
    check_dependencies()
    check_workflow()
    run_tool_smoke()
    run_fast_tests()
    run(
        [sys.executable, "-m", "pytest", "-rs", "async-runtime/test_async_runtime_blackbox.py"],
        label="async runtime black-box pytest",
        timeout=1200,
        env_extra={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    print("benchmark-golden-gate: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
