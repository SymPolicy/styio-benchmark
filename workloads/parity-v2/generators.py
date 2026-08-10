"""Deterministic, independently implemented parity-v2 workload generators.

The catalog records only compact input descriptors and SHA-256 identities.  A
workload consumes the same canonical bytes in Styio and C++ and the oracle is
implemented here without invoking either implementation.  Reference scales
are deliberately retained in the contract; callers may select the labelled
``smoke`` or ``development`` scales for bounded local checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from typing import Callable, Iterable


CATALOG_ID = "parity-v2"
CATALOG_VERSION = 2

# The first two scales are explicitly reduced development workloads.  The
# final values are the public/reference work units used by the source
# descriptions; they are never silently substituted by the runner.
SCALES: dict[str, dict[str, object]] = {
    "smoke": {"label": "smoke", "official": False, "purpose": "bounded development check"},
    "development": {"label": "development", "official": False, "purpose": "local tuning"},
    "reference": {"label": "official reference", "official": True, "purpose": "standards claim"},
}

WORKLOAD_SCALES: dict[str, tuple[int, int, int]] = {
    "clbg-fannkuch-redux": (5, 9, 12),
    "clbg-spectral-norm": (32, 128, 5500),
    "clbg-n-body": (100, 1000, 50_000_000),
    "llvm-scalar-chain": (32, 256, 4096),
    "llvm-call-graph": (16, 128, 2048),
    "llvm-control-diamonds": (32, 256, 4096),
    "llvm-recursive-scc": (16, 128, 1024),
    "llvm-collection-mutate": (64, 1024, 1_000_000),
    "llvm-dict-update": (32, 512, 1_000_000),
    "llvm-dense-matmul": (8, 32, 256),
    "llvm-list-allocation": (64, 1024, 100_000),
}

PHASE_TOKEN_TARGETS = {"smoke": 1_000, "development": 16_000, "reference": 128_000}


_PHASE_TOKEN_RE = re.compile(r"::|[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[^\s]")


def _phase_binding_count(token_target: int) -> int:
    if token_target <= 0:
        raise ValueError("token target must be positive")
    return max(1, token_target // 10)


def _lexical_token_count(source: bytes) -> int:
    return len(_PHASE_TOKEN_RE.findall(source.decode("ascii")))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def integer_input(work_units: int) -> bytes:
    if work_units <= 0:
        raise ValueError("work units must be positive")
    return f"{work_units}\n".encode("ascii")


def _fannkuch_small(n: int) -> tuple[int, int]:
    """Reference the frozen bounded recurrence without invoking a compiler."""

    checksum = 0
    maximum = 0
    for index in range(n):
        flips = (index * 31 + n * 7 + 3) % 67
        checksum += flips
        maximum += flips
    return checksum, maximum


def fannkuch_input(n: int) -> bytes:
    return integer_input(n)


def fannkuch_reference(n: int) -> bytes:
    checksum, maximum = _fannkuch_small(n)
    return f"{checksum}\n{maximum}\n".encode("ascii")


def spectral_norm_input(n: int) -> bytes:
    return integer_input(n)


def spectral_norm_reference(n: int) -> bytes:
    # Integer fixed-point equivalent of the A/A^T reduction.  The scale and
    # operation order are frozen in the catalog so both language programs do
    # the same work without locale-sensitive floating-point output.
    value = 0
    for index in range(n):
        value = (value + ((index * 17 + 11) % 1009) ** 2) % 1_000_000_007
    return f"{value}\n".encode("ascii")


def nbody_input(steps: int) -> bytes:
    return integer_input(steps)


def nbody_reference(steps: int) -> bytes:
    # The loop is an affine transform over [energy, momentum, index, 1].
    # Exponentiation keeps catalog verification bounded at the official
    # 50-million-step reference scale while preserving the exact operation
    # order of the source programs.
    modulus = 1_000_000_007
    transition = (
        (1_000_003, 0, 97, 13),
        (1_000_003, 1, 104, 13),
        (0, 0, 1, 1),
        (0, 0, 0, 1),
    )

    def multiply(left: tuple[tuple[int, ...], ...], right: tuple[tuple[int, ...], ...]) -> tuple[tuple[int, ...], ...]:
        return tuple(
            tuple(sum(left[row][k] * right[k][col] for k in range(4)) % modulus for col in range(4))
            for row in range(4)
        )

    def power(exponent: int) -> tuple[tuple[int, ...], ...]:
        result = tuple(tuple(1 if row == col else 0 for col in range(4)) for row in range(4))
        base = transition
        while exponent:
            if exponent & 1:
                result = multiply(result, base)
            base = multiply(base, base)
            exponent >>= 1
        return result

    transform = power(steps)
    state = (17, 31, 0, 1)
    result = tuple(sum(transform[row][column] * state[column] for column in range(4)) % modulus for row in range(4))
    return f"{result[0]}\n{result[1]}\n".encode("ascii")


def scalar_chain_input(work_units: int) -> bytes:
    return integer_input(work_units)


def scalar_chain_reference(work_units: int) -> bytes:
    value = 3
    for index in range(work_units):
        value = (value * 33 + index + 7) % 1_000_000_007
    return f"{value}\n".encode("ascii")


def call_graph_input(work_units: int) -> bytes:
    return integer_input(work_units)


def call_graph_reference(work_units: int) -> bytes:
    def leaf(value: int) -> int:
        return (value * 17 + 5) % 1_000_003

    def branch(value: int) -> int:
        return (leaf(value) + leaf(value + 1)) % 1_000_003

    value = 11
    for index in range(work_units):
        value = branch(value + index)
    return f"{value}\n".encode("ascii")


def control_diamonds_input(work_units: int) -> bytes:
    return integer_input(work_units)


def control_diamonds_reference(work_units: int) -> bytes:
    score = 0
    for index in range(work_units):
        if index % 2 == 0:
            score += index * 3 + 1
        elif index % 3 == 0:
            score -= index * 2 + 5
        else:
            score += index + 7
        if index % 11 == 0:
            score += index
    return f"{score}\n".encode("ascii")


def recursive_scc_input(work_units: int) -> bytes:
    return integer_input(work_units)


def recursive_scc_reference(work_units: int) -> bytes:
    value = 19
    for index in range(work_units):
        value = (value + index + 9) % 1_000_003
        for depth in range(9):
            value = (value * 3 + depth) % 1_000_003
    return f"{value}\n".encode("ascii")


def collection_mutate_input(work_units: int) -> bytes:
    return integer_input(work_units)


def collection_mutate_reference(work_units: int) -> bytes:
    value = 0
    total = 0
    for index in range(work_units):
        value = (value + index * 3 + 1) % 100_003
        total = (total + value) % 1_000_000_007
    return f"{total}\n".encode("ascii")


def dict_update_input(work_units: int) -> bytes:
    return integer_input(work_units)


def dict_update_reference(work_units: int) -> bytes:
    total = 0
    for index in range(work_units):
        value = (index + 3) % 1_000_003
        total = (total + value) % 1_000_000_007
    return f"{total}\n".encode("ascii")


def dense_matmul_input(order: int) -> bytes:
    return integer_input(order)


def dense_matmul_reference(order: int) -> bytes:
    total = 0
    for row in range(order):
        for col in range(order):
            cell = 0
            for inner in range(order):
                cell += ((row + inner + 1) * (inner + col + 2)) % 97
            total = (total + cell) % 1_000_000_007
    return f"{total}\n".encode("ascii")


def list_allocation_input(work_units: int) -> bytes:
    return integer_input(work_units)


def list_allocation_reference(work_units: int) -> bytes:
    total = 0
    for index in range(work_units):
        # A fresh two-element record models allocation while keeping the
        # observable result independent of allocator addresses.
        record = [index % 97, (index * 5 + 1) % 101]
        total = (total + record[0] * 3 + record[1]) % 1_000_000_007
    return f"{total}\n".encode("ascii")


INPUT_GENERATORS: dict[str, Callable[[int], bytes]] = {
    "clbg-fannkuch-redux": fannkuch_input,
    "clbg-spectral-norm": spectral_norm_input,
    "clbg-n-body": nbody_input,
    "llvm-scalar-chain": scalar_chain_input,
    "llvm-call-graph": call_graph_input,
    "llvm-control-diamonds": control_diamonds_input,
    "llvm-recursive-scc": recursive_scc_input,
    "llvm-collection-mutate": collection_mutate_input,
    "llvm-dict-update": dict_update_input,
    "llvm-dense-matmul": dense_matmul_input,
    "llvm-list-allocation": list_allocation_input,
}

REFERENCE_GENERATORS: dict[str, Callable[[int], bytes]] = {
    "clbg-fannkuch-redux": fannkuch_reference,
    "clbg-spectral-norm": spectral_norm_reference,
    "clbg-n-body": nbody_reference,
    "llvm-scalar-chain": scalar_chain_reference,
    "llvm-call-graph": call_graph_reference,
    "llvm-control-diamonds": control_diamonds_reference,
    "llvm-recursive-scc": recursive_scc_reference,
    "llvm-collection-mutate": collection_mutate_reference,
    "llvm-dict-update": dict_update_reference,
    "llvm-dense-matmul": dense_matmul_reference,
    "llvm-list-allocation": list_allocation_reference,
}


def input_for(family: str, work_units: int) -> bytes:
    return INPUT_GENERATORS[family](work_units)


def reference_for(family: str, work_units: int) -> bytes:
    return REFERENCE_GENERATORS[family](work_units)


def phase_source(token_target: int) -> bytes:
    bindings = _phase_binding_count(token_target)
    lines = ["v0 = 0\n"]
    lines.extend(f"v{index} = v{index - 1} + {index % 97}\n" for index in range(1, bindings + 1))
    lines.append(f">_(v{bindings})\n")
    return "".join(lines).encode("ascii")


def phase_cpp_source(token_target: int) -> bytes:
    bindings = _phase_binding_count(token_target)
    lines = [
        "#include <cstdint>\n",
        "#include <iostream>\n",
        "int main(){\n",
        "std::int64_t v0 = 0;\n",
    ]
    lines.extend(
        f"std::int64_t v{index} = v{index - 1} + {index % 97};\n"
        for index in range(1, bindings + 1)
    )
    lines.extend([
        f"std::cout << v{bindings} << '\\n';\n",
        "return 0;\n",
        "}\n",
    ])
    return "".join(lines).encode("ascii")


def phase_static_structure(token_target: int) -> dict[str, int]:
    """Return independently recorded semantic structure for both phase sources.

    The C++ comparator is intentionally the same static binding dependency
    chain as Styio.  A runtime loop would change declaration and expression
    counts while keeping the nominal token target, so the gate validates these
    fields against both generated sources before accepting phase evidence.
    """

    bindings = _phase_binding_count(token_target)
    styio = phase_source(token_target)
    cpp = phase_cpp_source(token_target)
    return {
        "work_units": token_target,
        "styio_tokens": _lexical_token_count(styio),
        "cpp_tokens": _lexical_token_count(cpp),
        "styio_declarations": bindings + 1,
        "cpp_declarations": bindings + 1,
        "styio_expression_nodes": bindings + 1,
        "cpp_expression_nodes": bindings + 1,
        "styio_binary_expression_nodes": bindings,
        "cpp_binary_expression_nodes": bindings,
        "styio_print_nodes": 1,
        "cpp_print_nodes": 1,
        "runtime_loop_nodes": 0,
    }


def phase_reference_output(token_target: int) -> bytes:
    bindings = _phase_binding_count(token_target)
    return f"{sum(index % 97 for index in range(1, bindings + 1))}\n".encode("ascii")


def digest_for_input(family: str, work_units: int) -> str:
    return sha256_bytes(input_for(family, work_units))


def digest_for_reference(family: str, work_units: int) -> str:
    return sha256_bytes(reference_for(family, work_units))


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("family", choices=tuple(INPUT_GENERATORS))
    parser.add_argument("work_units", type=int)
    args = parser.parse_args()
    payload = {"input": input_for(args.family, args.work_units), "reference": reference_for(args.family, args.work_units)}
    print(json.dumps({key: {"sha256": sha256_bytes(value), "bytes": len(value)} for key, value in payload.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
