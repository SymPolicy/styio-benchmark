#pragma once
#ifndef STYIO_BENCH_INTERNAL_BENCH_UTILS_HPP_
#define STYIO_BENCH_INTERNAL_BENCH_UTILS_HPP_

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <string>
#include <vector>

namespace styio::bench {

struct BenchmarkSample {
  std::string phase;       // "lex", "parse", "sema", "type", "topology", "diag", "runtime", ...
  std::string label;       // descriptive sub-label
  int64_t duration_ns = 0;
  int64_t alloc_count = 0; // 0 if not measured

  // Route cache counters (TASK-06): non-zero when captured.
  int64_t route_cache_scan_count = 0;
  int64_t route_cache_hit_count = 0;
  int64_t route_cache_miss_count = 0;
  int64_t route_cache_disabled_count = 0;

  // IR allocation counters captured from SessionAllocationStats.
  int64_t ir_arena_allocations = 0;
  int64_t ir_raw_allocations = 0;
  int64_t ir_bytes_allocated = 0;
  int64_t ir_node_count = 0;
  int64_t ir_max_node_count = 0;
  int64_t ir_destructor_calls = 0;

  // Task scheduler metadata/counters captured from the runtime profile snapshot.
  int64_t task_scheduler_queue_kind = -1; // ReadyQueueKind: 1=BoundedWait
  int64_t task_scheduler_worker_count = 0;
  int64_t task_scheduler_queue_capacity = 0;
  int64_t task_scheduler_queue_current_depth = 0;
  int64_t task_scheduler_queue_peak_depth = 0;
  int64_t task_scheduler_queue_accepted_pushes = 0;
  int64_t task_scheduler_queue_pops = 0;
  int64_t task_scheduler_queue_pressure_events = 0;
  int64_t task_scheduler_queue_producer_waits = 0;
  int64_t task_scheduler_queue_consumer_waits = 0;
  int64_t task_scheduler_queue_close_wake_ups = 0;
  int64_t task_scheduler_queue_closed = 0;

  // Conditional resource-typestate dataflow counters.
  int64_t resource_typestate_branch_snapshot_count = 0;
  int64_t resource_typestate_join_count = 0;
  int64_t resource_typestate_fact_insertion_count = 0;
  int64_t resource_typestate_peak_temporary_fact_slots = 0;

  // Deterministic finite zip-barrier metadata counters.
  int64_t zip_barrier_fact_bundle_count = 0;
  int64_t zip_barrier_fact_valid_count = 0;
  int64_t zip_barrier_fact_metadata_bytes = 0;
};

struct BenchmarkResult {
  std::string schema = "styio.benchmark.v1";
  std::string git_sha;
  std::string build_type;
  int64_t timestamp = 0;
  std::vector<BenchmarkSample> samples;

  std::string to_json() const;
};

// Timer that records elapsed time in nanoseconds.
class Timer {
public:
  using Clock = std::chrono::steady_clock;
  using TimePoint = Clock::time_point;

  Timer() : start_(Clock::now()) {}

  void reset() { start_ = Clock::now(); }

  // Elapsed in nanoseconds.
  int64_t elapsed_ns() const {
    auto end = Clock::now();
    return std::chrono::duration_cast<std::chrono::nanoseconds>(end - start_).count();
  }

  // Elapsed in microseconds.
  int64_t elapsed_us() const {
    auto end = Clock::now();
    return std::chrono::duration_cast<std::chrono::microseconds>(end - start_).count();
  }

private:
  TimePoint start_;
};

// Run a benchmark function multiple times and return a sample with median time.
// `warmup` iterations are run first and discarded.
// `measured` iterations are timed; the median is used for the sample.
template <typename F>
BenchmarkSample run_benchmark(
    std::string phase,
    std::string label,
    int warmup,
    int measured,
    F&& func) {
  // Warmup
  for (int i = 0; i < warmup; ++i) {
    func();
  }

  // Measured iterations
  std::vector<int64_t> times;
  times.reserve(measured);
  for (int i = 0; i < measured; ++i) {
    Timer t;
    func();
    times.push_back(t.elapsed_ns());
  }

  // Median
  std::sort(times.begin(), times.end());
  int64_t median_ns = times[times.size() / 2];

  BenchmarkSample sample;
  sample.phase = std::move(phase);
  sample.label = std::move(label);
  sample.duration_ns = median_ns;
  return sample;
}

// Simple inline JSON serialization (no third-party JSON lib dependency).
inline std::string BenchmarkResult::to_json() const {
  std::string out;
  out.reserve(4096);
  out += "{\n";
  out += "  \"schema\": \"" + schema + "\",\n";
  out += "  \"git_sha\": \"" + git_sha + "\",\n";
  out += "  \"build_type\": \"" + build_type + "\",\n";
  out += "  \"timestamp\": " + std::to_string(timestamp) + ",\n";
  out += "  \"samples\": [\n";
  for (size_t i = 0; i < samples.size(); ++i) {
    const auto& s = samples[i];
    out += "    {";
    out += "\"phase\": \"" + s.phase + "\", ";
    out += "\"label\": \"" + s.label + "\", ";
    out += "\"duration_ns\": " + std::to_string(s.duration_ns);
    if (s.alloc_count > 0) {
      out += ", \"alloc_count\": " + std::to_string(s.alloc_count);
    }
    const bool is_route_cache_sample = s.phase == "route_cache";
    if (is_route_cache_sample || s.route_cache_scan_count > 0) {
      out += ", \"route_cache_scan_count\": " + std::to_string(s.route_cache_scan_count);
    }
    if (is_route_cache_sample || s.route_cache_hit_count > 0) {
      out += ", \"route_cache_hit_count\": " + std::to_string(s.route_cache_hit_count);
    }
    if (is_route_cache_sample || s.route_cache_miss_count > 0) {
      out += ", \"route_cache_miss_count\": " + std::to_string(s.route_cache_miss_count);
    }
    if (is_route_cache_sample || s.route_cache_disabled_count > 0) {
      out += ", \"route_cache_disabled_count\": " + std::to_string(s.route_cache_disabled_count);
    }
    const bool is_ir_alloc_sample = s.phase == "ir_alloc";
    if (is_ir_alloc_sample || s.ir_arena_allocations > 0) {
      out += ", \"ir_arena_allocations\": " + std::to_string(s.ir_arena_allocations);
    }
    if (is_ir_alloc_sample || s.ir_raw_allocations > 0) {
      out += ", \"ir_raw_allocations\": " + std::to_string(s.ir_raw_allocations);
    }
    if (is_ir_alloc_sample || s.ir_bytes_allocated > 0) {
      out += ", \"ir_bytes_allocated\": " + std::to_string(s.ir_bytes_allocated);
    }
    if (is_ir_alloc_sample || s.ir_node_count > 0) {
      out += ", \"ir_node_count\": " + std::to_string(s.ir_node_count);
    }
    if (is_ir_alloc_sample || s.ir_max_node_count > 0) {
      out += ", \"ir_max_node_count\": " + std::to_string(s.ir_max_node_count);
    }
    if (is_ir_alloc_sample || s.ir_destructor_calls > 0) {
      out += ", \"ir_destructor_calls\": " + std::to_string(s.ir_destructor_calls);
    }
    const bool is_scheduler_sample = s.phase == "scheduler";
    if (is_scheduler_sample || s.task_scheduler_queue_kind >= 0) {
      out += ", \"task_scheduler_queue_kind\": " + std::to_string(s.task_scheduler_queue_kind);
    }
    if (is_scheduler_sample || s.task_scheduler_worker_count > 0) {
      out += ", \"task_scheduler_worker_count\": " + std::to_string(s.task_scheduler_worker_count);
    }
    if (is_scheduler_sample) {
      out += ", \"task_scheduler_queue_capacity\": "
             + std::to_string(s.task_scheduler_queue_capacity);
      out += ", \"task_scheduler_queue_current_depth\": "
             + std::to_string(s.task_scheduler_queue_current_depth);
      out += ", \"task_scheduler_queue_peak_depth\": "
             + std::to_string(s.task_scheduler_queue_peak_depth);
      out += ", \"task_scheduler_queue_accepted_pushes\": "
             + std::to_string(s.task_scheduler_queue_accepted_pushes);
      out += ", \"task_scheduler_queue_pops\": "
             + std::to_string(s.task_scheduler_queue_pops);
      out += ", \"task_scheduler_queue_pressure_events\": "
             + std::to_string(s.task_scheduler_queue_pressure_events);
      out += ", \"task_scheduler_queue_producer_waits\": "
             + std::to_string(s.task_scheduler_queue_producer_waits);
      out += ", \"task_scheduler_queue_consumer_waits\": "
             + std::to_string(s.task_scheduler_queue_consumer_waits);
      out += ", \"task_scheduler_queue_close_wake_ups\": "
             + std::to_string(s.task_scheduler_queue_close_wake_ups);
      out += ", \"task_scheduler_queue_closed\": "
             + std::to_string(s.task_scheduler_queue_closed);
    }
    const bool is_resource_typestate_sample = s.phase == "resource_typestate";
    if (is_resource_typestate_sample
        || s.resource_typestate_branch_snapshot_count > 0) {
      out += ", \"resource_typestate_branch_snapshot_count\": "
             + std::to_string(s.resource_typestate_branch_snapshot_count);
    }
    if (is_resource_typestate_sample || s.resource_typestate_join_count > 0) {
      out += ", \"resource_typestate_join_count\": "
             + std::to_string(s.resource_typestate_join_count);
    }
    if (is_resource_typestate_sample
        || s.resource_typestate_fact_insertion_count > 0) {
      out += ", \"resource_typestate_fact_insertion_count\": "
             + std::to_string(s.resource_typestate_fact_insertion_count);
    }
    if (is_resource_typestate_sample
        || s.resource_typestate_peak_temporary_fact_slots > 0) {
      out += ", \"resource_typestate_peak_temporary_fact_slots\": "
             + std::to_string(
                 s.resource_typestate_peak_temporary_fact_slots);
    }
    const bool is_zip_barrier_fact_sample = s.phase == "zip_barrier_facts";
    if (is_zip_barrier_fact_sample || s.zip_barrier_fact_bundle_count > 0) {
      out += ", \"zip_barrier_fact_bundle_count\": "
             + std::to_string(s.zip_barrier_fact_bundle_count);
    }
    if (is_zip_barrier_fact_sample || s.zip_barrier_fact_valid_count > 0) {
      out += ", \"zip_barrier_fact_valid_count\": "
             + std::to_string(s.zip_barrier_fact_valid_count);
    }
    if (is_zip_barrier_fact_sample || s.zip_barrier_fact_metadata_bytes > 0) {
      out += ", \"zip_barrier_fact_metadata_bytes\": "
             + std::to_string(s.zip_barrier_fact_metadata_bytes);
    }
    out += "}";
    if (i + 1 < samples.size()) out += ",";
    out += "\n";
  }
  out += "  ]\n";
  out += "}\n";
  return out;
}

} // namespace styio::bench

#endif // STYIO_BENCH_INTERNAL_BENCH_UTILS_HPP_
