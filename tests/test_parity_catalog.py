"""Schema, digest, and source checks for the frozen parity-v1 catalog."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = ROOT / "workloads" / "parity-v1"
CONTRACT_PATH = CATALOG_ROOT / "contract.json"
HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_ROUTES = ("compile-and-run", "native-build", "native-run")
FAMILY_SIZES = {
    "scalar-compute": (10_000, 250_000, 5_000_000),
    "collection-reduce": (256, 4_096, 65_536),
    "stream-I/O": (65_536, 4_194_304, 33_554_432),
}
TIERS = ("small", "medium", "large")
PHASES = ("tokenize", "parse", "semantic-analysis", "lowering", "llvm-emission")
PHASE_TARGETS = {"small": 1_000, "medium": 16_000, "large": 128_000}
EMPTY_DIGEST = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
DIAGNOSTIC_CODES = {
    "diagnostic/lex/unterminated-block-comment": "STYIO_LEX_UNTERMINATED_BLOCK_COMMENT",
    "diagnostic/parse/unexpected-token": "STYIO_PARSE_UNEXPECTED_TOKEN",
    "diagnostic/type/fixed-reassignment": "STYIO_TYPE_ERROR",
    "diagnostic/runtime/missing-file": "STYIO_RUNTIME_FILE_OPEN_READ",
}


def _load_generators():
    spec = importlib.util.spec_from_file_location("parity_v1_generators", CATALOG_ROOT / "generators.py")
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load parity-v1 generator module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATORS = _load_generators()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _assert_digest(value: Any, label: str) -> None:
    assert isinstance(value, str), f"{label} must be a SHA-256 string"
    assert HEX_DIGEST.fullmatch(value), f"{label} is not a lowercase SHA-256 digest"


def _route_boundary_is_valid(boundary: Any) -> None:
    assert isinstance(boundary, dict)
    assert set(boundary) == {"start", "stop"}
    assert all(isinstance(boundary[key], str) and boundary[key] for key in boundary)


def validate_catalog(catalog: dict[str, Any], root: Path = CATALOG_ROOT) -> None:
    """Validate the immutable contract; mutation tests exercise this directly."""

    assert catalog.get("schema") == "styio.parity.catalog.v1"
    assert catalog.get("catalog_id") == "parity-v1"
    assert catalog.get("catalog_version") == 1
    assert catalog.get("digest_algorithm") == "sha256"
    assert catalog.get("canonical_encoding") == "utf-8"
    assert catalog.get("generator") == "generators.py"

    routes = catalog.get("routes")
    assert isinstance(routes, list)
    assert tuple(route.get("id") for route in routes) == SUPPORTED_ROUTES
    for route in routes:
        boundary = route.get("boundary")
        assert isinstance(boundary, dict)
        assert set(boundary) == {"start", "stop", "timed_region", "artifact_policy"}
        assert all(isinstance(value, str) and value for value in boundary.values())

    workloads = catalog.get("workloads")
    assert isinstance(workloads, list)
    assert tuple(workload.get("id") for workload in workloads) == tuple(FAMILY_SIZES)
    all_ids: set[str] = set()
    for workload in workloads:
        family = workload["id"]
        sizes = tuple(workload.get("sizes", ()))
        assert sizes == FAMILY_SIZES[family]
        assert all(isinstance(size, int) and size > 0 for size in sizes)
        assert list(sizes) == sorted(set(sizes))
        assert isinstance(workload.get("algorithm_id"), str) and workload["algorithm_id"]
        assert isinstance(workload.get("work_unit"), str) and workload["work_unit"]

        source = workload.get("source")
        assert isinstance(source, dict)
        assert set(source) == {"styio", "cpp", "source_digest"}
        source_digests = source["source_digest"]
        assert set(source_digests) == {"styio", "cpp"}
        for language in ("styio", "cpp"):
            source_relpath = Path(source[language])
            assert not source_relpath.is_absolute()
            source_path = root / source_relpath
            assert source_path.is_file(), source_path
            _assert_digest(source_digests[language], f"{family}.{language}.source_digest")
            assert _sha256(source_path.read_bytes()) == source_digests[language]

        input_spec = workload.get("input")
        assert isinstance(input_spec, dict)
        assert isinstance(input_spec.get("generator"), str)
        assert isinstance(input_spec.get("format"), str)
        by_size = input_spec.get("sha256_by_size")
        assert isinstance(by_size, dict)
        assert tuple(int(size) for size in by_size) == sizes
        for size in sizes:
            _assert_digest(by_size[str(size)], f"{family}.{size}.input_digest")

        cells = workload.get("cells")
        assert isinstance(cells, list)
        assert len(cells) == len(sizes) * len(SUPPORTED_ROUTES)
        expected_ids = {
            f"{family}/{tier}/{route}"
            for tier, _size in zip(TIERS, sizes)
            for route in SUPPORTED_ROUTES
        }
        assert {cell.get("id") for cell in cells} == expected_ids
        for cell in cells:
            cell_id = cell["id"]
            assert cell_id not in all_ids, f"duplicate cell id: {cell_id}"
            all_ids.add(cell_id)
            tier = cell.get("tier")
            assert tier in TIERS
            size = cell.get("size")
            assert size == dict(zip(TIERS, sizes))[tier]
            assert cell.get("work_units") == size
            assert cell.get("algorithm_id") == workload["algorithm_id"]
            assert cell.get("route") in SUPPORTED_ROUTES
            source_digest = cell.get("source_digest")
            assert isinstance(source_digest, dict)
            assert source_digest == source_digests
            _assert_digest(cell.get("input_digest"), f"{cell_id}.input_digest")
            assert cell["input_digest"] == by_size[str(size)]
            for output_key in ("expected_output_digest", "reference_output_digest"):
                _assert_digest(cell.get(output_key), f"{cell_id}.{output_key}")
            assert cell["expected_output_digest"] == cell["reference_output_digest"]
            assert cell.get("focus_owner") in {
                "frontend-parser", "frontend-sema", "ir-lowering", "backend-runtime"
            }
            _route_boundary_is_valid(cell.get("route_boundary"))

    phase_sweep = catalog.get("compiler_phase_sweep")
    assert isinstance(phase_sweep, dict)
    assert phase_sweep.get("token_unit") == "tokens"
    assert phase_sweep.get("token_targets") == PHASE_TARGETS
    assert isinstance(phase_sweep.get("source_generator"), str)
    assert isinstance(phase_sweep.get("reference_generator"), str)
    phase_descriptors = phase_sweep.get("phases")
    assert isinstance(phase_descriptors, list)
    assert tuple(phase.get("id") for phase in phase_descriptors) == PHASES
    for phase in phase_descriptors:
        assert phase.get("route") in SUPPORTED_ROUTES
        _route_boundary_is_valid(phase.get("boundary"))
    phase_cells = phase_sweep.get("cells")
    assert isinstance(phase_cells, list)
    assert len(phase_cells) == len(PHASES) * len(TIERS)
    for cell in phase_cells:
        cell_id = cell["id"]
        assert cell_id not in all_ids, f"duplicate cell id: {cell_id}"
        all_ids.add(cell_id)
        phase, tier = cell_id.split("/")[1:]
        assert phase in PHASES
        assert tier in TIERS
        size = PHASE_TARGETS[tier]
        assert cell["size"] == size and cell["work_units"] == size
        assert cell["algorithm_id"] == f"compiler_phase.{phase.replace('-', '_')}.v1"
        assert cell["route"] in SUPPORTED_ROUTES
        assert set(cell["source_digest"]) == {"styio", "cpp"}
        _assert_digest(cell["source_digest"]["styio"], f"{cell_id}.source_digest")
        _assert_digest(cell["source_digest"]["cpp"], f"{cell_id}.source_digest")
        assert cell["input_digest"] == EMPTY_DIGEST
        for output_key in ("expected_output_digest", "reference_output_digest"):
            _assert_digest(cell.get(output_key), f"{cell_id}.{output_key}")
        assert cell["expected_output_digest"] == cell["reference_output_digest"]
        assert cell["focus_owner"] in {
            "frontend-parser", "frontend-sema", "ir-lowering", "backend-runtime"
        }
        _route_boundary_is_valid(cell.get("route_boundary"))

    diagnostics = catalog.get("diagnostics")
    assert isinstance(diagnostics, list) and len(diagnostics) >= 4
    diagnostic_ids: set[str] = set()
    for case in diagnostics:
        assert case["id"] not in diagnostic_ids
        diagnostic_ids.add(case["id"])
        source_relpath = Path(case["source"])
        assert not source_relpath.is_absolute()
        source = root / source_relpath
        assert source.is_file()
        digest = _sha256(source.read_bytes())
        assert case["source_digest"] == digest
        assert case["input_digest"] == digest
        assert case["kind"] in {"lex", "parse", "type", "runtime"}
        assert case["expected_exit_code"] in {2, 3, 4, 5}
        assert case["expected_diagnostic_code"] == DIAGNOSTIC_CODES[case["id"]]
        assert case["route"] in SUPPORTED_ROUTES
        _route_boundary_is_valid(case["route_boundary"])

    serialized = json.dumps(catalog, sort_keys=True)
    for forbidden in ("cached-jit", "runtime-helper", "runtime-only", "full-cli"):
        assert forbidden not in serialized
    assert "http://" not in serialized and "https://" not in serialized
    assert not re.search(r"(?:^|[\" ])/(?:Users|home|private|tmp)/", serialized)


def test_contract_is_valid_and_exactly_versioned() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    validate_catalog(contract)


def test_independent_reference_digests_match_every_workload_cell() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    references = {
        "scalar-compute": GENERATORS.scalar_compute_reference,
        "collection-reduce": GENERATORS.collection_reduce_reference,
        "stream-I/O": GENERATORS.stream_io_reference,
    }
    inputs = {
        "scalar-compute": GENERATORS.scalar_compute_input,
        "collection-reduce": GENERATORS.collection_reduce_input,
        "stream-I/O": GENERATORS.stream_io_input,
    }
    for workload in contract["workloads"]:
        for cell in workload["cells"]:
            size = cell["work_units"]
            assert _sha256(inputs[workload["id"]](size)) == cell["input_digest"]
            assert _sha256(references[workload["id"]](size)) == cell["reference_output_digest"]


def test_phase_sweep_digests_are_generated_not_precomputed() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    phase_sweep = contract["compiler_phase_sweep"]
    for cell in phase_sweep["cells"]:
        target = cell["work_units"]
        source_digest = _sha256(GENERATORS.phase_source(target))
        output_digest = _sha256(GENERATORS.phase_reference_output(target))
        cpp_source_digest = _sha256(GENERATORS.phase_cpp_source(target))
        assert cell["source_digest"] == {"styio": source_digest, "cpp": cpp_source_digest}
        assert cell["expected_output_digest"] == output_digest
        assert cell["reference_output_digest"] == output_digest


def test_phase_sweep_keeps_language_specific_source_digests() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert all(
        cell["source_digest"]["styio"] != cell["source_digest"]["cpp"]
        for cell in contract["compiler_phase_sweep"]["cells"]
    )


def _styio_compiler() -> Path | None:
    candidates = []
    env_compiler = os.environ.get("STYIO_COMPILER_EXE")
    if env_compiler:
        candidates.append(Path(env_compiler))
    candidates.extend(
        (
            ROOT.parent / "styio-nightly" / "build" / "bin" / "styio",
            ROOT.parent / "styio-nightly" / "build" / "perf-parity-catalog" / "bin" / "styio",
        )
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


@pytest.mark.skipif(_styio_compiler() is None, reason="Styio compiler is not built")
def test_styio_workload_sources_pass_syntax_check() -> None:
    compiler = _styio_compiler()
    assert compiler is not None
    for path in sorted((CATALOG_ROOT / "sources").glob("*.styio")):
        result = subprocess.run(
            [str(compiler), "check", "--syntax", "--json", "--file", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{path.name}: {result.stdout}{result.stderr}"
        assert '"status":"ok"' in result.stdout


@pytest.mark.skipif(_styio_compiler() is None, reason="Styio compiler is not built")
def test_diagnostic_fixtures_keep_exact_codes_and_exit_statuses() -> None:
    compiler = _styio_compiler()
    assert compiler is not None
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for case in contract["diagnostics"]:
        path = CATALOG_ROOT / case["source"]
        result = subprocess.run(
            [str(compiler), "--error-format", "jsonl", "--file", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == case["expected_exit_code"], case["id"]
        assert f'"code":"{case["expected_diagnostic_code"]}"' in result.stderr


@pytest.mark.parametrize(
    "mutation",
    (
        lambda c: c["routes"].pop(),
        lambda c: c["workloads"][0]["cells"].pop(),
        lambda c: c["workloads"][0]["cells"][0].update(id="duplicate"),
        lambda c: c["workloads"][0].update(sizes=[10_000, 9_999, 5_000_000]),
        lambda c: c["workloads"][0]["cells"][0].update(route="unsupported"),
        lambda c: c["workloads"][0]["cells"][0].update(expected_output_digest="0" * 64),
        lambda c: c["diagnostics"][0].update(expected_diagnostic_code="STYIO_CHANGED"),
    ),
)
def test_invalid_catalog_shapes_fail_validation(mutation) -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    mutation(copy.deepcopy(contract))
    mutated = copy.deepcopy(contract)
    mutation(mutated)
    with pytest.raises((AssertionError, KeyError, TypeError)):
        validate_catalog(mutated)
