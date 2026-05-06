#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STYIO_BUILD_DIR = "build/async-runtime-release"
DEFAULT_TASKS = 4
DEFAULT_SLEEP_MS = 160
DEFAULT_NOOP_TASKS = 100000
DEFAULT_WORKERS = 4
DEFAULT_REPEATS = 5
DEFAULT_CASE = "baseline"
RUNTIME_CHOICES = ("styio", "cpp", "go", "rust")


def default_styio_root() -> Path:
    raw = os.environ.get("STYIO_ROOT")
    if raw:
        return Path(raw).expanduser()
    return BENCHMARK_ROOT.parent / "styio"


@dataclass(frozen=True)
class BenchmarkCase:
    tasks: int
    sleep_ms: int
    noop_tasks: int
    workers: int
    repeats: int
    description: str


BENCHMARK_CASES = {
    "smoke": BenchmarkCase(
        tasks=2,
        sleep_ms=20,
        noop_tasks=1000,
        workers=2,
        repeats=1,
        description="quick black-box contract check for local and CI smoke runs",
    ),
    "baseline": BenchmarkCase(
        tasks=DEFAULT_TASKS,
        sleep_ms=DEFAULT_SLEEP_MS,
        noop_tasks=DEFAULT_NOOP_TASKS,
        workers=DEFAULT_WORKERS,
        repeats=DEFAULT_REPEATS,
        description="median performance comparison against C++ stackless coroutine, goroutine, and Tokio",
    ),
    "stress": BenchmarkCase(
        tasks=8,
        sleep_ms=160,
        noop_tasks=200000,
        workers=4,
        repeats=7,
        description="higher fanout comparison for scheduler regression investigation",
    ),
}


CPP_SOURCE = r'''
#include <chrono>
#include <condition_variable>
#include <coroutine>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <memory>
#include <mutex>
#include <queue>
#include <stdexcept>
#include <thread>
#include <utility>
#include <vector>

using Clock = std::chrono::steady_clock;

class CoroutineScheduler {
public:
  explicit CoroutineScheduler(int worker_count) {
    for (int i = 0; i < worker_count; ++i) {
      workers_.emplace_back([this]() { worker_loop(); });
    }
    timer_ = std::thread([this]() { timer_loop(); });
  }

  ~CoroutineScheduler() {
    stopping_.store(true);
    {
      std::lock_guard<std::mutex> lock(ready_mu_);
    }
    ready_cv_.notify_all();
    {
      std::lock_guard<std::mutex> lock(timer_mu_);
    }
    timer_cv_.notify_all();
    for (auto& worker : workers_) {
      if (worker.joinable()) {
        worker.join();
      }
    }
    if (timer_.joinable()) {
      timer_.join();
    }
  }

  void schedule(std::coroutine_handle<> handle) {
    if (handle.done()) {
      return;
    }
    {
      std::lock_guard<std::mutex> lock(ready_mu_);
      ready_.push(handle);
    }
    ready_cv_.notify_one();
  }

  void schedule_after(std::coroutine_handle<> handle, std::chrono::milliseconds delay) {
    const auto due = Clock::now() + delay;
    {
      std::lock_guard<std::mutex> lock(timer_mu_);
      timers_.push(TimerItem{due, handle, next_timer_sequence_++});
    }
    timer_cv_.notify_one();
  }

private:
  struct TimerItem {
    Clock::time_point due;
    std::coroutine_handle<> handle;
    uint64_t sequence;
  };

  struct TimerCompare {
    bool operator()(const TimerItem& lhs, const TimerItem& rhs) const {
      if (lhs.due == rhs.due) {
        return lhs.sequence > rhs.sequence;
      }
      return lhs.due > rhs.due;
    }
  };

  std::mutex ready_mu_;
  std::condition_variable ready_cv_;
  std::queue<std::coroutine_handle<>> ready_;
  std::mutex timer_mu_;
  std::condition_variable timer_cv_;
  std::priority_queue<TimerItem, std::vector<TimerItem>, TimerCompare> timers_;
  std::vector<std::thread> workers_;
  std::thread timer_;
  std::atomic<bool> stopping_{false};
  uint64_t next_timer_sequence_ = 0;

  void worker_loop() {
    for (;;) {
      std::coroutine_handle<> handle;
      {
        std::unique_lock<std::mutex> lock(ready_mu_);
        ready_cv_.wait(lock, [this]() { return stopping_.load() || !ready_.empty(); });
        if (ready_.empty()) {
          if (stopping_.load()) {
            return;
          }
          continue;
        }
        handle = ready_.front();
        ready_.pop();
      }
      if (!handle.done()) {
        handle.resume();
      }
    }
  }

  void timer_loop() {
    std::unique_lock<std::mutex> lock(timer_mu_);
    for (;;) {
      if (stopping_.load()) {
        return;
      }
      if (timers_.empty()) {
        timer_cv_.wait(lock, [this]() { return stopping_.load() || !timers_.empty(); });
        continue;
      }
      const auto due = timers_.top().due;
      timer_cv_.wait_until(lock, due, [this, due]() {
        return stopping_.load() || timers_.empty() || timers_.top().due < due;
      });
      if (stopping_.load()) {
        return;
      }
      const auto now = Clock::now();
      while (!timers_.empty() && timers_.top().due <= now) {
        const auto item = timers_.top();
        timers_.pop();
        lock.unlock();
        schedule(item.handle);
        lock.lock();
        if (stopping_.load()) {
          return;
        }
      }
    }
  }
};

struct SleepAwaitable {
  CoroutineScheduler& scheduler;
  std::chrono::milliseconds delay;

  bool await_ready() const noexcept {
    return delay.count() <= 0;
  }

  void await_suspend(std::coroutine_handle<> handle) const {
    scheduler.schedule_after(handle, delay);
  }

  void await_resume() const noexcept {}
};

class Task {
public:
  struct State {
    std::mutex mu;
    std::condition_variable cv;
    bool done = false;
    int64_t value = 0;
    std::exception_ptr exception;
  };

  struct promise_type {
    std::shared_ptr<State> state = std::make_shared<State>();

    Task get_return_object() {
      return Task{std::coroutine_handle<promise_type>::from_promise(*this), state};
    }

    std::suspend_always initial_suspend() noexcept {
      return {};
    }

    struct FinalAwaiter {
      bool await_ready() noexcept {
        return false;
      }

      void await_suspend(std::coroutine_handle<promise_type> handle) noexcept {
        auto state = handle.promise().state;
        {
          std::lock_guard<std::mutex> lock(state->mu);
          state->done = true;
        }
        state->cv.notify_one();
      }

      void await_resume() noexcept {}
    };

    FinalAwaiter final_suspend() noexcept {
      return {};
    }

    void return_value(int64_t value) noexcept {
      state->value = value;
    }

    void unhandled_exception() noexcept {
      state->exception = std::current_exception();
    }
  };

  Task(Task&& other) noexcept : handle_(std::exchange(other.handle_, {})), state_(std::move(other.state_)) {}

  Task& operator=(Task&& other) noexcept {
    if (this != &other) {
      destroy();
      handle_ = std::exchange(other.handle_, {});
      state_ = std::move(other.state_);
    }
    return *this;
  }

  Task(const Task&) = delete;
  Task& operator=(const Task&) = delete;

  ~Task() {
    destroy();
  }

  void start(CoroutineScheduler& scheduler) {
    if (!handle_) {
      throw std::logic_error("coroutine task already consumed");
    }
    scheduler.schedule(handle_);
  }

  int64_t get() {
    std::unique_lock<std::mutex> lock(state_->mu);
    state_->cv.wait(lock, [this]() { return state_->done; });
    const auto value = state_->value;
    const auto exception = state_->exception;
    lock.unlock();
    destroy();
    if (exception) {
      std::rethrow_exception(exception);
    }
    return value;
  }

private:
  Task(std::coroutine_handle<promise_type> handle, std::shared_ptr<State> state)
      : handle_(handle), state_(std::move(state)) {}

  void destroy() {
    if (handle_) {
      handle_.destroy();
      handle_ = {};
    }
  }

  std::coroutine_handle<promise_type> handle_;
  std::shared_ptr<State> state_;
};

Task sleep_task(CoroutineScheduler& scheduler, int sleep_ms) {
  co_await SleepAwaitable{scheduler, std::chrono::milliseconds(sleep_ms)};
  co_return 1;
}

Task noop_task() {
  co_return 1;
}

long long elapsed_ms(Clock::time_point start, Clock::time_point end) {
  return std::chrono::duration_cast<std::chrono::milliseconds>(end - start).count();
}

long long elapsed_us(Clock::time_point start, Clock::time_point end) {
  return std::chrono::duration_cast<std::chrono::microseconds>(end - start).count();
}

int main(int argc, char** argv) {
  int tasks = std::atoi(argv[1]);
  int sleep_ms = std::atoi(argv[2]);
  int noop_tasks = std::atoi(argv[3]);
  int workers = std::atoi(argv[4]);

  auto seq_start = Clock::now();
  for (int i = 0; i < tasks; ++i) {
    std::this_thread::sleep_for(std::chrono::milliseconds(sleep_ms));
  }
  auto seq_ms = elapsed_ms(seq_start, Clock::now());

  CoroutineScheduler scheduler(workers);
  std::vector<Task> sleep_tasks;
  sleep_tasks.reserve(tasks);
  auto par_start = Clock::now();
  for (int i = 0; i < tasks; ++i) {
    sleep_tasks.push_back(sleep_task(scheduler, sleep_ms));
    sleep_tasks.back().start(scheduler);
  }
  int64_t sleep_sum = 0;
  for (auto& task : sleep_tasks) {
    sleep_sum += task.get();
  }
  auto par_ms = elapsed_ms(par_start, Clock::now());

  std::vector<Task> noop_tasks_vec;
  noop_tasks_vec.reserve(noop_tasks);
  auto noop_start = Clock::now();
  for (int i = 0; i < noop_tasks; ++i) {
    noop_tasks_vec.push_back(noop_task());
    noop_tasks_vec.back().start(scheduler);
  }
  int64_t noop_sum = 0;
  for (auto& task : noop_tasks_vec) {
    noop_sum += task.get();
  }
  auto noop_us = elapsed_us(noop_start, Clock::now());

  std::cout << "{"
            << "\"language\":\"cpp\","
            << "\"runtime\":\"cpp_stackless_coroutine\","
            << "\"status\":\"ok\","
            << "\"workers\":" << workers << ","
            << "\"sleep\":{\"tasks\":" << tasks
            << ",\"sleep_ms\":" << sleep_ms
            << ",\"sequential_ms\":" << seq_ms
            << ",\"parallel_ms\":" << par_ms
            << ",\"speedup\":" << (par_ms > 0 ? double(seq_ms) / double(par_ms) : 0.0)
            << ",\"sum\":" << sleep_sum << "},"
            << "\"noop\":{\"tasks\":" << noop_tasks
            << ",\"total_us\":" << noop_us
            << ",\"per_task_us\":" << (noop_tasks > 0 ? double(noop_us) / double(noop_tasks) : 0.0)
            << ",\"sum\":" << noop_sum << "}"
            << "}\n";
  return 0;
}
'''


GO_SOURCE = r'''
package main

import (
  "encoding/json"
  "os"
  "runtime"
  "strconv"
  "sync"
  "sync/atomic"
  "time"
)

func atoi(v string) int {
  n, err := strconv.Atoi(v)
  if err != nil {
    panic(err)
  }
  return n
}

func main() {
  tasks := atoi(os.Args[1])
  sleepMs := atoi(os.Args[2])
  noopTasks := atoi(os.Args[3])
  workers := atoi(os.Args[4])
  runtime.GOMAXPROCS(workers)

  seqStart := time.Now()
  for i := 0; i < tasks; i++ {
    time.Sleep(time.Duration(sleepMs) * time.Millisecond)
  }
  seqMs := time.Since(seqStart).Milliseconds()

  parStart := time.Now()
  var wg sync.WaitGroup
  sleepSum := int64(0)
  wg.Add(tasks)
  for i := 0; i < tasks; i++ {
    go func() {
      defer wg.Done()
      time.Sleep(time.Duration(sleepMs) * time.Millisecond)
      atomic.AddInt64(&sleepSum, 1)
    }()
  }
  wg.Wait()
  parMs := time.Since(parStart).Milliseconds()

  noopStart := time.Now()
  noopSum := int64(0)
  wg.Add(noopTasks)
  for i := 0; i < noopTasks; i++ {
    go func() {
      defer wg.Done()
      atomic.AddInt64(&noopSum, 1)
    }()
  }
  wg.Wait()
  noopUs := time.Since(noopStart).Microseconds()

  speedup := 0.0
  if parMs > 0 {
    speedup = float64(seqMs) / float64(parMs)
  }
  perTask := 0.0
  if noopTasks > 0 {
    perTask = float64(noopUs) / float64(noopTasks)
  }

  result := map[string]any{
    "language": "go",
    "runtime": "goroutine",
    "status": "ok",
    "workers": workers,
    "sleep": map[string]any{
      "tasks": tasks,
      "sleep_ms": sleepMs,
      "sequential_ms": seqMs,
      "parallel_ms": parMs,
      "speedup": speedup,
      "sum": sleepSum,
    },
    "noop": map[string]any{
      "tasks": noopTasks,
      "total_us": noopUs,
      "per_task_us": perTask,
      "sum": noopSum,
    },
  }
  enc := json.NewEncoder(os.Stdout)
  if err := enc.Encode(result); err != nil {
    panic(err)
  }
}
'''


RUST_SOURCE = r'''
use std::env;
use std::time::{Duration, Instant};
use tokio::runtime::Builder;

async fn run_benchmark(tasks: usize, sleep_ms: u64, noop_tasks: usize, workers: usize) {
    let seq_start = Instant::now();
    for _ in 0..tasks {
        tokio::time::sleep(Duration::from_millis(sleep_ms)).await;
    }
    let seq_ms = seq_start.elapsed().as_millis();

    let mut sleep_handles = Vec::with_capacity(tasks);
    let par_start = Instant::now();
    for _ in 0..tasks {
        sleep_handles.push(tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(sleep_ms)).await;
            1_i64
        }));
    }
    let mut sleep_sum = 0_i64;
    for handle in sleep_handles {
        sleep_sum += handle.await.unwrap();
    }
    let par_ms = par_start.elapsed().as_millis();

    let mut noop_handles = Vec::with_capacity(noop_tasks);
    let noop_start = Instant::now();
    for _ in 0..noop_tasks {
        noop_handles.push(tokio::spawn(async { 1_i64 }));
    }
    let mut noop_sum = 0_i64;
    for handle in noop_handles {
        noop_sum += handle.await.unwrap();
    }
    let noop_us = noop_start.elapsed().as_micros();

    let speedup = if par_ms > 0 { seq_ms as f64 / par_ms as f64 } else { 0.0 };
    let per_task = if noop_tasks > 0 { noop_us as f64 / noop_tasks as f64 } else { 0.0 };
    println!(
        "{{\"language\":\"rust\",\"runtime\":\"tokio_multi_thread\",\"status\":\"ok\",\"workers\":{},\"sleep\":{{\"tasks\":{},\"sleep_ms\":{},\"sequential_ms\":{},\"parallel_ms\":{},\"speedup\":{},\"sum\":{}}},\"noop\":{{\"tasks\":{},\"total_us\":{},\"per_task_us\":{},\"sum\":{}}}}}",
        workers, tasks, sleep_ms, seq_ms, par_ms, speedup, sleep_sum, noop_tasks, noop_us, per_task, noop_sum
    );
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let tasks: usize = args[1].parse().unwrap();
    let sleep_ms: u64 = args[2].parse().unwrap();
    let noop_tasks: usize = args[3].parse().unwrap();
    let workers: usize = args[4].parse().unwrap();
    let runtime = Builder::new_multi_thread()
        .worker_threads(workers)
        .enable_time()
        .build()
        .unwrap();
    runtime.block_on(run_benchmark(tasks, sleep_ms, noop_tasks, workers));
}
'''


def run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def command_version(cmd: str) -> str:
    try:
        proc = run([cmd, "--version"], cwd=BENCHMARK_ROOT, timeout=20)
    except Exception:
        return ""
    return (proc.stdout or proc.stderr).splitlines()[0] if (proc.stdout or proc.stderr) else ""


def json_from_stdout(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}")
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise RuntimeError("benchmark did not emit a JSON object")


def host_tag() -> str:
    return f"{platform.system().lower()}-{platform.machine().lower()}"


def local_go(toolchain_dir: Path) -> Path | None:
    go = toolchain_dir / "go" / "bin" / "go"
    return go if go.exists() else None


def local_rustc(toolchain_dir: Path) -> Path | None:
    rustup_home = toolchain_dir / "rustup" / "toolchains"
    if rustup_home.exists():
        candidates = sorted(rustup_home.glob("stable-*/bin/rustc"))
        if candidates:
            return candidates[0]
    rustc = toolchain_dir / "cargo" / "bin" / "rustc"
    if rustc.exists():
        return rustc
    return None


def local_cargo(toolchain_dir: Path) -> Path | None:
    cargo = toolchain_dir / "cargo" / "bin" / "cargo"
    return cargo if cargo.exists() else None


def rust_env(toolchain_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["CARGO_HOME"] = str(toolchain_dir / "cargo")
    env["RUSTUP_HOME"] = str(toolchain_dir / "rustup")
    cargo_bin = toolchain_dir / "cargo" / "bin"
    env["PATH"] = f"{cargo_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def command_version_with_env(cmd: str, env: dict[str, str] | None = None) -> str:
    try:
        proc = run([cmd, "--version"], cwd=BENCHMARK_ROOT, env=env, timeout=20)
    except Exception:
        return ""
    return (proc.stdout or proc.stderr).splitlines()[0] if (proc.stdout or proc.stderr) else ""


def preferred_clang_tool(name: str) -> str | None:
    numbered = shutil.which(f"{name}-18")
    return numbered or shutil.which(name)


def clang_cmake_env() -> tuple[dict[str, str], str | None]:
    env = os.environ.copy()
    if not env.get("CC"):
        clang = preferred_clang_tool("clang")
        if not clang:
            return env, "Styio standard builds require clang; clang was not found"
        env["CC"] = clang
    if not env.get("CXX"):
        clangxx = preferred_clang_tool("clang++")
        if not clangxx:
            return env, "Styio standard builds require clang++; clang++ was not found"
        env["CXX"] = clangxx
    return env, None


def is_clang_cxx(compiler: str | None) -> bool:
    if not compiler:
        return False
    return "clang++" in Path(compiler).name or Path(compiler).name == "clang-cl"


def cargo_lock_version(lock_path: Path, package: str) -> str:
    if not lock_path.exists():
        return ""
    current: dict[str, str] = {}
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line == "[[package]]":
            if current.get("name") == package:
                return current.get("version", "")
            current = {}
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip().strip('"')
    if current.get("name") == package:
        return current.get("version", "")
    return ""


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response:
        with dest.open("wb") as handle:
            shutil.copyfileobj(response, handle)


def bootstrap_go(toolchain_dir: Path) -> Path | None:
    existing = local_go(toolchain_dir)
    if existing:
        return existing
    machine = platform.machine().lower()
    go_arch = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(machine)
    if platform.system().lower() != "linux" or go_arch is None:
        return None
    with urllib.request.urlopen("https://go.dev/dl/?mode=json", timeout=30) as response:
        versions = json.loads(response.read().decode("utf-8"))
    archive = None
    for version in versions:
        if not version.get("stable", False):
            continue
        for file_info in version.get("files", []):
            if file_info.get("os") == "linux" and file_info.get("arch") == go_arch and file_info.get("kind") == "archive":
                archive = file_info["filename"]
                break
        if archive:
            break
    if not archive:
        return None
    tar_path = toolchain_dir / "downloads" / archive
    if not tar_path.exists():
        download(f"https://go.dev/dl/{archive}", tar_path)
    if (toolchain_dir / "go").exists():
        shutil.rmtree(toolchain_dir / "go")
    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(toolchain_dir)
    return local_go(toolchain_dir)


def bootstrap_rustc(toolchain_dir: Path) -> Path | None:
    existing = local_rustc(toolchain_dir)
    if existing:
        return existing
    machine = platform.machine().lower()
    rust_target = {
        "x86_64": "x86_64-unknown-linux-gnu",
        "amd64": "x86_64-unknown-linux-gnu",
        "aarch64": "aarch64-unknown-linux-gnu",
        "arm64": "aarch64-unknown-linux-gnu",
    }.get(machine)
    if platform.system().lower() != "linux" or rust_target is None:
        return None
    rustup = toolchain_dir / "downloads" / "rustup-init"
    if not rustup.exists():
        download(f"https://static.rust-lang.org/rustup/dist/{rust_target}/rustup-init", rustup)
    rustup.chmod(rustup.stat().st_mode | stat.S_IXUSR)
    env = os.environ.copy()
    env["CARGO_HOME"] = str(toolchain_dir / "cargo")
    env["RUSTUP_HOME"] = str(toolchain_dir / "rustup")
    proc = run(
        [str(rustup), "-y", "--profile", "minimal", "--default-toolchain", "stable", "--no-modify-path"],
        cwd=BENCHMARK_ROOT,
        env=env,
        timeout=300,
    )
    if proc.returncode != 0:
        return None
    return local_rustc(toolchain_dir)


def unavailable(language: str, runtime: str, reason: str) -> dict[str, Any]:
    return {"language": language, "runtime": runtime, "status": "unavailable", "reason": reason}


def cmake_cache_value(build_dir: Path, key: str) -> str | None:
    cache = build_dir / "CMakeCache.txt"
    if not cache.exists():
        return None
    prefix = f"{key}:"
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(prefix) and "=" in line:
            return line.split("=", 1)[1].strip()
    return None


def relative_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def ensure_styio_release_build(build_dir: Path, styio_root: Path) -> str | None:
    cache = build_dir / "CMakeCache.txt"
    if cache.exists():
        home = cmake_cache_value(build_dir, "CMAKE_HOME_DIRECTORY")
        if home and Path(home).resolve() != styio_root.resolve():
            return f"{relative_path(build_dir, styio_root)} points at {home}, not {styio_root}"
        build_type = cmake_cache_value(build_dir, "CMAKE_BUILD_TYPE")
        if build_type and build_type != "Release":
            return (
                f"{relative_path(build_dir, styio_root)} is CMAKE_BUILD_TYPE={build_type}; "
                "use a Release build directory for async runtime benchmarks"
            )
        compiler = cmake_cache_value(build_dir, "CMAKE_CXX_COMPILER")
        if compiler and not is_clang_cxx(compiler):
            return (
                f"{relative_path(build_dir, styio_root)} uses CMAKE_CXX_COMPILER={compiler}; "
                "Styio standard async runtime benchmarks require a Clang build directory"
            )
        if build_type == "Release":
            return None

    build_dir.mkdir(parents=True, exist_ok=True)
    env, clang_error = clang_cmake_env()
    if clang_error:
        return clang_error
    proc = run(
        ["cmake", "-S", str(styio_root), "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Release"],
        cwd=styio_root,
        env=env,
        timeout=300,
    )
    if proc.returncode != 0:
        return proc.stderr.strip() or proc.stdout.strip()
    return None


def run_cpp(work_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    compiler = shutil.which(os.environ.get("CXX", "")) if os.environ.get("CXX") else None
    compiler = compiler or preferred_clang_tool("clang++") or shutil.which("g++")
    if not compiler:
        return unavailable("cpp", "cpp_stackless_coroutine", "C++ compiler not found")
    src = work_dir / "async_bench.cpp"
    exe = work_dir / "async_bench_cpp"
    src.write_text(CPP_SOURCE, encoding="utf-8")
    proc = run([compiler, "-O3", "-std=c++20", "-pthread", str(src), "-o", str(exe)], cwd=BENCHMARK_ROOT, timeout=120)
    if proc.returncode != 0:
        return unavailable("cpp", "cpp_stackless_coroutine", proc.stderr.strip())
    proc = run([str(exe), str(args.tasks), str(args.sleep_ms), str(args.noop_tasks), str(args.workers)], cwd=BENCHMARK_ROOT, timeout=120)
    result = json_from_stdout(proc)
    result["toolchain"] = command_version(compiler)
    return result


def run_go(work_dir: Path, toolchain_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    go = None
    if args.bootstrap_toolchains:
        local = bootstrap_go(toolchain_dir)
        go = str(local) if local else None
    go = go or shutil.which("go")
    if not go:
        return unavailable("go", "goroutine", "go toolchain not found")
    src = work_dir / "async_bench.go"
    exe = work_dir / "async_bench_go"
    src.write_text(GO_SOURCE, encoding="utf-8")
    proc = run([go, "build", "-o", str(exe), str(src)], cwd=BENCHMARK_ROOT, timeout=180)
    if proc.returncode != 0:
        return unavailable("go", "goroutine", proc.stderr.strip())
    proc = run([str(exe), str(args.tasks), str(args.sleep_ms), str(args.noop_tasks), str(args.workers)], cwd=BENCHMARK_ROOT, timeout=120)
    result = json_from_stdout(proc)
    version = run([go, "version"], cwd=BENCHMARK_ROOT, timeout=20)
    result["toolchain"] = version.stdout.strip()
    return result


def run_rust(work_dir: Path, toolchain_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    env = None
    cargo = None
    rustc = None
    if args.bootstrap_toolchains:
        bootstrap_rustc(toolchain_dir)
        local = local_cargo(toolchain_dir)
        cargo = str(local) if local else None
        rust_local = local_rustc(toolchain_dir)
        rustc = str(rust_local) if rust_local else None
        env = rust_env(toolchain_dir) if cargo else None
    cargo = cargo or shutil.which("cargo")
    rustc = rustc or shutil.which("rustc")
    if not cargo:
        return unavailable("rust", "tokio_multi_thread", "cargo toolchain not found")
    crate_dir = work_dir / "rust_tokio"
    src_dir = crate_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (crate_dir / "Cargo.toml").write_text(
        "\n".join(
            [
                "[package]",
                'name = "async_bench_tokio"',
                'version = "0.1.0"',
                'edition = "2021"',
                "",
                "[dependencies]",
                'tokio = { version = "1", features = ["rt-multi-thread", "time"] }',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (src_dir / "main.rs").write_text(RUST_SOURCE, encoding="utf-8")
    proc = run([cargo, "build", "--release", "--manifest-path", str(crate_dir / "Cargo.toml")], cwd=BENCHMARK_ROOT, env=env, timeout=600)
    if proc.returncode != 0:
        return unavailable("rust", "tokio_multi_thread", proc.stderr.strip())
    exe = crate_dir / "target" / "release" / "async_bench_tokio"
    proc = run([str(exe), str(args.tasks), str(args.sleep_ms), str(args.noop_tasks), str(args.workers)], cwd=BENCHMARK_ROOT, timeout=120)
    result = json_from_stdout(proc)
    rust_version = command_version_with_env(rustc, env) if rustc else ""
    cargo_version = command_version_with_env(cargo, env)
    tokio_version = cargo_lock_version(crate_dir / "Cargo.lock", "tokio")
    result["toolchain"] = "; ".join(part for part in [rust_version, cargo_version, f"tokio {tokio_version}" if tokio_version else "tokio 1"] if part)
    return result


def parse_styio_perf(stdout: str, args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {
        "language": "styio",
        "runtime": "styio_task_scheduler",
        "status": "ok",
        "workers": args.workers,
    }
    for line in stdout.splitlines():
        if line.startswith("styio_task_scheduler_perf "):
            data = dict(token.split("=", 1) for token in line.split()[1:] if "=" in token)
            result["sleep"] = {
                "tasks": int(data.get("tasks", args.tasks)),
                "sleep_ms": int(data.get("sleep_ms", args.sleep_ms)),
                "sequential_ms": int(data["sequential_ms"]),
                "parallel_ms": int(data["parallel_ms"]),
                "speedup": float(data["speedup"]),
            }
            result["workers"] = int(data.get("workers", args.workers))
        if line.startswith("styio_task_scheduler_noop "):
            data = dict(token.split("=", 1) for token in line.split()[1:] if "=" in token)
            result["noop"] = {
                "tasks": int(data["tasks"]),
                "total_us": int(data["total_us"]),
                "per_task_us": float(data["per_task_us"]),
            }
            result["workers"] = int(data.get("workers", args.workers))
    if "sleep" not in result or "noop" not in result:
        raise RuntimeError("styio perf target did not emit expected benchmark lines")
    return result


def run_styio(build_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    configure_error = ensure_styio_release_build(build_dir, args.styio_root)
    if configure_error:
        return unavailable("styio", "styio_task_scheduler", configure_error)
    proc = run(["cmake", "--build", str(build_dir), "--target", "styio_task_scheduler_perf_test", "-j2"], cwd=args.styio_root, timeout=300)
    if proc.returncode != 0:
        return unavailable("styio", "styio_task_scheduler", proc.stderr.strip())
    exe = build_dir / "bin" / "styio_task_scheduler_perf_test"
    if not exe.exists():
        return unavailable("styio", "styio_task_scheduler", f"{exe} not found")
    env = os.environ.copy()
    env["STYIO_TASK_THREADS"] = str(args.workers)
    env["STYIO_TASK_SLEEP_COUNT"] = str(args.tasks)
    env["STYIO_TASK_SLEEP_MS"] = str(args.sleep_ms)
    env["STYIO_TASK_NOOP_COUNT"] = str(args.noop_tasks)
    proc = run([str(exe), "--gtest_filter=StyioTaskSchedulerPerf.*"], cwd=args.styio_root, env=env, timeout=180)
    if proc.returncode != 0:
        return unavailable("styio", "styio_task_scheduler", proc.stderr.strip() or proc.stdout.strip())
    result = parse_styio_perf(proc.stdout, args)
    build_type = cmake_cache_value(build_dir, "CMAKE_BUILD_TYPE") or "Release"
    compiler = cmake_cache_value(build_dir, "CMAKE_CXX_COMPILER") or "C++ compiler"
    result["toolchain"] = f"repo runtime target ({build_type}; {Path(compiler).name})"
    return result


def median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def median_number(samples: list[dict[str, Any]], workload: str, metric: str) -> float:
    return median([
        float(sample[workload][metric])
        for sample in samples
        if sample.get("status") == "ok" and workload in sample and metric in sample[workload]
    ])


def summarize_ok_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    ok_samples = [sample for sample in samples if sample.get("status") == "ok"]
    if not ok_samples:
        result = dict(samples[-1]) if samples else unavailable("unknown", "unknown", "no samples")
        result["samples"] = samples
        return result
    base = ok_samples[0]
    sleep_parallel_ms = median_number(ok_samples, "sleep", "parallel_ms")
    sleep_sequential_ms = median_number(ok_samples, "sleep", "sequential_ms")
    noop_total_us = median_number(ok_samples, "noop", "total_us")
    noop_per_task_us = median_number(ok_samples, "noop", "per_task_us")
    summary = {
        "language": base.get("language", ""),
        "runtime": base.get("runtime", ""),
        "status": "ok",
        "workers": base.get("workers", 0),
        "toolchain": base.get("toolchain", ""),
        "sample_count": len(ok_samples),
        "sleep": {
            "tasks": base.get("sleep", {}).get("tasks", 0),
            "sleep_ms": base.get("sleep", {}).get("sleep_ms", 0),
            "sequential_ms": sleep_sequential_ms,
            "parallel_ms": sleep_parallel_ms,
            "speedup": sleep_sequential_ms / sleep_parallel_ms if sleep_parallel_ms > 0 else 0.0,
        },
        "noop": {
            "tasks": base.get("noop", {}).get("tasks", 0),
            "total_us": noop_total_us,
            "per_task_us": noop_per_task_us,
        },
        "samples": ok_samples,
    }
    return summary


def run_repeated(name: str, repeats: int, run_once) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    for index in range(repeats):
        sample = run_once(index)
        samples.append(sample)
    result = summarize_ok_samples(samples)
    result["repeat_policy"] = "median"
    result["requested_repeats"] = repeats
    print(f"{name} samples=" + ",".join(str(sample.get("status", "")) for sample in samples))
    return result


def flatten_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        base = {
            "language": result.get("language", ""),
            "runtime": result.get("runtime", ""),
            "status": result.get("status", ""),
        }
        if result.get("status") != "ok":
            rows.append({**base, "workload": "", "metric": "reason", "value": result.get("reason", "")})
            continue
        for workload in ("sleep", "noop"):
            payload = result.get(workload, {})
            for metric, value in payload.items():
                rows.append({**base, "workload": workload, "metric": metric, "value": value})
        payload = result.get("relative_performance", {})
        for metric, value in payload.items():
            rows.append({**base, "workload": "relative", "metric": metric, "value": value})
        if result.get("sample_count") is not None:
            rows.append({**base, "workload": "summary", "metric": "sample_count", "value": result["sample_count"]})
    return rows


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def fmt_ratio(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.2f}x"


def add_relative_performance(results: list[dict[str, Any]]) -> None:
    sleep_scores = [
        float(result["sleep"]["speedup"])
        for result in results
        if result.get("status") == "ok" and result.get("sleep", {}).get("speedup", 0) > 0
    ]
    noop_scores = [
        float(result["noop"]["per_task_us"])
        for result in results
        if result.get("status") == "ok" and result.get("noop", {}).get("per_task_us", 0) > 0
    ]
    best_sleep = max(sleep_scores) if sleep_scores else None
    best_noop = min(noop_scores) if noop_scores else None
    cpp_result = next(
        (
            result
            for result in results
            if result.get("status") == "ok"
            and result.get("runtime") == "cpp_stackless_coroutine"
        ),
        None,
    )
    cpp_sleep_speedup = float(cpp_result["sleep"]["speedup"]) if cpp_result else None
    cpp_noop_per_task = float(cpp_result["noop"]["per_task_us"]) if cpp_result else None
    for result in results:
        relative: dict[str, float] = {}
        if result.get("status") == "ok":
            sleep = result.get("sleep", {})
            noop = result.get("noop", {})
            if best_sleep and sleep.get("speedup", 0) > 0:
                relative["sleep_speedup"] = float(sleep["speedup"]) / best_sleep
            if best_noop and noop.get("per_task_us", 0) > 0:
                relative["noop_fanout"] = best_noop / float(noop["per_task_us"])
            if cpp_sleep_speedup and sleep.get("speedup", 0) > 0:
                relative["sleep_vs_cpp_stackless"] = float(sleep["speedup"]) / cpp_sleep_speedup
            if cpp_noop_per_task and noop.get("per_task_us", 0) > 0:
                relative["noop_vs_cpp_stackless"] = cpp_noop_per_task / float(noop["per_task_us"])
        if relative:
            result["relative_performance"] = relative


def write_report(out_dir: Path, metadata: dict[str, Any], results: list[dict[str, Any]]) -> None:
    add_relative_performance(results)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "results.json").write_text(json.dumps({"metadata": metadata, "results": results}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (out_dir / "benchmarks.csv").open("w", encoding="utf-8", newline="") as handle:
        fieldnames = ["language", "runtime", "status", "workload", "metric", "value"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(flatten_rows(results))

    lines = [
        "# Async Runtime Benchmark Report",
        "",
        f"- Run ID: `{metadata['run_id']}`",
        f"- Host: `{metadata['host']}`",
        f"- Case: `{metadata['case']}` ({metadata['case_description']})",
        f"- Harness: `{metadata['framework']}` over `{metadata['interface']}`",
        f"- Runtimes: `{', '.join(metadata['runtimes'])}`",
        f"- Required runtimes: `{', '.join(metadata['required_runtimes']) if metadata['required_runtimes'] else 'none'}`",
        f"- Tasks: `{metadata['tasks']}` sleep tasks x `{metadata['sleep_ms']}ms`, `{metadata['noop_tasks']}` no-op tasks, `{metadata['workers']}` workers/procs",
        f"- Repeats: `{metadata['repeats']}` per runtime, reported values are medians",
        "",
        "| Language | Runtime | Status | Samples | Sleep seq ms | Sleep parallel ms | Speedup | Sleep perf | Noop total us | Noop us/task | Noop perf | Toolchain / reason |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in results:
        sleep = result.get("sleep", {})
        noop = result.get("noop", {})
        relative = result.get("relative_performance", {})
        reason = result.get("toolchain") if result.get("status") == "ok" else result.get("reason", "")
        lines.append(
            "| "
            + " | ".join(
                [
                    str(result.get("language", "")),
                    str(result.get("runtime", "")),
                    str(result.get("status", "")),
                    fmt(result.get("sample_count")),
                    fmt(sleep.get("sequential_ms")),
                    fmt(sleep.get("parallel_ms")),
                    fmt(sleep.get("speedup")),
                    fmt_ratio(relative.get("sleep_speedup")),
                    fmt(noop.get("total_us")),
                    fmt(noop.get("per_task_us")),
                    fmt_ratio(relative.get("noop_fanout")),
                    str(reason).replace("|", "\\|"),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `sleep` measures whether the runtime actually overlaps blocked tasks; speedup near the worker count indicates real scheduling instead of eager evaluation.",
            "- `noop` measures submit/wait/release overhead for a large fanout of trivial tasks.",
            "- `Samples` records successful repeats; all table metrics are median values to avoid single-run microbenchmark noise.",
            "- `Sleep perf` and `Noop perf` normalize each workload independently; the best runtime is `1.00x`, and the others show their relative performance against that best result.",
            "- The runner is intentionally pytest-compatible: each runtime is a subprocess black box, and pytest can assert the generated JSON/CSV contract without embedding language-specific unit-test frameworks.",
            "- C++ uses C++20 stackless coroutine frames with `co_await` and a small scheduler built with Clang by default, Go uses goroutines with `GOMAXPROCS`, Rust uses Tokio's multi-thread runtime, and Styio uses the repository task scheduler target.",
            "- `--bootstrap-toolchains` installs missing Go/Rust toolchains under the build directory; this keeps comparison runs reproducible on machines without system Go or Rust.",
            "",
        ]
    )
    styio_result = next((result for result in results if result.get("runtime") == "styio_task_scheduler" and result.get("status") == "ok"), None)
    cpp_result = next((result for result in results if result.get("runtime") == "cpp_stackless_coroutine" and result.get("status") == "ok"), None)
    if styio_result and cpp_result:
        styio_relative = styio_result.get("relative_performance", {})
        lines.extend(
            [
                "## C++ Stackless Parity",
                "",
                f"- Styio no-op vs C++ stackless coroutine: `{fmt_ratio(styio_relative.get('noop_vs_cpp_stackless'))}` (`{fmt(styio_result['noop'].get('per_task_us'))}` us/task vs `{fmt(cpp_result['noop'].get('per_task_us'))}` us/task).",
                f"- Styio sleep overlap vs C++ stackless coroutine: `{fmt_ratio(styio_relative.get('sleep_vs_cpp_stackless'))}` (`{fmt(styio_result['sleep'].get('speedup'))}` speedup vs `{fmt(cpp_result['sleep'].get('speedup'))}`).",
                "",
            ]
        )
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Styio async runtime benchmarks against C++ stackless coroutine, Go goroutine, and Rust Tokio baselines.")
    parser.add_argument("--styio-root", default=str(default_styio_root()), help="Path to a Styio source checkout that provides the benchmark probe targets. Defaults to STYIO_ROOT or ../styio.")
    parser.add_argument("--build-dir", default=DEFAULT_STYIO_BUILD_DIR, help="Release CMake build directory for Styio runtime target.")
    parser.add_argument("--out-dir", default="", help="Output directory. Defaults to async-runtime/reports/<run-id> in this benchmark repository.")
    parser.add_argument("--case", choices=[*BENCHMARK_CASES.keys(), "custom"], default=DEFAULT_CASE, help="Benchmark case preset. Use custom with explicit sizing flags.")
    parser.add_argument("--tasks", type=int, default=None)
    parser.add_argument("--sleep-ms", type=int, default=None)
    parser.add_argument("--noop-tasks", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--repeats", type=int, default=None, help="Run each runtime this many times and report medians.")
    parser.add_argument("--runtime", action="append", choices=RUNTIME_CHOICES, dest="runtimes", help="Runtime to run. Repeat to select multiple runtimes. Defaults to all.")
    parser.add_argument("--require-runtime", action="append", choices=RUNTIME_CHOICES, default=[], dest="required_runtimes", help="Fail the route if this runtime is unavailable or reports a failed status. Repeat as needed.")
    parser.add_argument("--bootstrap-toolchains", action="store_true", help="Install missing Go/Rust toolchains under the build directory.")
    args = parser.parse_args()
    resolve_benchmark_args(args)
    return args


def unique_preserving_order(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def resolve_benchmark_args(args: argparse.Namespace) -> None:
    args.styio_root = Path(args.styio_root).expanduser().resolve()
    if not (args.styio_root / "CMakeLists.txt").exists():
        raise SystemExit(f"--styio-root does not look like a Styio source checkout: {args.styio_root}")
    case = BENCHMARK_CASES[DEFAULT_CASE] if args.case == "custom" else BENCHMARK_CASES[args.case]
    args.case_description = "custom sizing over the baseline benchmark contract" if args.case == "custom" else case.description
    args.tasks = args.tasks if args.tasks is not None else case.tasks
    args.sleep_ms = args.sleep_ms if args.sleep_ms is not None else case.sleep_ms
    args.noop_tasks = args.noop_tasks if args.noop_tasks is not None else case.noop_tasks
    args.workers = args.workers if args.workers is not None else case.workers
    args.repeats = args.repeats if args.repeats is not None else case.repeats
    args.runtimes = unique_preserving_order(args.runtimes or RUNTIME_CHOICES)
    args.required_runtimes = unique_preserving_order(args.required_runtimes)
    if args.tasks < 1:
        raise SystemExit("--tasks must be >= 1")
    if args.sleep_ms < 0:
        raise SystemExit("--sleep-ms must be >= 0")
    if args.noop_tasks < 1:
        raise SystemExit("--noop-tasks must be >= 1")
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.repeats < 1:
        raise SystemExit("--repeats must be >= 1")
    missing_required = [runtime for runtime in args.required_runtimes if runtime not in args.runtimes]
    if missing_required:
        raise SystemExit("--require-runtime must also be selected with --runtime: " + ", ".join(missing_required))


def required_runtime_failures(results: list[dict[str, Any]], required_runtimes: list[str]) -> list[str]:
    by_key = {result.get("runtime_key", ""): result for result in results}
    failures: list[str] = []
    for runtime in required_runtimes:
        result = by_key.get(runtime)
        if not result:
            failures.append(f"{runtime}: not selected")
            continue
        if result.get("status") != "ok":
            reason = result.get("reason") or f"status={result.get('status', '')}"
            failures.append(f"{runtime}: {reason}")
    return failures


def main() -> int:
    args = parse_args()
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-async-runtime"
    out_dir = Path(args.out_dir) if args.out_dir else BENCHMARK_ROOT / "async-runtime" / "reports" / run_id
    if not out_dir.is_absolute():
        out_dir = BENCHMARK_ROOT / out_dir
    build_dir = Path(args.build_dir)
    if not build_dir.is_absolute():
        build_dir = args.styio_root / build_dir
    work_dir = build_dir / "async-runtime-work" / run_id
    work_dir.mkdir(parents=True, exist_ok=True)
    toolchain_dir = build_dir / "async-runtime-toolchains"

    metadata = {
        "run_id": run_id,
        "host": host_tag(),
        "benchmark_root": str(BENCHMARK_ROOT),
        "styio_root": str(args.styio_root),
        "styio_build_dir": str(build_dir),
        "framework": "pytest-compatible black-box runner",
        "interface": "subprocess",
        "case": args.case,
        "case_description": args.case_description,
        "runtimes": args.runtimes,
        "required_runtimes": args.required_runtimes,
        "tasks": args.tasks,
        "sleep_ms": args.sleep_ms,
        "noop_tasks": args.noop_tasks,
        "workers": args.workers,
        "repeats": args.repeats,
        "bootstrap_toolchains": args.bootstrap_toolchains,
    }
    runners = {
        "styio": lambda: run_repeated("styio", args.repeats, lambda index: run_styio(build_dir, args)),
        "cpp": lambda: run_repeated("cpp", args.repeats, lambda index: run_cpp(work_dir / f"cpp-{index}", args)),
        "go": lambda: run_repeated("go", args.repeats, lambda index: run_go(work_dir / f"go-{index}", toolchain_dir, args)),
        "rust": lambda: run_repeated("rust", args.repeats, lambda index: run_rust(work_dir / f"rust-{index}", toolchain_dir, args)),
    }
    results = []
    for runtime_key in args.runtimes:
        result = runners[runtime_key]()
        result["runtime_key"] = runtime_key
        results.append(result)
    write_report(out_dir, metadata, results)
    print(out_dir)
    for result in results:
        print(f"{result.get('runtime_key')} {result.get('language')} {result.get('runtime')} {result.get('status')}")
    failures = required_runtime_failures(results, args.required_runtimes)
    if failures:
        for failure in failures:
            print(f"required runtime failed: {failure}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
