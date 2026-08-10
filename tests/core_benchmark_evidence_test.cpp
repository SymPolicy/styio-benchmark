#include <gtest/gtest.h>

#include "../styio-probes/bench_utils.hpp"
#include "StyioIR/StyioIR.hpp"
#include "StyioSession/SessionAllocation.hpp"

namespace {

class BenchmarkEvidenceIrNode final : public StyioIR
{
public:
  std::string toString(StyioRepr*, int = 0) override { return "benchmark-evidence"; }
  llvm::Type* toLLVMType(StyioToLLVM*) override { return nullptr; }
  llvm::Value* toLLVMIR(StyioToLLVM*) override { return nullptr; }
  bool is_active() const override { return true; }
};

} // namespace

TEST(StyioBenchmarkEvidence, SerializesRouteCacheAndIrAllocationFields) {
  styio::bench::BenchmarkResult result;
  result.git_sha = "deadbeef";
  result.build_type = "Release";
  result.timestamp = 123456;

  styio::bench::BenchmarkSample route_cache;
  route_cache.phase = "route_cache";
  route_cache.label = "name_resolution_5k";
  route_cache.duration_ns = 0;
  route_cache.route_cache_scan_count = 5001;
  route_cache.route_cache_hit_count = 42;
  route_cache.route_cache_miss_count = 4959;
  route_cache.route_cache_disabled_count = 0;
  result.samples.push_back(route_cache);

  styio::session_alloc::SessionAllocationStats stats;
  auto* previous_stats = styio::session_alloc::set_current_ir_stats(&stats);
  EXPECT_FALSE(styio::session_alloc::ir_arena_active());
  auto* node = styio::session_alloc::make_ir<BenchmarkEvidenceIrNode>();
  ASSERT_EQ(stats.arena_allocations, 0u);
  delete node;
  styio::session_alloc::set_current_ir_stats(previous_stats);
  ASSERT_GT(stats.raw_allocations, 0u);
  ASSERT_GT(stats.bytes_allocated, 0u);
  ASSERT_GT(stats.node_count, 0u);
  ASSERT_GT(stats.destructor_calls, 0u);

  styio::bench::BenchmarkSample ir_sample;
  ir_sample.phase = "ir_alloc";
  ir_sample.label = "sg_const_int_factory";
  ir_sample.duration_ns = 123;
  ir_sample.ir_arena_allocations = static_cast<int64_t>(stats.arena_allocations);
  ir_sample.ir_raw_allocations = static_cast<int64_t>(stats.raw_allocations);
  ir_sample.ir_bytes_allocated = static_cast<int64_t>(stats.bytes_allocated);
  ir_sample.ir_node_count = static_cast<int64_t>(stats.node_count);
  ir_sample.ir_max_node_count = static_cast<int64_t>(stats.max_node_count);
  ir_sample.ir_destructor_calls = static_cast<int64_t>(stats.destructor_calls);
  result.samples.push_back(ir_sample);

  const std::string json = result.to_json();
  EXPECT_NE(json.find("\"schema\": \"styio.benchmark.v1\""), std::string::npos);
  EXPECT_NE(json.find("\"route_cache_scan_count\": 5001"), std::string::npos);
  EXPECT_NE(json.find("\"route_cache_hit_count\": 42"), std::string::npos);
  EXPECT_NE(json.find("\"route_cache_miss_count\": 4959"), std::string::npos);
  EXPECT_NE(json.find("\"phase\": \"ir_alloc\""), std::string::npos);
  EXPECT_NE(json.find("\"ir_arena_allocations\": 0"), std::string::npos);
  EXPECT_NE(json.find("\"ir_raw_allocations\": " + std::to_string(stats.raw_allocations)), std::string::npos);
  EXPECT_NE(json.find("\"ir_bytes_allocated\": " + std::to_string(stats.bytes_allocated)), std::string::npos);
  EXPECT_NE(json.find("\"ir_node_count\": " + std::to_string(stats.node_count)), std::string::npos);
  EXPECT_NE(json.find("\"ir_max_node_count\": " + std::to_string(stats.max_node_count)), std::string::npos);
  EXPECT_NE(json.find("\"ir_destructor_calls\": " + std::to_string(stats.destructor_calls)), std::string::npos);
}
