#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include "gtest/gtest.h"

#include "StyioExtern/ExternLib.hpp"

namespace {

using Clock = std::chrono::steady_clock;

constexpr int kTaskCount = 4;
constexpr int kSleepPerTaskMs = 160;
constexpr int kWorkerCount = 4;

int
env_int(const char* name, int fallback, int min_value, int max_value) {
  if (const char* raw = std::getenv(name)) {
    char* end = nullptr;
    const long parsed = std::strtol(raw, &end, 10);
    if (end != raw && *end == '\0' && parsed >= min_value && parsed <= max_value) {
      return static_cast<int>(parsed);
    }
  }
  return fallback;
}

void
set_task_threads(int workers) {
  const std::string value = std::to_string(workers);
  setenv("STYIO_TASK_THREADS", value.c_str(), 1);
}

int64_t
sleep_i64_task(void* raw_config) {
  const auto sleep_ms = raw_config ? *static_cast<const int*>(raw_config) : kSleepPerTaskMs;
  std::this_thread::sleep_for(std::chrono::milliseconds(sleep_ms));
  return 1;
}

int64_t
noop_i64_task(void*) {
  return 1;
}

long long
elapsed_ms(const Clock::time_point& start, const Clock::time_point& end) {
  return std::chrono::duration_cast<std::chrono::milliseconds>(end - start).count();
}

long long
elapsed_us(const Clock::time_point& start, const Clock::time_point& end) {
  return std::chrono::duration_cast<std::chrono::microseconds>(end - start).count();
}

}  // namespace

TEST(StyioTaskSchedulerPerf, SleepTasksRunConcurrently) {
  const int task_count = env_int("STYIO_TASK_SLEEP_COUNT", kTaskCount, 1, 1000);
  int sleep_ms = env_int("STYIO_TASK_SLEEP_MS", kSleepPerTaskMs, 0, 10000);
  const int workers = env_int("STYIO_TASK_THREADS", kWorkerCount, 1, 1024);
  set_task_threads(workers);
  styio_runtime_clear_error();

  const auto seq_start = Clock::now();
  int64_t sequential_sum = 0;
  for (int i = 0; i < task_count; ++i) {
    sequential_sum += sleep_i64_task(&sleep_ms);
  }
  const auto seq_ms = elapsed_ms(seq_start, Clock::now());

  std::vector<int64_t> handles;
  handles.reserve(static_cast<std::size_t>(task_count));
  const auto parallel_start = Clock::now();
  for (int i = 0; i < task_count; ++i) {
    auto* sleep_ctx = static_cast<int*>(std::malloc(sizeof(int)));
    ASSERT_NE(sleep_ctx, nullptr);
    *sleep_ctx = sleep_ms;
    // The task runtime owns and frees non-null task context after executing it.
    handles.push_back(styio_task_i64_spawn(&sleep_i64_task, sleep_ctx));
  }

  int64_t parallel_sum = 0;
  for (const int64_t handle : handles) {
    parallel_sum += styio_task_i64_pull(handle);
  }
  const auto parallel_ms = elapsed_ms(parallel_start, Clock::now());

  for (const int64_t handle : handles) {
    styio_task_release(handle);
  }

  EXPECT_EQ(sequential_sum, task_count);
  EXPECT_EQ(parallel_sum, task_count);
  EXPECT_GE(styio_task_worker_count(), workers);
  EXPECT_EQ(styio_task_active_count(), 0);
  EXPECT_EQ(styio_runtime_has_error(), 0);

  std::cout << "styio_task_scheduler_perf tasks=" << task_count
            << " sleep_ms=" << sleep_ms
            << " sequential_ms=" << seq_ms
            << " parallel_ms=" << parallel_ms
            << " speedup="
            << (parallel_ms > 0 ? static_cast<double>(seq_ms) / static_cast<double>(parallel_ms) : 0.0)
            << " workers=" << styio_task_worker_count() << "\n";

  EXPECT_LT(parallel_ms, seq_ms * 70 / 100)
    << "sequential_ms=" << seq_ms << " parallel_ms=" << parallel_ms;
}

TEST(StyioTaskSchedulerPerf, NoopFanoutOverhead) {
  const int workers = env_int("STYIO_TASK_THREADS", kWorkerCount, 1, 1024);
  set_task_threads(workers);
  styio_runtime_clear_error();

  const int task_count = env_int("STYIO_TASK_NOOP_COUNT", 10000, 1, 999999);

  std::vector<int64_t> handles;
  handles.reserve(static_cast<std::size_t>(task_count));
  const auto start = Clock::now();
  for (int i = 0; i < task_count; ++i) {
    handles.push_back(styio_task_i64_spawn(&noop_i64_task, nullptr));
  }

  int64_t sum = 0;
  for (const int64_t handle : handles) {
    sum += styio_task_i64_pull(handle);
  }
  const auto total_us = elapsed_us(start, Clock::now());

  for (const int64_t handle : handles) {
    styio_task_release(handle);
  }

  EXPECT_EQ(sum, task_count);
  EXPECT_GE(styio_task_worker_count(), workers);
  EXPECT_EQ(styio_task_active_count(), 0);
  EXPECT_EQ(styio_runtime_has_error(), 0);

  const double per_task_us = static_cast<double>(total_us) / static_cast<double>(task_count);
  std::cout << "styio_task_scheduler_noop tasks=" << task_count
            << " total_us=" << total_us
            << " per_task_us=" << per_task_us
            << " workers=" << styio_task_worker_count() << "\n";
}
