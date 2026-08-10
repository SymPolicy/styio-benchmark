"""Recursive report privacy contract tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location("parity_gate_privacy_module", ROOT / "tools" / "parity_gate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GATE = _module()


@pytest.mark.parametrize(
    "payload",
    (
        {"hostname": "public-host"},
        {"nested": {"environment": {"PATH": "private"}}},
        {"command": ["styio", "--file", "source.styio"]},
        {"endpoint": "https://example.invalid"},
        {"secret": "sk-test-value"},
        {"value": "/Users/example/project"},
        {"value": "unsanitized\nchild stderr"},
    ),
)
def test_recursive_privacy_rejects_sensitive_payloads(payload) -> None:
    with pytest.raises(GATE.PrivacyError):
        GATE.validate_public_report(payload, strict=True)


def test_recursive_privacy_allows_stable_public_metadata() -> None:
    payload = {
        "schema": "styio.parity.report.v1",
        "catalog_id": "parity-v1",
        "contract_digest": "0" * 64,
        "target_class": "arm64-darwin",
        "reason_codes": ["missing_samples"],
        "cells": [{"id": "scalar-compute/small/native-run", "samples": [0.1, 0.2]}],
    }
    assert GATE.assert_public_report(payload, strict=True) == payload


def test_nonfinite_numbers_are_rejected() -> None:
    with pytest.raises(GATE.PrivacyError):
        GATE.validate_public_report({"ratio": float("nan")}, strict=True)
