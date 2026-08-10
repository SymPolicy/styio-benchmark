#!/usr/bin/env python3
"""Small process-tree runner used by the parity harness.

The helper deliberately returns only numeric process facts.  It does not
capture or format child stderr/stdout; callers keep those bytes in private
temporary files and validate them before writing a public report.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


# ``ps`` is a process-wide snapshot on non-Linux hosts.  Calling it for every
# ten-millisecond child interval is both expensive and needlessly perturbs
# short compiler invocations.  A delayed first snapshot lets wait4 capture a
# short direct child while a bounded cadence still observes longer-lived
# descendants.  Linux /proc keeps the lighter cadence because reading a few
# status files does not launch another process.
NO_PROC_SAMPLER_INITIAL_DELAY_S = 0.100
NO_PROC_SAMPLER_MIN_INTERVAL_S = 0.100


@dataclass(frozen=True)
class ProcessSample:
    """Result of one child-process invocation."""

    returncode: int
    elapsed_s: float
    peak_rss_kib: float


class ProcessTimedOut(TimeoutError):
    """A child exceeded the declared wall-clock budget.

    This distinct type lets the public runner emit the stable ``timed_out``
    reason without ever serializing subprocess exception text.
    """


def _sample_process_tree(root_pid: int, stop: threading.Event, result: list[float], interval_s: float) -> None:
    """Sample RSS on a separate thread while the parent blocks in ``wait``.

    Keeping sampling off the wait path avoids quantizing elapsed time to the
    sampler interval.  The list is intentionally private to one invocation;
    no cumulative child resource counter is consulted for the sample.
    """

    peak = 0.0
    proc_available = Path("/proc").is_dir()
    cadence_s = interval_s if proc_available else max(interval_s, NO_PROC_SAMPLER_MIN_INTERVAL_S)
    if not proc_available and stop.wait(NO_PROC_SAMPLER_INITIAL_DELAY_S):
        # A process which completed during the initial delay is covered by
        # wait4's direct-child ru_maxrss below; avoid an unnecessary ps spawn
        # after the waiter has already observed completion.
        result.append(peak)
        return
    while not stop.is_set():
        peak = max(peak, _proc_tree_rss_kib(root_pid))
        if stop.wait(cadence_s):
            break
    # A final sample closes the race where a short-lived process exits just
    # before the sampler observes it.
    peak = max(peak, _proc_tree_rss_kib(root_pid))
    result.append(peak)


def _proc_rss_kib(pid: int) -> float:
    """Read one process RSS without requiring psutil.

    Linux exposes ``VmRSS`` in ``/proc``.  Other Unix hosts use the point-in-
    time ``ps`` process snapshot in :func:`_proc_tree_rss_kib`; a missing proc
    entry is expected when a short-lived child exits between polls.
    """

    status = Path("/proc") / str(pid) / "status"
    try:
        for line in status.read_text(encoding="ascii", errors="ignore").splitlines():
            if line.startswith("VmRSS:"):
                fields = line.split()
                if len(fields) >= 2:
                    return float(fields[1])
    except (OSError, ValueError):
        pass
    return 0.0


def _ps_process_snapshot() -> dict[int, tuple[int, float]]:
    """Return a point-in-time ``pid -> (ppid, rss_kib)`` snapshot.

    macOS and other Unix hosts do not expose Linux's ``/proc`` files.  A
    single ``ps`` listing keeps RSS attribution per invocation and lets the
    sampler construct the same descendant tree without consulting cumulative
    ``RUSAGE_CHILDREN`` counters.
    """

    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    snapshot: dict[int, tuple[int, float]] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
            rss = float(fields[2])
        except (ValueError, TypeError):
            continue
        if pid > 0 and ppid >= 0 and rss >= 0 and rss == rss:
            snapshot[pid] = (ppid, rss)
    return snapshot


def _wait4_rss_kib(value: float) -> float:
    """Normalize direct-child wait4 ``ru_maxrss`` to KiB."""

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    system_name = getattr(os, "uname", lambda: type("Uname", (), {"sysname": ""})())().sysname.lower()
    return numeric / 1024.0 if system_name == "darwin" else numeric


def _proc_tree_rss_kib(root_pid: int) -> float:
    """Return a best-effort RSS sum for a root and its descendants."""

    proc_root = Path("/proc")
    if proc_root.is_dir():
        parents: dict[int, int] = {}
        rss_values: dict[int, float] = {}
        try:
            entries = list(proc_root.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if not entry.name.isdigit():
                continue
            status = entry / "status"
            try:
                ppid = 0
                for line in status.read_text(encoding="ascii", errors="ignore").splitlines():
                    if line.startswith("PPid:"):
                        ppid = int(line.split()[1])
                        break
                if ppid:
                    pid = int(entry.name)
                    parents[pid] = ppid
                    rss_values[pid] = _proc_rss_kib(pid)
            except (OSError, ValueError, IndexError):
                continue
    else:
        snapshot = _ps_process_snapshot()
        parents = {pid: ppid for pid, (ppid, _rss) in snapshot.items()}
        rss_values = {pid: rss for pid, (_ppid, rss) in snapshot.items()}
    members = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, ppid in parents.items():
            if ppid in members and pid not in members:
                members.add(pid)
                changed = True
    return sum(rss_values.get(pid, 0.0) for pid in members)


def run_process(
    command: Sequence[str],
    *,
    cwd: Path,
    stdin_path: Path | None = None,
    stdout_path: Path | None = None,
    timeout_s: float = 300.0,
    env: Mapping[str, str] | None = None,
    sample_interval_s: float = 0.01,
    sample_process_tree: bool = True,
) -> ProcessSample:
    """Execute one command and collect elapsed time plus process-tree RSS.

    Stderr is sent to the null device.  This is intentional: subprocess text
    is diagnostic data, not report data, and callers must not serialize it.
    Set ``sample_process_tree=False`` for an observer-free timing pass.  On
    POSIX hosts the dedicated wait4 waiter still supplies direct-child RSS,
    but no sampler thread or ``ps`` snapshot is started; callers that need
    descendant RSS should run an equivalent isolated replay with the default
    ``True`` value.
    """

    if timeout_s <= 0 or not (timeout_s < float("inf")):
        raise ValueError("timeout_s must be finite and positive")
    if sample_interval_s <= 0 or not (sample_interval_s < float("inf")):
        raise ValueError("sample_interval_s must be finite and positive")

    stdin_handle = stdin_path.open("rb") if stdin_path is not None else subprocess.DEVNULL
    stdout_handle = stdout_path.open("wb") if stdout_path is not None else subprocess.DEVNULL
    sampler_stop = threading.Event()
    sampler_values: list[float] = []
    sampler_thread: threading.Thread | None = None
    child: subprocess.Popen[bytes] | None = None
    try:
        started = time.perf_counter()
        popen_kwargs: dict[str, object] = {
            "cwd": str(cwd),
            "stdin": stdin_handle,
            "stdout": stdout_handle,
            "stderr": subprocess.DEVNULL,
            "close_fds": (os.name != "nt"),
        }
        if env is not None:
            popen_kwargs["env"] = dict(env)
        # A process group makes timeout recovery include descendants (a build
        # driver can otherwise outlive the timed child).
        if os.name != "nt":
            popen_kwargs["start_new_session"] = True
        child = subprocess.Popen(
            list(command),
            **popen_kwargs,
        )
        if sample_process_tree:
            sampler_thread = threading.Thread(
                target=_sample_process_tree,
                args=(child.pid, sampler_stop, sampler_values, sample_interval_s),
                daemon=True,
            )
            sampler_thread.start()
        wait_result: dict[str, object] = {}
        waiter_thread: threading.Thread | None = None

        def wait4_child() -> None:
            try:
                _pid, status, usage = os.wait4(child.pid, 0)
                wait_result["returncode"] = os.waitstatus_to_exitcode(status)
                wait_result["rss_kib"] = _wait4_rss_kib(usage.ru_maxrss)
            except (AttributeError, ChildProcessError, OSError) as exc:
                wait_result["error"] = exc

        # POSIX wait4 blocks in a dedicated waiter and returns direct-child
        # rusage immediately on exit.  The main thread only joins that waiter,
        # so neither RSS polling nor a ps snapshot enters elapsed time.
        use_wait4 = os.name != "nt" and hasattr(os, "wait4") and hasattr(os, "waitstatus_to_exitcode")
        if use_wait4:
            waiter_thread = threading.Thread(target=wait4_child, daemon=True)
            waiter_thread.start()
            waiter_thread.join(timeout=timeout_s)
            if waiter_thread.is_alive():
                if os.name != "nt":
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    child.kill()
                waiter_thread.join()
                raise ProcessTimedOut
            wait_finished = time.perf_counter()
            if "error" in wait_result:
                # A rare wait4 platform failure falls back to the Popen
                # return code only when the waiter did reap the child.
                raise OSError("wait4_failed") from wait_result["error"]
            returncode = int(wait_result.get("returncode", 1))
            child.returncode = returncode
        else:
            try:
                # One blocking wait gives a monotonic elapsed interval
                # independent of RSS polling cadence on non-wait4 hosts.
                returncode = child.wait(timeout=timeout_s)
                wait_finished = time.perf_counter()
            except subprocess.TimeoutExpired as exc:
                if os.name != "nt":
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    child.kill()
                child.wait()
                raise ProcessTimedOut from exc
        sampler_stop.set()
        if sampler_thread is not None:
            sampler_thread.join(timeout=max(1.0, sample_interval_s * 20.0))
        # Stop/joining the sampler is bookkeeping and is intentionally outside
        # the timed blocking-wait interval.
        elapsed = wait_finished - started
        peak = max(sampler_values or [0.0])
        if use_wait4:
            peak = max(peak, float(wait_result.get("rss_kib", 0.0)))
        return ProcessSample(int(returncode), elapsed, peak)
    finally:
        # Timeout/error paths raise before the normal success cleanup; stop
        # and join the sampler here as well so no daemon keeps polling a dead
        # process after the invocation has returned.
        sampler_stop.set()
        if sampler_thread is not None and sampler_thread.is_alive():
            sampler_thread.join(timeout=max(1.0, sample_interval_s * 20.0))
        if child is not None and child.returncode is None:
            try:
                child.kill()
            except OSError:
                pass
        if stdin_path is not None:
            stdin_handle.close()
        if stdout_path is not None:
            stdout_handle.close()



def _bounded_returncode(value: int) -> int:
    """Map a child status to a portable helper exit code."""

    return int(value) if 0 < int(value) < 256 else 1


def _run_batch_exec(arguments: Sequence[str]) -> int:
    """Run a prebuilt artifact repeatedly with argv-safe paths.

    This tiny harness is invoked as a child of :func:`run_process`, so the
    process-tree sampler observes the harness and every artifact child.  It
    deliberately opens the input afresh for each iteration and stops on the
    first non-zero child exit; no shell interpolation or command text is
    involved.
    """

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--batch-exec", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--count", required=True, type=int)
    args = parser.parse_args(list(arguments))
    if args.count <= 0:
        return 64
    artifact = Path(args.batch_exec)
    input_path = Path(args.input)
    output_path = Path(args.output)
    try:
        with output_path.open("wb") as output:
            for _ in range(args.count):
                with input_path.open("rb") as input_handle:
                    child = subprocess.run(
                        [str(artifact)],
                        stdin=input_handle,
                        stdout=output,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                if child.returncode != 0:
                    return int(child.returncode)
    except OSError:
        return 126
    return 0


def _run_batch_build(arguments: Sequence[str], *, run_after_build: bool) -> int:
    """Build fresh artifacts from an argv template, optionally execute each.

    ``{artifact}`` is an explicit, single argv token.  The helper replaces it
    with a new private artifact path for every iteration and never invokes a
    shell.  A compile-and-run caller gets the final child output at
    ``--output``; every intermediate artifact is removed in the per-iteration
    ``finally`` block.  The helper's own exit status is the first failing
    build/run status, preserving fail-closed route semantics.
    """

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--batch-build", action="store_true")
    parser.add_argument("--batch-build-run", action="store_true")
    parser.add_argument("--build-argv-json", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--artifact-token", default="{artifact}")
    parser.add_argument("--input", required=run_after_build)
    parser.add_argument("--output", required=run_after_build)
    parser.add_argument("--count", required=True, type=int)
    args = parser.parse_args(list(arguments))
    if args.count <= 0 or args.artifact_token != "{artifact}":
        return 64
    try:
        template = json.loads(args.build_argv_json)
    except (TypeError, ValueError):
        return 64
    if not isinstance(template, list) or not template or any(not isinstance(value, str) for value in template):
        return 64
    if sum(value == args.artifact_token for value in template) != 1:
        return 64
    artifact_dir = Path(args.artifact_dir)
    input_path = Path(args.input) if run_after_build else None
    output_path = Path(args.output) if run_after_build else None
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if run_after_build and (input_path is None or not input_path.is_file() or output_path is None):
            return 66
        for index in range(args.count):
            artifact = artifact_dir / f"artifact-{index}"
            rendered = [str(artifact) if value == args.artifact_token else value for value in template]
            try:
                built = subprocess.run(
                    rendered,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                if built.returncode != 0 or not artifact.is_file():
                    return _bounded_returncode(built.returncode or 126)
                if run_after_build:
                    assert input_path is not None and output_path is not None
                    with input_path.open("rb") as input_handle, output_path.open("wb") as output_handle:
                        executed = subprocess.run(
                            [str(artifact)],
                            stdin=input_handle,
                            stdout=output_handle,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                    if executed.returncode != 0:
                        return _bounded_returncode(executed.returncode)
            finally:
                try:
                    artifact.unlink()
                except FileNotFoundError:
                    pass
        return 0
    except OSError:
        return 126
    finally:
        # The directory itself belongs to the caller's private work root, but
        # remove any builder side-products if a tool ignored the exact output
        # path contract.
        try:
            for child in artifact_dir.iterdir():
                if child.is_file() or child.is_symlink():
                    child.unlink()
                elif child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
        except OSError:
            pass


if __name__ == "__main__":
    if "--batch-build-run" in sys.argv:
        raise SystemExit(_run_batch_build(sys.argv[1:], run_after_build=True))
    if "--batch-build" in sys.argv:
        raise SystemExit(_run_batch_build(sys.argv[1:], run_after_build=False))
    raise SystemExit(_run_batch_exec(sys.argv[1:]) if "--batch-exec" in sys.argv else 64)


__all__ = ["ProcessSample", "ProcessTimedOut", "run_process"]
