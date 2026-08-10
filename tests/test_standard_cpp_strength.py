"""Strongest-legal-C++ contract checks for parity-v2."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "workloads" / "parity-v2" / "contract.json"


def _gate():
    spec = importlib.util.spec_from_file_location("standard_cpp_strength_test", ROOT / "tools" / "standard_parity_gate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cpp_strength_contract_is_single_threaded_and_no_lto() -> None:
    gate = _gate()
    catalog, path = gate.load_catalog(CATALOG_PATH)
    result = gate.validate_cpp_strength(catalog, path)
    assert result["pass"] is True
    assert result["flags"] == ["-O3", "-DNDEBUG", "-fno-lto"]
    assert result["threads"] == 1


def test_cpp_companions_are_independent_from_styio_sources() -> None:
    catalog = __import__("json").loads(CATALOG_PATH.read_text(encoding="utf-8"))
    for workload in catalog["workloads"]:
        source = workload["source"]
        assert source["source_digest"]["styio"] != source["source_digest"]["cpp"]
        cpp = (CATALOG_PATH.parent / source["cpp"]).read_text(encoding="utf-8")
        assert "-O0" not in cpp
        assert "std::endl" not in cpp
        assert "std::thread" not in cpp


def test_cpp_strength_rejects_weak_flags() -> None:
    gate = _gate()
    assert gate._compiler_strength_violations("clang++ -O0 -flto std::endl") == ["weak-optimization", "lto-enabled", "sync-streams"]
