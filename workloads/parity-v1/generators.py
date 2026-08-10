"""Deterministic parity-v1 workload and oracle generators.

The catalog stores generator names, parameters, and SHA-256 digests instead of
checking large generated inputs into the repository.  These functions are
deliberately language-neutral: the Styio and C++ runners consume the same
bytes, while the reference functions below implement the expected result
without invoking either language implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable


CATALOG_ID = "parity-v1"
SCALAR_SIZES = (10_000, 250_000, 5_000_000)
COLLECTION_SIZES = (256, 4_096, 65_536)
STREAM_SIZES = (65_536, 4_194_304, 33_554_432)
PHASE_TOKEN_TARGETS = (1_000, 16_000, 128_000)


def sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 digest for canonical bytes."""

    return hashlib.sha256(value).hexdigest()


def scalar_compute_input(iterations: int) -> bytes:
    """Create the runtime-varying scalar input (one decimal count per line)."""

    if iterations <= 0:
        raise ValueError("scalar iterations must be positive")
    return f"{iterations}\n".encode("ascii")


def scalar_compute_reference(iterations: int) -> bytes:
    """Independent scalar reference implementation and canonical output."""

    if iterations <= 0:
        raise ValueError("scalar iterations must be positive")
    accumulator = 17
    modulus = 2_147_483_647
    for index in range(iterations):
        accumulator = (
            accumulator * 1_103_515_245 + index * 12_345 + 1_013_904_223
        ) % modulus
    return f"{accumulator}\n".encode("ascii")


def collection_values(elements: int) -> Iterable[int]:
    """Yield deterministic signed values used by collection-reduce."""

    if elements <= 0:
        raise ValueError("collection element count must be positive")
    for index in range(elements):
        yield (index * 37 + 11) % 100_003


def collection_reduce_input(elements: int) -> bytes:
    """Create a canonical Styio list literal consumed by both programs."""

    values = ",".join(str(value) for value in collection_values(elements))
    return f"[{values}]\n".encode("ascii")


def collection_reduce_reference(elements: int) -> bytes:
    """Independent collection reduction and canonical output."""

    modulus = 1_000_000_007
    accumulator = 0
    for index, value in enumerate(collection_values(elements)):
        accumulator = (accumulator + value * ((index % 17) + 1)) % modulus
    return f"{accumulator}\n".encode("ascii")


def _stream_record(index: int) -> bytes:
    prefix = f"styio-parity/{index:010d}/"
    return (prefix + ("abcdefghijklmnopqrstuvwxyz0123456789" * 2))[:63].encode("ascii")


def stream_io_input(input_bytes: int) -> bytes:
    """Create deterministic newline-delimited input with an exact byte size."""

    if input_bytes <= 0 or input_bytes % 64 != 0:
        raise ValueError("stream input size must be a positive multiple of 64")
    records = input_bytes // 64
    return b"".join(_stream_record(index) + b"\n" for index in range(records))


def stream_io_reference(input_bytes: int) -> bytes:
    """The stream oracle is byte-preserving by contract."""

    return stream_io_input(input_bytes)


def phase_source(token_target: int) -> bytes:
    """Generate a valid, deterministic binding chain near the token target."""

    if token_target <= 0:
        raise ValueError("token target must be positive")
    # Ten tokens per assignment is a stable approximation for the tokenizer;
    # the manifest records the target as a budget, never as a measured count.
    bindings = max(1, token_target // 10)
    lines = ["v0 = 0\n"]
    for index in range(1, bindings + 1):
        lines.append(f"v{index} = v{index - 1} + {index % 97}\n")
    lines.append(f">_(v{bindings})\n")
    return "".join(lines).encode("ascii")


def phase_cpp_source(token_target: int) -> bytes:
    """Generate the equivalent optimized C++ phase attribution source.

    The C++ source intentionally has its own digest: phase attribution compares
    equivalent generated programs, but a Styio source digest must never be
    presented as if it were a C++ source digest.
    """

    if token_target <= 0:
        raise ValueError("token target must be positive")
    bindings = max(1, token_target // 10)
    return (
        "#include <cstdint>\n#include <iostream>\n"
        "int main(){std::int64_t value=0;"
        f"for(std::int64_t i=1;i<={bindings};++i)value+=i%97;"
        "std::cout<<value<<'\\n';return 0;}\n"
    ).encode("ascii")


def phase_reference_output(token_target: int) -> bytes:
    """Reference output for the generated compiler-phase program."""

    if token_target <= 0:
        raise ValueError("token target must be positive")
    bindings = max(1, token_target // 10)
    total = sum(index % 97 for index in range(1, bindings + 1))
    return f"{total}\n".encode("ascii")


def digest_for_input(family: str, work_units: int) -> str:
    generators = {
        "scalar-compute": scalar_compute_input,
        "collection-reduce": collection_reduce_input,
        "stream-I/O": stream_io_input,
    }
    try:
        return sha256_bytes(generators[family](work_units))
    except KeyError as exc:
        raise ValueError(f"unknown workload family: {family}") from exc


def digest_for_reference(family: str, work_units: int) -> str:
    references = {
        "scalar-compute": scalar_compute_reference,
        "collection-reduce": collection_reduce_reference,
        "stream-I/O": stream_io_reference,
    }
    try:
        return sha256_bytes(references[family](work_units))
    except KeyError as exc:
        raise ValueError(f"unknown workload family: {family}") from exc


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=("scalar-compute", "collection-reduce", "stream-I/O"))
    parser.add_argument("--work-units", type=int)
    parser.add_argument("--phase-tokens", type=int)
    parser.add_argument("--kind", choices=("input", "reference", "phase", "phase-cpp"), default="input")
    args = parser.parse_args()

    if args.kind in {"phase", "phase-cpp"}:
        if args.phase_tokens is None:
            parser.error("--phase-tokens is required for --kind phase")
        payload = phase_source(args.phase_tokens) if args.kind == "phase" else phase_cpp_source(args.phase_tokens)
    else:
        if args.family is None or args.work_units is None:
            parser.error("--family and --work-units are required")
        if args.kind == "input":
            payload = {
                "scalar-compute": scalar_compute_input,
                "collection-reduce": collection_reduce_input,
                "stream-I/O": stream_io_input,
            }[args.family](args.work_units)
        else:
            payload = {
                "scalar-compute": scalar_compute_reference,
                "collection-reduce": collection_reduce_reference,
                "stream-I/O": stream_io_reference,
            }[args.family](args.work_units)
    print(json.dumps({"sha256": sha256_bytes(payload), "bytes": len(payload)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
