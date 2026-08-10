#!/usr/bin/env python3
"""Privacy-safe isolated RSS replay helper for the parity-v2 runner.

The historical helper remains available to parity-v1 consumers.  This module
loads the same wait4/process-tree implementation under a distinct public
name, so parity-v2 can evolve its report schema without importing the old
runner.  The helper returns numeric process facts only; it never exposes
commands, paths, or child text.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_BASE = Path(__file__).with_name("process_tree_rss.py")
_SPEC = importlib.util.spec_from_file_location("standard_parity_process_tree_base", _BASE)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - installation error
    raise ImportError("process tree helper unavailable")
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

ProcessSample = _MODULE.ProcessSample
ProcessTimedOut = _MODULE.ProcessTimedOut
run_process = _MODULE.run_process


def run_isolated_process(*args, **kwargs):
    """Run one memory replay with process-tree sampling enabled."""

    kwargs["sample_process_tree"] = True
    return run_process(*args, **kwargs)


__all__ = ["ProcessSample", "ProcessTimedOut", "run_process", "run_isolated_process"]
