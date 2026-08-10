"""Operation-budget regression for the compiler delimiter-prefix cache.

The test deliberately checks counters rather than wall time.  Wall time is
useful for diagnosis but is too host-sensitive for a permanent gate.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
STYIO_ROOT = ROOT.parent / "styio-nightly"
STYIO = STYIO_ROOT / "build" / "perf-parity-baseline" / "bin" / "styio"
GENERATORS = ROOT / "workloads" / "parity-v2" / "generators.py"


def _load_generators():
    spec = importlib.util.spec_from_file_location("parity_v2_generators", GENERATORS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("sizes", [(16_000, 32_000, 64_000, 128_000)])
def test_delimiter_prefix_operation_budget(tmp_path: Path, sizes: tuple[int, ...]) -> None:
    if not STYIO.is_file():
        pytest.skip("perf-parity compiler build is unavailable")
    generators = _load_generators()
    observations: list[tuple[int, int, int]] = []
    for size in sizes:
        source = tmp_path / f"phase-{size}.styio"
        profile = tmp_path / f"phase-{size}.json"
        source.write_bytes(generators.phase_source(size))
        completed = subprocess.run(
            [
                str(STYIO),
                "--profile-frontend",
                "--profile-out",
                str(profile),
                str(source),
            ],
            cwd=STYIO_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        payload = json.loads(profile.read_text(encoding="utf-8"))
        counters = payload["counters"]
        token_count = int(counters["token_count"])
        prefix_steps = int(counters["delimiter_nesting_prefix_steps"])
        queries = int(counters["delimiter_nesting_queries"])
        assert prefix_steps <= token_count + 1
        assert queries <= token_count
        observations.append((size, token_count, prefix_steps))

    for (_, previous_tokens, previous_steps), (_, tokens, steps) in zip(
        observations, observations[1:]
    ):
        # The generated chain doubles work at each scale.  Prefix construction
        # must stay linear; this rejects a reintroduced prefix scan without a
        # noisy wall-clock threshold.
        assert tokens <= previous_tokens * 2 + 32
        assert steps <= previous_steps * 2 + 32
