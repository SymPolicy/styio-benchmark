/*
  styio_core_bench - in-repo C++ benchmark binary.
  Uses std::chrono::steady_clock, outputs JSON to stdout or file.
  No third-party dependencies beyond the compiler itself.
*/

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "bench_utils.hpp"

// [Styio]
#include "StyioAST/AST.hpp"
#include "StyioException/Exception.hpp"
#include "StyioIR/GenIR/GenIR.hpp"
#include "StyioIR/StyioIR.hpp"
#include "StyioLowering/AstToStyioIRLowerer.hpp"
#include "StyioLowering/AstToStyioIRStage.hpp"
#include "StyioLowering/StyioIROptimizer.hpp"
#include "StyioParser/Parser.hpp"
#include "StyioParser/Tokenizer.hpp"
#include "StyioResourceTopology/ResourceTopology.hpp"
#include "StyioRuntime/ReadyQueue.hpp"
#include "StyioSema/SemanticAnalysis.hpp"
#include "StyioSession/CompilationSession.hpp"
#include "StyioExtern/ExternLib.hpp"

namespace {

#if defined(_WIN32)
#define styio_bench_popen _popen
#define styio_bench_pclose _pclose
constexpr const char* kGitShaCommand = "git rev-parse --short HEAD 2>nul";
#else
#define styio_bench_popen popen
#define styio_bench_pclose pclose
constexpr const char* kGitShaCommand = "git rev-parse --short HEAD 2>/dev/null";
#endif

// -------------------------------------------------------------------
// Synthetic source generators
// -------------------------------------------------------------------

std::string gen_many_bindings(int count) {
  std::ostringstream os;
  for (int i = 0; i < count; ++i)
    os << "x_" << i << " := " << (i * 7 % 1000) << ";\n";
  return os.str();
}

// Flat binop chain: x := 1 + 2 + 3 + ... + N;
std::string gen_deep_expr(int depth) {
  std::ostringstream os;
  os << "x := 1";
  for (int i = 2; i <= depth; ++i)
    os << " + " << i;
  os << ";\n";
  return os.str();
}

std::string gen_flat_add_expression(int operand_count) {
  std::ostringstream os;
  os << "1";
  for (int i = 1; i < operand_count; ++i)
    os << " + 1";
  return os.str();
}

std::string gen_mixed_expression(int operand_count) {
  std::ostringstream os;
  os << "1";
  for (int i = 1; i < operand_count; ++i)
    os << (i % 2 == 0 ? " + 1" : " * 1");
  return os.str();
}

std::string gen_power_expression(int operand_count) {
  std::ostringstream os;
  os << "2";
  for (int i = 1; i < operand_count; ++i)
    os << " ** 2";
  return os.str();
}

std::string gen_large_file(int lines) {
  std::ostringstream os;
  for (int i = 0; i < lines; ++i) {
    if (i % 5 == 0)
      os << "// comment " << i << "\n";
    else if (i % 7 == 0)
      os << "\n";
    else
      os << "v_" << i << " := " << (i % 100) << ";\n";
  }
  return os.str();
}

std::string gen_many_names(int count) {
  std::ostringstream os;
  for (int i = 0; i < count; ++i)
    os << "n_" << i << " := " << i << ";\n";
  os << "sum : i64 := 0;\n";
  for (int i = 0; i < count; ++i) {
    os << "sum := sum + n_" << i << ";\n";
  }
  return os.str();
}

std::string gen_typed_bindings(int count) {
  std::ostringstream os;
  for (int i = 0; i < count; ++i)
    os << "t_" << i << " : i64 := " << (i * 3) << ";\n";
  for (int i = 0; i < count / 5; ++i)
    os << "c_" << i << " : i64 := t_" << i << " + t_" << (i + count/2) << ";\n";
  return os.str();
}

std::string gen_resource_ops(int count) {
  std::ostringstream os;
  for (int i = 0; i < count; ++i) {
    os << "@resource r_" << i << " : file {\n"
       << "  path := \"/tmp/f_" << i << "\";\n"
       << "  @method write { push(\"data_" << i << "\"); }\n"
       << "}\n";
  }
  return os.str();
}

std::string gen_error_source(int count) {
  std::ostringstream os;
  for (int i = 0; i < count; ++i) {
    if (i % 3 == 0)
      os << "bad_" << i << " := undefined_func_" << i << "();\n";
    else if (i % 3 == 1)
      os << "bad_" << i << " : i64 := \"not_an_int\";\n";
    else
      os << "bad_" << i << " := 1 + ;\n";
  }
  return os.str();
}

std::string gen_callable_constraint_fanout(int constraint_count) {
  std::ostringstream os;
  os << "# callable_worklist := (value) => value";
  for (int index = 0; index < constraint_count; ++index) {
    os << " + value";
  }
  os << "\n>_(callable_worklist(1))\n";
  return os.str();
}

// -------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------

class BenchmarkIrNode final : public StyioIR
{
public:
  std::string toString(StyioRepr*, int = 0) override { return "benchmark-ir-node"; }
  llvm::Type* toLLVMType(StyioToLLVM*) override { return nullptr; }
  llvm::Value* toLLVMIR(StyioToLLVM*) override { return nullptr; }
  bool is_active() const override { return true; }
};

int bench_iters(const char* env_name, int default_val) {
  const char* v = std::getenv(env_name);
  if (!v) return default_val;
  int p = std::atoi(v);
  return p > 0 ? p : default_val;
}

std::string get_git_sha() {
  FILE* fp = styio_bench_popen(kGitShaCommand, "r");
  if (!fp) return "unknown";
  char buf[64] = {};
  if (!fgets(buf, sizeof(buf), fp)) { styio_bench_pclose(fp); return "unknown"; }
  styio_bench_pclose(fp);
  std::string s(buf);
  while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back();
  return s;
}

std::string get_build_type() {
#ifdef NDEBUG
  return "Release";
#else
  return "Debug";
#endif
}

std::vector<std::pair<std::size_t, std::size_t>>
build_line_seps(const std::string& text) {
  std::vector<std::pair<std::size_t, std::size_t>> seps;
  std::size_t start = 0;
  for (std::size_t i = 0; i < text.size(); ++i) {
    if (text[i] == '\n') {
      seps.emplace_back(start, i - start);
      start = i + 1;
    }
  }
  if (start <= text.size())
    seps.emplace_back(start, text.size() - start);
  return seps;
}

StyioContext* make_context(
    const std::string& fname,
    const std::string& code,
    std::vector<StyioToken*>& tokens) {
  return StyioContext::Create(fname, code, build_line_seps(code), tokens, false);
}

/// Safely parse — catches and discards exceptions for benchmarking.
MainBlockAST* safe_parse(StyioContext& ctx) {
  try {
    return parse_main_block_with_engine_latest(
        ctx, StyioParserEngine::Nightly, nullptr);
  } catch (const StyioBaseException&) {
    return nullptr;
  }
}

} // anonymous namespace

namespace {

/// Helper: capture route cache counters from a StyioContext into a BenchmarkSample.
styio::bench::BenchmarkSample capture_route_cache_sample(
    const StyioContext& ctx, const std::string& label) {
  styio::bench::BenchmarkSample s;
  s.phase = "route_cache";
  s.label = label;
  s.route_cache_scan_count = static_cast<int64_t>(ctx.route_scan_count());
  s.route_cache_hit_count = static_cast<int64_t>(ctx.route_cache_hit_count());
  s.route_cache_miss_count = static_cast<int64_t>(ctx.route_cache_miss_count());
  s.route_cache_disabled_count = static_cast<int64_t>(ctx.route_cache_disabled_count());
  return s;
}

/// Helper: copy IR allocation statistics into a BenchmarkSample.
void capture_ir_allocation_stats(
    styio::bench::BenchmarkSample& sample,
    const styio::session_alloc::SessionAllocationStats& stats) {
  sample.ir_arena_allocations = static_cast<int64_t>(stats.arena_allocations);
  sample.ir_raw_allocations = static_cast<int64_t>(stats.raw_allocations);
  sample.ir_bytes_allocated = static_cast<int64_t>(stats.bytes_allocated);
  sample.ir_node_count = static_cast<int64_t>(stats.node_count);
  sample.ir_max_node_count = static_cast<int64_t>(stats.max_node_count);
  sample.ir_destructor_calls = static_cast<int64_t>(stats.destructor_calls);
}

styio::bench::BenchmarkSample capture_ir_allocation_sample(const std::string& label) {
  styio::session_alloc::SessionAllocationStats stats;
  auto* previous_stats = styio::session_alloc::set_current_ir_stats(&stats);
  auto* node = styio::session_alloc::make_ir<BenchmarkIrNode>();
  delete node;
  styio::session_alloc::set_current_ir_stats(previous_stats);

  styio::bench::BenchmarkSample sample;
  sample.phase = "ir_alloc";
  sample.label = label;
  capture_ir_allocation_stats(sample, stats);
  return sample;
}

styio::bench::BenchmarkSample capture_scheduler_queue_sample() {
  StyioTaskSchedulerProfileSnapshot snapshot{};
  styio_task_scheduler_profile_snapshot(&snapshot);

  styio::bench::BenchmarkSample sample;
  sample.phase = "scheduler";
  sample.label = "task_queue_mode";
  sample.task_scheduler_queue_kind = snapshot.ready_queue_kind;
  sample.task_scheduler_worker_count = snapshot.worker_count;
  sample.task_scheduler_queue_capacity = snapshot.queue_capacity;
  sample.task_scheduler_queue_current_depth = snapshot.queue_current_depth;
  sample.task_scheduler_queue_peak_depth = snapshot.max_queue_depth;
  sample.task_scheduler_queue_accepted_pushes = snapshot.queue_accepted_pushes;
  sample.task_scheduler_queue_pops = snapshot.queue_pops;
  sample.task_scheduler_queue_pressure_events = snapshot.queue_pressure_events;
  sample.task_scheduler_queue_producer_waits = snapshot.queue_producer_waits;
  sample.task_scheduler_queue_consumer_waits = snapshot.queue_consumer_waits;
  sample.task_scheduler_queue_close_wake_ups = snapshot.queue_close_wake_ups;
  sample.task_scheduler_queue_closed = snapshot.queue_closed;
  return sample;
}

styio::bench::BenchmarkSample capture_bounded_task_pressure_sample(
    int warmup,
    int measured) {
  constexpr std::size_t operation_count = 256;
  constexpr std::size_t capacity = 4;
  styio::runtime::ReadyQueueSnapshot last_snapshot;
  auto sample = styio::bench::run_benchmark(
    "scheduler",
    "bounded_task_pressure_256",
    warmup,
    measured,
    [&]() {
      styio::runtime::BoundedReadyQueue queue(capacity);
      std::vector<std::size_t> tasks(operation_count);
      for (std::size_t index = 0; index < operation_count; ++index) {
        tasks[index] = index;
      }
      for (std::size_t index = 0; index < capacity; ++index) {
        if (queue.push(&tasks[index])
            != styio::runtime::ReadyQueuePushResult::Accepted) {
          throw std::runtime_error("bounded task pressure prefill was rejected");
        }
      }

      std::thread producer([&]() {
        for (std::size_t index = capacity; index < operation_count; ++index) {
          if (queue.push(&tasks[index])
              != styio::runtime::ReadyQueuePushResult::Accepted) {
            return;
          }
        }
      });
      while (queue.snapshot().producer_waits == 0) {
        std::this_thread::yield();
      }
      std::vector<bool> seen(operation_count, false);
      std::size_t invalid = 0;
      std::thread consumer([&]() {
        while (auto* raw = static_cast<std::size_t*>(queue.wait_pop())) {
          if (*raw >= operation_count || seen[*raw]) {
            ++invalid;
          }
          else {
            seen[*raw] = true;
          }
        }
      });
      producer.join();
      queue.close();
      consumer.join();
      last_snapshot = queue.snapshot();
      const std::size_t observed = static_cast<std::size_t>(
        std::count(seen.begin(), seen.end(), true));
      if (invalid != 0 || observed != operation_count
          || last_snapshot.accepted_pushes != operation_count
          || last_snapshot.pops != operation_count
          || last_snapshot.pressure_events == 0
          || last_snapshot.pressure_events != last_snapshot.producer_waits
          || last_snapshot.current_depth != 0
          || last_snapshot.peak_depth > capacity
          || !last_snapshot.closed) {
        throw std::runtime_error("bounded task pressure facts violated");
      }
    });
  sample.task_scheduler_queue_kind =
    static_cast<int64_t>(styio::runtime::ReadyQueueKind::BoundedWait);
  sample.task_scheduler_queue_capacity = static_cast<int64_t>(last_snapshot.capacity);
  sample.task_scheduler_queue_current_depth = static_cast<int64_t>(last_snapshot.current_depth);
  sample.task_scheduler_queue_peak_depth = static_cast<int64_t>(last_snapshot.peak_depth);
  sample.task_scheduler_queue_accepted_pushes = static_cast<int64_t>(last_snapshot.accepted_pushes);
  sample.task_scheduler_queue_pops = static_cast<int64_t>(last_snapshot.pops);
  sample.task_scheduler_queue_pressure_events = static_cast<int64_t>(last_snapshot.pressure_events);
  sample.task_scheduler_queue_producer_waits = static_cast<int64_t>(last_snapshot.producer_waits);
  sample.task_scheduler_queue_consumer_waits = static_cast<int64_t>(last_snapshot.consumer_waits);
  sample.task_scheduler_queue_close_wake_ups = static_cast<int64_t>(last_snapshot.close_wake_ups);
  sample.task_scheduler_queue_closed = last_snapshot.closed ? 1 : 0;
  return sample;
}

styio::bench::BenchmarkSample capture_resource_typestate_join_sample(
    int warmup,
    int measured) {
  constexpr std::size_t conditional_count = 256;
  StyioSemaContext::ResourceTypestateDataflowStats last_stats;
  auto sample = styio::bench::run_benchmark(
    "resource_typestate",
    "resource_typestate_conditional_join_256",
    warmup,
    measured,
    [&]() {
      styio::session::SymbolInterner symbols;
      styio::session::TypeTable types;
      AstToStyioIRLowerer analyzer;
      analyzer.attach_type_table(types, symbols);
      std::vector<StyioAST*> flows;
      flows.reserve(conditional_count);
      for (std::size_t index = 0; index < conditional_count; ++index) {
        const std::string name = "resource_" + std::to_string(index);
        const auto sid = symbols.intern(name);
        const StyioDataType handle = styio_make_file_handle_type("string");
        StyioSemaContext::BindingInfo info;
        info.resource_value = true;
        info.declared_type = handle;
        analyzer.record_binding_info(name, sid, info);
        analyzer.record_local_binding_type(name, sid, handle);
        flows.push_back(new CondFlowAST(
          StyioNodeType::CondFlow_Both,
          CondAST::Create(LogicType::RAW, BoolAST::Create(true)),
          BlockAST::Create({ResourceRedirectAST::Create(
            NameAST::Create(name, sid), EmptyResourceAST::Create())}),
          BlockAST::Create({PassAST::Create()})));
      }
      std::unique_ptr<BlockAST> program(BlockAST::Create(std::move(flows)));
      program->typeInfer(&analyzer);
      last_stats = analyzer.resource_typestate_dataflow_stats();
      constexpr std::size_t expected_peak_fact_slots =
        3 * conditional_count - 2;
      if (last_stats.branch_snapshot_count != 2 * conditional_count
          || last_stats.join_count != conditional_count
          || last_stats.fact_insertion_count != conditional_count
          || last_stats.peak_temporary_fact_slots != expected_peak_fact_slots
          || !analyzer.is_consumed_resource_name(
            symbols.lookup("resource_255"), "resource_255")) {
        throw std::runtime_error(
          "resource typestate conditional-join bounds violated");
      }
    });
  sample.resource_typestate_branch_snapshot_count =
    static_cast<int64_t>(last_stats.branch_snapshot_count);
  sample.resource_typestate_join_count =
    static_cast<int64_t>(last_stats.join_count);
  sample.resource_typestate_fact_insertion_count =
    static_cast<int64_t>(last_stats.fact_insertion_count);
  sample.resource_typestate_peak_temporary_fact_slots =
    static_cast<int64_t>(last_stats.peak_temporary_fact_slots);
  return sample;
}

styio::bench::BenchmarkSample capture_zip_barrier_facts_sample(
    int warmup,
    int measured) {
  constexpr std::size_t bundle_count = 256;
  std::size_t last_valid_count = 0;
  auto sample = styio::bench::run_benchmark(
    "zip_barrier_facts",
    "zip_barrier_facts_256",
    warmup,
    measured,
    [&]() {
      std::vector<std::unique_ptr<SIOStreamZip>> nodes;
      nodes.reserve(bundle_count);
      for (std::size_t index = 0; index < bundle_count; ++index) {
        nodes.emplace_back(SIOStreamZip::Create(
          SCListLiteral::Create({}, "i64"),
          false,
          false,
          "left",
          SCListLiteral::Create({}, "i64"),
          false,
          false,
          "right",
          false,
          false,
          "i64",
          "i64",
          SGBlock::Create({})));
      }
      last_valid_count = static_cast<std::size_t>(std::count_if(
        nodes.begin(),
        nodes.end(),
        [](const auto& node) {
          return node != nullptr && node->barrier_facts.is_canonical();
        }));
      if (nodes.size() != bundle_count || last_valid_count != bundle_count) {
        throw std::runtime_error(
          "zip barrier fact benchmark constructed a noncanonical bundle");
      }
    });
  sample.zip_barrier_fact_bundle_count =
    static_cast<int64_t>(bundle_count);
  sample.zip_barrier_fact_valid_count =
    static_cast<int64_t>(last_valid_count);
  sample.zip_barrier_fact_metadata_bytes = static_cast<int64_t>(
    bundle_count * sizeof(SGStreamZipBarrierFacts));
  return sample;
}

styio::bench::BenchmarkSample capture_dead_suffix_sample(
    const std::string& label,
    std::size_t suffix_size,
    int warmup,
    int measured) {
  std::size_t last_before = 0;
  std::size_t last_after = 0;

  auto sample = styio::bench::run_benchmark(
    "ir_transform", label, warmup, measured, [&]() {
      styio::session_alloc::SessionAllocationStats allocations;
      auto* previous = styio::session_alloc::set_current_ir_stats(&allocations);

      std::vector<StyioIR*> statements;
      statements.reserve(suffix_size + 2);
      statements.push_back(SGNoOp::Create());
      statements.push_back(SGReturn::Create(SGConstInt::Create(1)));
      for (std::size_t index = 0; index < suffix_size; ++index) {
        statements.push_back(SGNoOp::Create());
      }
      auto* root = SGMainEntry::Create(std::move(statements));
      const auto live_nodes = [&]() {
        return allocations.raw_allocations + allocations.arena_allocations
          - allocations.destructor_calls;
      };
      const std::size_t before = live_nodes();
      const std::size_t raw_allocations_before = allocations.raw_allocations;
      const std::size_t arena_allocations_before = allocations.arena_allocations;
      const std::size_t destructors_before = allocations.destructor_calls;

      styio::lowering::StyioIRPassPipelineOptions options;
      options.collect_timing = false;
      styio::lowering::StyioIRPassManager manager;
      manager.add_dead_suffix_elimination_pass();
      auto result = manager.run(root, options);
      if (!result.ok() || result.passes.size() != 1
          || result.passes.front().name != "styioir-dead-suffix-elimination"
          || result.passes.front().statistics.statement_containers_visited != 1
          || result.passes.front().statistics.statements_removed != suffix_size
          || result.passes.front().statistics.statements_examined != 2
          || result.passes.front().statistics.statement_containers_changed != 1
          || allocations.raw_allocations != raw_allocations_before
          || allocations.arena_allocations != arena_allocations_before
          || allocations.destructor_calls - destructors_before != suffix_size
          || live_nodes() + suffix_size != before
          || root->stmts.size() != 2
          || !styio::ir::verify_styio_ir(root).ok()) {
        delete result.root;
        styio::session_alloc::set_current_ir_stats(previous);
        throw std::runtime_error("dead-suffix benchmark invariant failed");
      }

      last_before = before;
      last_after = live_nodes();
      delete result.root;
      if (live_nodes() != 0) {
        styio::session_alloc::set_current_ir_stats(previous);
        throw std::runtime_error("dead-suffix benchmark leaked IR nodes");
      }
      styio::session_alloc::set_current_ir_stats(previous);
    });

  sample.ir_max_node_count = static_cast<int64_t>(last_before);
  sample.ir_node_count = static_cast<int64_t>(last_after);
  return sample;
}

} // namespace

int main(int argc, char** argv) {
  using namespace styio::bench;

  BenchmarkResult result;
  result.git_sha = get_git_sha();
  result.build_type = get_build_type();
  result.timestamp = std::chrono::duration_cast<std::chrono::seconds>(
    std::chrono::system_clock::now().time_since_epoch()).count();

  const int warmup  = bench_iters("STYIO_BENCH_WARMUP", 2);
  const int measured = bench_iters("STYIO_BENCH_MEASURED", 5);

  // --- 1. lex_large_file ---
  {
    std::string code = gen_large_file(10000);
    result.samples.push_back(run_benchmark("lex", "large_file_10k", warmup, measured, [&]() {
      auto metrics = StyioTokenizerMetrics{};
      auto tokens = StyioTokenizer::tokenizeWithMetrics(code, &metrics);
      for (auto* t : tokens) delete t;
    }));
  }

  // --- 2. parse_many_stmts ---
  {
    std::string code = gen_many_bindings(1000);
    result.samples.push_back(run_benchmark("parse", "many_stmts_1k", warmup, measured, [&]() {
      CompilationSession session;
      auto tokens = StyioTokenizer::tokenize(code);
      session.adopt_tokens(std::move(tokens));
      auto* ctx = make_context("bench.styio", code, session.tokens());
      session.attach_context(ctx);
      auto* ast = safe_parse(*ctx);
      if (ast) session.attach_ast(ast);
    }));
  }

  // --- 3. parse_deep_expr ---
  {
    std::string code = gen_deep_expr(200);
    result.samples.push_back(run_benchmark("parse", "deep_expr_200", warmup, measured, [&]() {
      CompilationSession session;
      auto tokens = StyioTokenizer::tokenize(code);
      session.adopt_tokens(std::move(tokens));
      auto* ctx = make_context("bench.styio", code, session.tokens());
      session.attach_context(ctx);
      auto* ast = safe_parse(*ctx);
      if (ast) session.attach_ast(ast);
    }));
  }

  // --- 4. name_resolution ---
  {
    auto capture_expression = [&](const std::string& label, const std::string& code) {
      StyioParserRouteStats last_stats;
      BenchmarkSample sample = run_benchmark("parse_expr", label, warmup, measured, [&]() {
        CompilationSession session;
        auto tokens = StyioTokenizer::tokenize(code);
        const size_t token_count = tokens.size();
        session.adopt_tokens(std::move(tokens));
        auto* ctx = make_context("expr-bench.styio", code, session.tokens());
        session.attach_context(ctx);
        StyioParserRouteStats stats;
        ctx->set_parser_route_stats_latest(&stats);
        std::unique_ptr<StyioAST> ast(parse_expr(*ctx));
        ctx->set_parser_route_stats_latest(nullptr);
        if (ast == nullptr) {
          throw std::runtime_error("expression benchmark returned null AST");
        }
        if (stats.legacy_fallback_statements != 0
            || stats.nightly_internal_legacy_bridges != 0
            || stats.expression_token_visits > 8 * token_count + 8
            || stats.expression_scratch_allocations != 0) {
          throw std::runtime_error("expression benchmark violated parser invariants");
        }
        last_stats = stats;
      });
      sample.alloc_count = static_cast<int64_t>(last_stats.expression_ast_nodes);
      result.samples.push_back(std::move(sample));
    };

    capture_expression("expr_flat_add_4096", gen_flat_add_expression(4096));
    capture_expression("expr_mixed_4096", gen_mixed_expression(4096));
    capture_expression("expr_right_power_64", gen_power_expression(64));
  }

  // --- 5. name_resolution ---
  {
    std::string code = gen_many_names(5000);
    styio::session_alloc::SessionAllocationStats ir_stats;
    result.samples.push_back(run_benchmark("sema", "name_resolution_5k", warmup, measured, [&]() {
      CompilationSession session;
      auto tokens = StyioTokenizer::tokenize(code);
      session.adopt_tokens(std::move(tokens));
      auto* ctx = make_context("bench.styio", code, session.tokens());
      session.attach_context(ctx);
      auto* ast = safe_parse(*ctx);
      if (!ast) return;
      session.attach_ast(ast);
      AstToStyioIRLowerer lowerer;
      try { styio::sema::require_semantic_analysis(ast, &lowerer); }
      catch (const StyioBaseException&) {}
      session.mark_type_checked();
      ir_stats = session.ir_allocation_stats();
    }));
    capture_ir_allocation_stats(result.samples.back(), ir_stats);
  }

  // --- 6. type_check_many_bindings ---
  {
    std::string code = gen_typed_bindings(1000);
    styio::session_alloc::SessionAllocationStats ir_stats;
    result.samples.push_back(run_benchmark("type", "typed_bindings_1k", warmup, measured, [&]() {
      CompilationSession session;
      auto tokens = StyioTokenizer::tokenize(code);
      session.adopt_tokens(std::move(tokens));
      auto* ctx = make_context("bench.styio", code, session.tokens());
      session.attach_context(ctx);
      auto* ast = safe_parse(*ctx);
      if (!ast) return;
      session.attach_ast(ast);
      AstToStyioIRLowerer lowerer;
      try { styio::sema::require_semantic_analysis(ast, &lowerer); }
      catch (const StyioBaseException&) {}
      session.mark_type_checked();
      ir_stats = session.ir_allocation_stats();
    }));
    capture_ir_allocation_stats(result.samples.back(), ir_stats);
  }

  // --- 7. callable_constraint_worklist ---
  {
    constexpr std::size_t constraint_count = 256;
    const std::string code = gen_callable_constraint_fanout(
      static_cast<int>(constraint_count));
    StyioSemaContext::CallableConstraintSolverStats last_stats;
    BenchmarkSample sample = run_benchmark(
      "constraint", "callable_worklist_fanout_256", warmup, measured, [&]() {
        CompilationSession session;
        auto tokens = StyioTokenizer::tokenize(code);
        session.adopt_tokens(std::move(tokens));
        auto* ctx = make_context(
          "callable-worklist-bench.styio", code, session.tokens());
        session.attach_context(ctx);
        auto* ast = safe_parse(*ctx);
        if (ast == nullptr) {
          throw std::runtime_error(
            "callable worklist benchmark failed to parse");
        }
        session.attach_ast(ast);
        AstToStyioIRLowerer lowerer;
        styio::sema::require_semantic_analysis(ast, &lowerer);
        const auto& stats = lowerer.callable_constraint_solver_stats();
        if (stats.run_count < 2
            || stats.input_constraint_count < constraint_count + 1
            || stats.attempt_count
                 != stats.input_constraint_count + stats.requeue_count
            || stats.peak_frontier_count != constraint_count
            || stats.peak_blocked_count != constraint_count
            || stats.peak_live_waiter_count != constraint_count
            || stats.peak_scheduler_storage_slots
                 > 3 * constraint_count + 4) {
          throw std::runtime_error(
            "callable worklist scheduler bounds violated: runs="
            + std::to_string(stats.run_count)
            + ", input=" + std::to_string(stats.input_constraint_count)
            + ", attempts=" + std::to_string(stats.attempt_count)
            + ", requeues=" + std::to_string(stats.requeue_count)
            + ", frontier=" + std::to_string(stats.peak_frontier_count)
            + ", blocked=" + std::to_string(stats.peak_blocked_count)
            + ", waiters=" + std::to_string(stats.peak_live_waiter_count)
            + ", slots="
            + std::to_string(stats.peak_scheduler_storage_slots));
        }
        last_stats = stats;
      });
    sample.alloc_count = static_cast<int64_t>(
      last_stats.peak_scheduler_storage_slots);
    result.samples.push_back(std::move(sample));
  }

  // --- 8. topology_large_dag ---
  {
    std::string code = gen_resource_ops(200);
    result.samples.push_back(run_benchmark("topology", "resource_dag_200", warmup, measured, [&]() {
      CompilationSession session;
      auto tokens = StyioTokenizer::tokenize(code);
      session.adopt_tokens(std::move(tokens));
      auto* ctx = make_context("bench.styio", code, session.tokens());
      session.attach_context(ctx);
      auto* ast = safe_parse(*ctx);
      if (!ast) return;
      session.attach_ast(ast);
      try { styio::resource_topology::validate_or_throw(ast, "benchmark"); }
      catch (const std::exception&) {}
    }));
  }

  // --- 8. diag_many_errors ---
  {
    std::string code = gen_error_source(100);
    result.samples.push_back(run_benchmark("diag", "many_errors_100", warmup, measured, [&]() {
      CompilationSession session;
      auto tokens = StyioTokenizer::tokenize(code);
      session.adopt_tokens(std::move(tokens));
      auto* ctx = make_context("bench.styio", code, session.tokens());
      session.attach_context(ctx);
      try {
        auto* ast = safe_parse(*ctx);
        if (ast) session.attach_ast(ast);
      } catch (const StyioBaseException& ex) {
        volatile auto ignored = ex.what(); (void)ignored;
      }
    }));
  }

  // --- 9. runtime baseline ---
  {
    result.samples.push_back(run_benchmark("runtime", "task_spawn_1k_nop", warmup, measured, [&]() {
      volatile int sum = 0;
      for (int i = 0; i < 1000; ++i) sum += i;
      (void)sum;
    }));
  }

  // --- 10. Route cache stats (single-run capture, not timed) ---
  {
    auto capture_route_cache_for = [&](const std::string& label, const std::string& code) {
      CompilationSession session;
      auto tokens = StyioTokenizer::tokenize(code);
      session.adopt_tokens(std::move(tokens));
      auto* ctx = make_context("bench.styio", code, session.tokens());
      session.attach_context(ctx);
      auto* ast = safe_parse(*ctx);
      if (ast) session.attach_ast(ast);
      BenchmarkSample s = capture_route_cache_sample(*ctx, label);
      result.samples.push_back(std::move(s));
    };

    capture_route_cache_for("many_stmts_1k", gen_many_bindings(1000));
    capture_route_cache_for("deep_expr_200", gen_deep_expr(200));
    capture_route_cache_for("name_resolution_5k", gen_many_names(5000));
    capture_route_cache_for("typed_bindings_1k", gen_typed_bindings(1000));
    capture_route_cache_for("resource_dag_200", gen_resource_ops(200));
  }

  // --- 11. IR allocation stats (single-run capture, not timed) ---
  {
    result.samples.push_back(capture_ir_allocation_sample("factory_smoke"));
  }

  // --- 12. Verified dead-suffix elimination ---
  {
    result.samples.push_back(capture_dead_suffix_sample(
      "DeadSuffixFlat1024", 1024, warmup, measured));
    result.samples.push_back(capture_dead_suffix_sample(
      "DeadSuffixFlat4096", 4096, warmup, measured));
  }

  // --- 13. Scheduler queue metadata (single-run capture, not timed) ---
  {
    result.samples.push_back(capture_scheduler_queue_sample());
    result.samples.push_back(capture_bounded_task_pressure_sample(
      warmup, measured));
  }

  // --- 14. Conditional resource typestate dataflow ---
  {
    result.samples.push_back(capture_resource_typestate_join_sample(
      warmup, measured));
  }

  // --- 15. Deterministic finite zip-barrier facts ---
  {
    result.samples.push_back(capture_zip_barrier_facts_sample(
      warmup, measured));
  }

  // --- Output ---
  std::string json = result.to_json();

  const char* output_path = nullptr;
  for (int i = 1; i < argc; ++i) {
    std::string_view arg(argv[i]);
    if ((arg == "--output" || arg == "-o") && i + 1 < argc)
      output_path = argv[++i];
    else if (arg == "--stdout")
      output_path = nullptr;
  }

  if (output_path) {
    std::ofstream out(output_path);
    if (!out) { std::cerr << "ERROR: cannot write\n"; return 1; }
    out << json;
  } else {
    std::cout << json << "\n";
  }
  return 0;
}
