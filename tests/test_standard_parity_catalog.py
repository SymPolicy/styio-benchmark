"""Contract tests for the standards-derived parity-v2 catalog."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "workloads" / "parity-v2" / "contract.json"


def _generators():
    spec = importlib.util.spec_from_file_location("standard_parity_generators_test", CATALOG_PATH.parent / "generators.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gate():
    spec = importlib.util.spec_from_file_location("standard_parity_gate_catalog_test", ROOT / "tools" / "standard_parity_gate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_is_standards_derived_and_complete() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert catalog["schema"] == "styio.parity.catalog.v2"
    assert catalog["catalog_id"] == "parity-v2"
    assert tuple(catalog["scales"]) == ("development", "reference", "smoke")
    assert catalog["scales"]["reference"]["official"] is True
    assert catalog["scales"]["smoke"]["official"] is False
    assert tuple(route["id"] for route in catalog["routes"]) == ("compile-and-run", "native-build", "native-run")
    assert len(catalog["workloads"]) == 11
    assert {workload["standard_family"] for workload in catalog["workloads"]} == {"CLBG", "LLVM-TestSuite"}
    assert len(catalog["compiler_phase_sweep"]["cells"]) == 15
    assert {case["id"] for case in catalog["unsupported_cases"]} == {"bit-packed", "byte-buffer", "object-node", "regular-expression", "arbitrary-precision"}


def test_catalog_digests_are_generated_from_independent_sources() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    generators = _generators()
    for workload in catalog["workloads"]:
        family = workload["id"]
        source = workload["source"]
        assert source["source_digest"]["styio"] == hashlib.sha256((CATALOG_PATH.parent / source["styio"]).read_bytes()).hexdigest()
        assert source["source_digest"]["cpp"] == hashlib.sha256((CATALOG_PATH.parent / source["cpp"]).read_bytes()).hexdigest()
        assert source["source_digest"]["styio"] != source["source_digest"]["cpp"]
        for scale, size in zip(("smoke", "development", "reference"), generators.WORKLOAD_SCALES[family]):
            assert workload["input"]["sha256_by_scale"][scale] == generators.digest_for_input(family, size)
            assert workload["output_oracle"]["sha256_by_scale"][scale] == generators.digest_for_reference(family, size)
            cells = [cell for cell in workload["cells"] if cell["scale"] == scale]
            assert len(cells) == 3
            assert {cell["route"] for cell in cells} == {"compile-and-run", "native-build", "native-run"}
            assert all(cell["required"] is (scale == "reference") for cell in cells)


def test_catalog_check_rejects_digest_mutation() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    catalog["workloads"][0]["input"]["sha256_by_scale"]["smoke"] = "0" * 64
    gate = _gate()
    try:
        gate.validate_catalog(catalog, CATALOG_PATH)
    except gate.GateError as error:
        assert error.reason_code in {"catalog_generated_digest_mismatch", "catalog_cell_input_digest"}
    else:
        raise AssertionError("catalog mutation unexpectedly passed")


def test_phase_catalog_rejects_static_shape_and_runtime_loop_mutations() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    gate = _gate()
    catalog["compiler_phase_sweep"]["static_structure"]["reference"]["cpp_declarations"] -= 1
    try:
        gate.validate_catalog(catalog, CATALOG_PATH)
    except gate.GateError as error:
        assert error.reason_code == "catalog_phase_static_structure"
    else:
        raise AssertionError("phase static-structure mutation unexpectedly passed")

    generators = _generators()
    target = generators.PHASE_TOKEN_TARGETS["smoke"]
    expected = generators.phase_static_structure(target)

    class RuntimeLoopMutation:
        phase_source = staticmethod(generators.phase_source)
        phase_static_structure = staticmethod(generators.phase_static_structure)
        phase_cpp_source = staticmethod(
            lambda token_target: (
                b"#include <cstdint>\n#include <iostream>\n"
                b"int main(){std::int64_t value=0;"
                + f"for(std::int64_t i=1;i<={max(1, token_target // 10)};++i)value+=i%97;".encode("ascii")
                + b"std::cout<<value<<'\\n';return 0;}\n"
            )
        )

    actual = gate._phase_source_structure(RuntimeLoopMutation, target)
    assert actual["runtime_loop_nodes"] > 0
    assert actual != expected


def test_official_reference_scales_are_not_reduced() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    generators = _generators()
    for workload in catalog["workloads"]:
        smoke, development, reference = generators.WORKLOAD_SCALES[workload["id"]]
        assert workload["scales"]["smoke"]["work_units"] == smoke
        assert workload["scales"]["development"]["work_units"] == development
        assert workload["scales"]["reference"]["work_units"] == reference
        assert workload["scales"]["reference"]["official"] is True


def test_privacy_forbids_machine_and_private_data_in_reports() -> None:
    gate = _gate()
    gate.validate_public_report({"schema": "ok", "samples": [1.0, 2.0]})
    try:
        gate.validate_public_report({"host_name": "workstation"})
    except gate.PrivacyError as error:
        assert error.reason_code == "privacy_forbidden_key"
    else:
        raise AssertionError("private report key unexpectedly accepted")
