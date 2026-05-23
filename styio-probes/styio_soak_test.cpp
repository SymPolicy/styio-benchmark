#include <gtest/gtest.h>

#include <array>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#if defined(__linux__) || defined(__APPLE__)
#include <sys/wait.h>
#include <unistd.h>
#endif
#if defined(__APPLE__)
#include <mach/mach.h>
#endif

#include "StyioLowering/AstToStyioIRLowerer.hpp"
#include "StyioCodeGen/CodeGenVisitor.hpp"
#include "StyioExtern/ExternLib.hpp"
#include "StyioException/Exception.hpp"
#include "StyioParser/Parser.hpp"
#include "StyioParser/Tokenizer.hpp"
#include "StyioSession/CompilationSession.hpp"
#include "llvm/Support/Error.h"
#include "llvm/Support/TargetSelect.h"

namespace fs = std::filesystem;

#ifndef STYIO_SOURCE_DIR
#define STYIO_SOURCE_DIR "."
#endif

#ifndef STYIO_COMPILER_EXE
#define STYIO_COMPILER_EXE ""
#endif

namespace {

int
read_env_i32(const char* key, int fallback, int min_v, int max_v) {
  const char* raw = std::getenv(key);
  if (raw == nullptr || raw[0] == '\0') {
    return fallback;
  }
  char* end = nullptr;
  long v = std::strtol(raw, &end, 10);
  if (end == raw || *end != '\0') {
    return fallback;
  }
  if (v < min_v) {
    return min_v;
  }
  if (v > max_v) {
    return max_v;
  }
  return static_cast<int>(v);
}

void
normalize_text(std::string& s) {
  while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) {
    s.pop_back();
  }
  s.push_back('\n');
}

std::string
read_text_file(const fs::path& path) {
  std::ifstream in(path);
  if (!in) {
    return {};
  }
  std::ostringstream ss;
  ss << in.rdbuf();
  return ss.str();
}

size_t
read_rss_bytes();

std::vector<std::pair<size_t, size_t>>
build_line_seps(const std::string& src) {
  std::vector<std::pair<size_t, size_t>> seps;
  size_t line_start = 0;
  size_t line_len = 0;
  for (size_t i = 0; i < src.size(); ++i) {
    if (src[i] == '\n') {
      seps.push_back(std::make_pair(line_start, line_len));
      line_start = i + 1;
      line_len = 0;
    }
    else {
      line_len += 1;
    }
  }
  if (!src.empty() && src.back() != '\n') {
    seps.push_back(std::make_pair(line_start, line_len));
  }
  return seps;
}

struct LoadedSource
{
  std::string label;
  std::string code;
  std::vector<std::pair<size_t, size_t>> line_seps;
  fs::path source_path;
};

struct CompilerWorkload
{
  std::string module_family;
  LoadedSource src;
  std::string expected_stdout;
  std::string stdin_text;
  bool run_full_stack = true;
};

LoadedSource
load_source_fixture(const fs::path& path, const std::string& label) {
  LoadedSource src;
  src.label = label;
  src.source_path = fs::absolute(path);
  src.code = read_text_file(src.source_path);
  src.line_seps = build_line_seps(src.code);
  return src;
}

LoadedSource
make_inline_source(const std::string& label, const std::string& code) {
  LoadedSource src;
  src.label = label;
  src.code = code;
  src.line_seps = build_line_seps(src.code);
  return src;
}

CompilerWorkload
load_workload_fixture(
  const std::string& module_family,
  const std::string& label,
  const fs::path& input_path,
  const fs::path& stdout_path = fs::path(),
  const fs::path& stdin_path = fs::path(),
  bool run_full_stack = true
) {
  CompilerWorkload workload;
  workload.module_family = module_family;
  workload.src = load_source_fixture(input_path, label);
  if (!stdout_path.empty()) {
    workload.expected_stdout = read_text_file(fs::absolute(stdout_path));
    normalize_text(workload.expected_stdout);
  }
  if (!stdin_path.empty()) {
    workload.stdin_text = read_text_file(fs::absolute(stdin_path));
  }
  workload.run_full_stack = run_full_stack && !workload.expected_stdout.empty();
  return workload;
}

CompilerWorkload
make_inline_workload(
  const std::string& module_family,
  const std::string& label,
  const std::string& code,
  const std::string& expected_stdout = "",
  const std::string& stdin_text = "",
  bool run_full_stack = true
) {
  CompilerWorkload workload;
  workload.module_family = module_family;
  workload.src = make_inline_source(label, code);
  workload.expected_stdout = expected_stdout;
  if (!workload.expected_stdout.empty()) {
    normalize_text(workload.expected_stdout);
  }
  workload.stdin_text = stdin_text;
  workload.run_full_stack = run_full_stack && !workload.expected_stdout.empty();
  return workload;
}

CompilerWorkload
make_state_inline_workload(const std::string& label) {
  std::string code;
  code += "@out : i64|..3|\n";
  code += "[1, 2, 3] >> #(v) => {\n";
  code += "  next = @out[-1] + v\n";
  code += "  next -> @out\n";
  code += "  >_(next)\n";
  code += "}\n";
  return make_inline_workload("StateAndSeries", label, code, "1\n3\n6\n");
}

CompilerWorkload
make_dict_inline_workload() {
  std::string code;
  code += "d = dict{\"a\": 1, \"b\": 2}\n";
  code += "d[\"c\"] = 3\n";
  code += ">_(d[\"a\"])\n";
  code += ">_(d.length)\n";
  code += "ks = d.keys\n";
  code += "vs = d.values\n";
  code += ">_(ks[0])\n";
  code += ">_(vs[1])\n";
  return make_inline_workload("Collections", "dict_heavy", code, "1\n3\na\n2\n");
}

std::vector<CompilerWorkload>
build_compiler_workloads() {
  const fs::path root = fs::path(STYIO_SOURCE_DIR);
  return {
    load_workload_fixture(
      "Scalar",
      "scalar_core",
      root / "tests" / "features" / "scalar_expressions" / "t20_combined.styio",
      root / "tests" / "features" / "scalar_expressions" / "expected" / "t20_combined.out"),
    load_workload_fixture(
      "Bindings",
      "bindings_chain",
      root / "tests" / "features" / "scalar_expressions" / "t19_chain_bind.styio",
      root / "tests" / "features" / "scalar_expressions" / "expected" / "t19_chain_bind.out"),
    load_workload_fixture(
      "Functions",
      "function_block_body",
      root / "tests" / "features" / "functions" / "t03_block_body.styio",
      root / "tests" / "features" / "functions" / "expected" / "t03_block_body.out"),
    load_workload_fixture(
      "ControlFlow",
      "control_match",
      root / "tests" / "features" / "control_flow" / "t10_fizzbuzz.styio",
      root / "tests" / "features" / "control_flow" / "expected" / "t10_fizzbuzz.out"),
    make_dict_inline_workload(),
    load_workload_fixture(
      "Resources",
      "resource_file_io",
      root / "tests" / "features" / "file_resources" / "t01_read_file.styio",
      root / "tests" / "features" / "file_resources" / "expected" / "t01_read_file.out"),
    load_workload_fixture(
      "Streams",
      "stdin_transform",
      root / "tests" / "features" / "stdio_input" / "t05_stdin_transform.styio",
      root / "tests" / "features" / "stdio_input" / "expected" / "t05_stdin_transform.out",
      root / "tests" / "features" / "stdio_input" / "data" / "transform_input.txt"),
    load_workload_fixture(
      "Streams",
      "stream_zip_files",
      root / "tests" / "features" / "stream_processing" / "t05_zip_files.styio",
      root / "tests" / "features" / "stream_processing" / "expected" / "t05_zip_files.out"),
    load_workload_fixture(
      "StateAndSeries",
      "snapshot_state",
      root / "tests" / "features" / "stream_processing" / "t09_snapshot_accum.styio",
      root / "tests" / "features" / "stream_processing" / "expected" / "t09_snapshot_accum.out"),
    load_workload_fixture(
      "StateAndSeries",
      "series_window_avg",
      root / "tests" / "features" / "state_resources" / "t03_window_avg.styio",
      root / "tests" / "features" / "state_resources" / "expected" / "t03_window_avg.out"),
    load_workload_fixture(
      "Topology",
      "topology_ring",
      root / "tests" / "features" / "final_bindings" / "t02_bounded_read.styio",
      root / "tests" / "features" / "final_bindings" / "expected" / "t02_bounded_read.out"),
    make_state_inline_workload("state_pulse_inline"),
    load_workload_fixture(
      "Mixed",
      "mixed_full_pipeline",
      root / "tests" / "pipeline_cases" / "p09_full_pipeline" / "input.styio",
      fs::path(),
      fs::path(),
      false),
  };
}

struct CompilerStageBenchResult
{
  std::string error;
  std::chrono::nanoseconds tokenize {};
  std::chrono::nanoseconds parse {};
  std::chrono::nanoseconds type_infer {};
  std::chrono::nanoseconds lower {};
  std::chrono::nanoseconds llvm_ir {};
  size_t avg_token_arena_bytes = 0;
  size_t avg_ast_arena_bytes = 0;
  size_t rss_before = 0;
  size_t rss_after = 0;
};

struct FullStackBenchResult
{
  std::string error;
  std::chrono::nanoseconds wall {};
  int loops_completed = 0;
};

enum class MicroBenchFocus
{
  Lexer,
  Parser,
  TypeInfer,
  Lower,
  LLVM,
};

struct MicroBenchSpec
{
  std::string name;
  MicroBenchFocus focus = MicroBenchFocus::Lexer;
  CompilerWorkload workload;
};

struct MicroBenchResult
{
  std::string error;
  std::chrono::nanoseconds focus_time {};
  size_t avg_token_count = 0;
  size_t avg_token_arena_bytes = 0;
  size_t avg_ast_arena_bytes = 0;
  size_t rss_before = 0;
  size_t rss_after = 0;
};

enum class ErrorBenchCategory
{
  Lex,
  Parse,
  Type,
  Runtime,
};

struct ErrorBenchSpec
{
  std::string name;
  ErrorBenchCategory category = ErrorBenchCategory::Parse;
  CompilerWorkload workload;
  int expected_exit_code = 0;
  std::string expected_diag_code;
};

struct ErrorBenchResult
{
  std::string error;
  std::chrono::nanoseconds wall {};
  int loops_completed = 0;
  size_t avg_diag_bytes = 0;
};

const char*
micro_focus_name(MicroBenchFocus focus) {
  switch (focus) {
    case MicroBenchFocus::Lexer:
      return "lexer";
    case MicroBenchFocus::Parser:
      return "parser";
    case MicroBenchFocus::TypeInfer:
      return "type";
    case MicroBenchFocus::Lower:
      return "lower";
    case MicroBenchFocus::LLVM:
      return "llvm";
  }
  return "unknown";
}

const char*
error_category_name(ErrorBenchCategory category) {
  switch (category) {
    case ErrorBenchCategory::Lex:
      return "lex";
    case ErrorBenchCategory::Parse:
      return "parse";
    case ErrorBenchCategory::Type:
      return "type";
    case ErrorBenchCategory::Runtime:
      return "runtime";
  }
  return "runtime";
}

std::string
build_long_identifier_lexer_code(int lines) {
  std::string code;
  code.reserve(static_cast<size_t>(lines) * 128);
  for (int i = 0; i < lines; ++i) {
    code += "ultra_long_identifier_segment_alpha_beta_gamma_delta_epsilon_";
    code += std::to_string(i);
    code += "_tail = ";
    code += std::to_string(i);
    code += "\n";
  }
  return code;
}

std::string
build_mixed_trivia_lexer_code(int lines) {
  std::string code;
  code.reserve(static_cast<size_t>(lines) * 96);
  for (int i = 0; i < lines; ++i) {
    code += "// lead comment ";
    code += std::to_string(i);
    code += "\n";
    code += "  \tvalue_";
    code += std::to_string(i);
    code += "    =    ";
    code += std::to_string(i);
    code += "    // trailing comment\n";
    if ((i % 4) == 0) {
      code += "\n";
    }
  }
  return code;
}

std::string
build_expr_scalar_chain_code(int binds) {
  std::string code;
  code.reserve(static_cast<size_t>(binds) * 64);
  code += "v0 = 1\n";
  for (int i = 0; i < binds; ++i) {
    code += "v";
    code += std::to_string(i + 1);
    code += " = (v";
    code += std::to_string(i);
    code += " + ";
    code += std::to_string(i + 2);
    code += ") * 3 - 4\n";
  }
  code += ">_(v";
  code += std::to_string(binds);
  code += ")\n";
  return code;
}

std::vector<MicroBenchSpec>
build_micro_bench_specs() {
  const fs::path root = fs::path(STYIO_SOURCE_DIR);
  return {
    {
      "lexer.long_identifiers",
      MicroBenchFocus::Lexer,
      make_inline_workload(
        "Lexer",
        "lexer_long_identifiers",
        build_long_identifier_lexer_code(512),
        "",
        "",
        false),
    },
    {
      "lexer.mixed_trivia",
      MicroBenchFocus::Lexer,
      make_inline_workload(
        "Lexer",
        "lexer_mixed_trivia",
        build_mixed_trivia_lexer_code(512),
        "",
        "",
        false),
    },
    {
      "parser.expr_scalar_chain",
      MicroBenchFocus::Parser,
      make_inline_workload(
        "Scalar",
        "parser_expr_scalar_chain",
        build_expr_scalar_chain_code(192),
        "",
        "",
        false),
    },
    {
      "parser.function_block_body",
      MicroBenchFocus::Parser,
      load_workload_fixture(
        "Functions",
        "function_block_body",
        root / "tests" / "features" / "functions" / "t03_block_body.styio",
        fs::path(),
        fs::path(),
        false),
    },
    {
      "parser.match_cases",
      MicroBenchFocus::Parser,
      load_workload_fixture(
        "ControlFlow",
        "parser_match_cases",
        root / "tests" / "features" / "control_flow" / "t02_match_expr.styio",
        fs::path(),
        fs::path(),
        false),
    },
    {
      "parser.iterator_resource_postfix",
      MicroBenchFocus::Parser,
      load_workload_fixture(
        "Resources",
        "parser_iterator_resource_postfix",
        root / "tests" / "features" / "file_resources" / "t01_read_file.styio",
        fs::path(),
        fs::path(),
        false),
    },
    {
      "type.dict_heavy",
      MicroBenchFocus::TypeInfer,
      make_dict_inline_workload(),
    },
    {
      "type.snapshot_state",
      MicroBenchFocus::TypeInfer,
      load_workload_fixture(
        "StateAndSeries",
        "snapshot_state",
        root / "tests" / "features" / "stream_processing" / "t09_snapshot_accum.styio",
        fs::path(),
        fs::path(),
        false),
    },
    {
      "lower.stream_zip",
      MicroBenchFocus::Lower,
      load_workload_fixture(
        "Streams",
        "stream_zip_files",
        root / "tests" / "features" / "stream_processing" / "t05_zip_files.styio",
        fs::path(),
        fs::path(),
        false),
    },
    {
      "lower.resource_io",
      MicroBenchFocus::Lower,
      load_workload_fixture(
        "Resources",
        "resource_file_io",
        root / "tests" / "features" / "file_resources" / "t01_read_file.styio",
        fs::path(),
        fs::path(),
        false),
    },
    {
      "lower.state_pulse",
      MicroBenchFocus::Lower,
      make_state_inline_workload("state_pulse_inline_lower"),
    },
    {
      "llvm.scalar_ir",
      MicroBenchFocus::LLVM,
      load_workload_fixture(
        "Scalar",
        "scalar_core",
        root / "tests" / "features" / "scalar_expressions" / "t20_combined.styio",
        fs::path(),
        fs::path(),
        false),
    },
    {
      "llvm.resource_ir",
      MicroBenchFocus::LLVM,
      load_workload_fixture(
        "Resources",
        "resource_file_io_llvm",
        root / "tests" / "features" / "file_resources" / "t01_read_file.styio",
        fs::path(),
        fs::path(),
        false),
    },
    {
      "llvm.state_ir",
      MicroBenchFocus::LLVM,
      make_state_inline_workload("state_pulse_inline_llvm"),
    },
  };
}

std::vector<ErrorBenchSpec>
build_error_bench_specs() {
  const fs::path root = fs::path(STYIO_SOURCE_DIR);
  return {
    {
      "lex.unterminated_block_comment",
      ErrorBenchCategory::Lex,
      make_inline_workload(
        "ErrorPaths",
        "lex_unterminated_block_comment",
        "a /* no closing",
        "",
        "",
        false),
      2,
      "STYIO_LEX",
    },
    {
      "parse.empty_match_cases",
      ErrorBenchCategory::Parse,
      make_inline_workload(
        "ErrorPaths",
        "parse_empty_match_cases",
        "x = 1\nx ?= {\n}\n",
        "",
        "",
        false),
      3,
      "STYIO_PARSE",
    },
    {
      "type.final_then_flex_i64",
      ErrorBenchCategory::Type,
      load_workload_fixture(
        "ErrorPaths",
        "type_final_then_flex_i64",
        root / "tests" / "features" / "final_bindings" / "e01_final_then_flex_i64.styio",
        fs::path(),
        fs::path(),
        false),
      4,
      "STYIO_TYPE",
    },
    {
      "runtime.read_missing_file",
      ErrorBenchCategory::Runtime,
      load_workload_fixture(
        "ErrorPaths",
        "runtime_read_missing_file",
        root / "tests" / "features" / "file_resources" / "t06_fail_fast.styio",
        fs::path(),
        fs::path(),
        false),
      5,
      "STYIO_RUNTIME",
    },
  };
}

void
ensure_llvm_targets_initialized() {
  static const bool initialized = []() {
    llvm::InitializeNativeTarget();
    llvm::InitializeNativeTargetAsmPrinter();
    llvm::InitializeNativeTargetAsmParser();
    return true;
  }();
  (void)initialized;
}

CompilerStageBenchResult
run_compiler_stage_bench(const CompilerWorkload& workload, int loops) {
  CompilerStageBenchResult result;
  result.rss_before = read_rss_bytes();
  ensure_llvm_targets_initialized();

  CompilationSession session;
  size_t total_token_arena_bytes = 0;
  size_t total_ast_arena_bytes = 0;

  for (int i = 0; i < loops; ++i) {
    try {
      const auto t0 = std::chrono::steady_clock::now();
      session.adopt_tokens(StyioTokenizer::tokenize(workload.src.code));
      const auto t1 = std::chrono::steady_clock::now();

      session.attach_context(StyioContext::Create(
        workload.src.label,
        workload.src.code,
        workload.src.line_seps,
        session.tokens(),
        false));
      session.attach_ast(parse_main_block_with_engine_latest(
        *session.context(),
        StyioParserEngine::Nightly,
        nullptr));
      const auto t2 = std::chrono::steady_clock::now();

      AstToStyioIRLowerer analyzer;
      analyzer.typeInfer(session.ast());
      session.mark_type_checked();
      const auto t3 = std::chrono::steady_clock::now();

      session.attach_ir(analyzer.toStyioIR(session.ast()));
      const auto t4 = std::chrono::steady_clock::now();

      auto jit_or_err = StyioJIT_ORC::Create();
      if (!jit_or_err) {
        llvm::handleAllErrors(
          jit_or_err.takeError(),
          [&](const llvm::ErrorInfoBase& e) { result.error = e.message(); });
        session.mark_failed();
        session.reset();
        return result;
      }
      StyioToLLVM generator(std::move(*jit_or_err));
      session.ir()->toLLVMIR(&generator);
      (void)generator.dump_llvm_ir();
      const auto t5 = std::chrono::steady_clock::now();

      result.tokenize += std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0);
      result.parse += std::chrono::duration_cast<std::chrono::nanoseconds>(t2 - t1);
      result.type_infer += std::chrono::duration_cast<std::chrono::nanoseconds>(t3 - t2);
      result.lower += std::chrono::duration_cast<std::chrono::nanoseconds>(t4 - t3);
      result.llvm_ir += std::chrono::duration_cast<std::chrono::nanoseconds>(t5 - t4);
      total_token_arena_bytes += session.token_arena_bytes();
      total_ast_arena_bytes += session.ast_arena_bytes();
    } catch (const std::exception& ex) {
      result.error = ex.what();
      session.mark_failed();
      session.reset();
      return result;
    }

    session.reset();
  }

  result.rss_after = read_rss_bytes();
  result.avg_token_arena_bytes =
    loops > 0 ? (total_token_arena_bytes / static_cast<size_t>(loops)) : 0;
  result.avg_ast_arena_bytes =
    loops > 0 ? (total_ast_arena_bytes / static_cast<size_t>(loops)) : 0;
  return result;
}

void
print_compiler_stage_bench(
  const CompilerWorkload& workload,
  int loops,
  const CompilerStageBenchResult& result
) {
  const auto total =
    result.tokenize + result.parse + result.type_infer + result.lower + result.llvm_ir;
  auto per_iter_us = [&](std::chrono::nanoseconds ns) -> double {
    return loops > 0
             ? static_cast<double>(ns.count()) / (1000.0 * static_cast<double>(loops))
             : 0.0;
  };
  auto share_pct = [&](std::chrono::nanoseconds ns) -> double {
    return total.count() > 0
             ? (100.0 * static_cast<double>(ns.count()) / static_cast<double>(total.count()))
             : 0.0;
  };
  const size_t rss_growth =
    result.rss_after > result.rss_before ? (result.rss_after - result.rss_before) : 0;

  std::cout
    << "[stage-bench] module=" << workload.module_family
    << " label=" << workload.src.label
    << " loops=" << loops
    << " tokenize_us=" << per_iter_us(result.tokenize)
    << " parse_us=" << per_iter_us(result.parse)
    << " type_us=" << per_iter_us(result.type_infer)
    << " lower_us=" << per_iter_us(result.lower)
    << " llvm_ir_us=" << per_iter_us(result.llvm_ir)
    << " parse_share_pct=" << share_pct(result.parse)
    << " lower_share_pct=" << share_pct(result.lower)
    << " llvm_ir_share_pct=" << share_pct(result.llvm_ir)
    << " avg_token_arena_kib=" << (static_cast<double>(result.avg_token_arena_bytes) / 1024.0)
    << " avg_ast_arena_kib=" << (static_cast<double>(result.avg_ast_arena_bytes) / 1024.0)
    << " rss_growth_kib=" << (static_cast<double>(rss_growth) / 1024.0)
    << "\n";
}

struct CommandCapture
{
  std::string stdout_text;
  int raw_status = -1;
  int exit_code = -1;
};

CommandCapture
run_command_capture_stdout(const std::string& cmd) {
  std::array<char, 4096> buf {};
  CommandCapture result;
  FILE* pipe = popen(cmd.c_str(), "r");
  if (pipe == nullptr) {
    return result;
  }
  while (fgets(buf.data(), static_cast<int>(buf.size()), pipe) != nullptr) {
    result.stdout_text += buf.data();
  }
  result.raw_status = pclose(pipe);
  if (result.raw_status == -1) {
    result.exit_code = -1;
    return result;
  }
#if defined(__linux__) || defined(__APPLE__)
  if (WIFEXITED(result.raw_status)) {
    result.exit_code = WEXITSTATUS(result.raw_status);
  }
  else {
    result.exit_code = result.raw_status;
  }
#else
  result.exit_code = result.raw_status;
#endif
  return result;
}

std::string
capture_stdout(const std::string& cmd) {
  return run_command_capture_stdout(cmd).stdout_text;
}

size_t
read_rss_bytes() {
#if defined(__linux__)
  std::ifstream in("/proc/self/statm");
  if (!in) {
    return 0;
  }
  long total_pages = 0;
  long resident_pages = 0;
  in >> total_pages >> resident_pages;
  if (!in || resident_pages <= 0) {
    return 0;
  }
  const long page_size = sysconf(_SC_PAGESIZE);
  if (page_size <= 0) {
    return 0;
  }
  return static_cast<size_t>(resident_pages) * static_cast<size_t>(page_size);
#elif defined(__APPLE__)
  mach_task_basic_info info {};
  mach_msg_type_number_t count = MACH_TASK_BASIC_INFO_COUNT;
  const kern_return_t rc = task_info(
    mach_task_self(),
    MACH_TASK_BASIC_INFO,
    reinterpret_cast<task_info_t>(&info),
    &count);
  if (rc != KERN_SUCCESS) {
    return 0;
  }
  return static_cast<size_t>(info.resident_size);
#else
  return 0;
#endif
}

struct TempFileGuard
{
  fs::path path;

  ~TempFileGuard() {
    if (path.empty()) {
      return;
    }
    std::error_code ec;
    fs::remove(path, ec);
  }
};

fs::path
make_temp_text_file(const std::string& prefix, const std::string& suffix, const std::string& text) {
  const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
  const fs::path path =
    fs::temp_directory_path() / (prefix + std::to_string(stamp) + suffix);
  std::ofstream out(path);
  if (!out.good()) {
    return {};
  }
  out << text;
  return path;
}

fs::path
make_temp_line_file(const std::string& prefix, int lines) {
  const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
  const fs::path path = fs::temp_directory_path() / (prefix + std::to_string(stamp) + ".txt");
  std::ofstream out(path);
  if (!out.good()) {
    return {};
  }
  for (int i = 0; i < lines; ++i) {
    out << i << "\n";
  }
  return path;
}

std::string
shell_quote(const std::string& raw) {
  std::string out = "'";
  for (char ch : raw) {
    if (ch == '\'') {
      out += "'\\''";
    }
    else {
      out.push_back(ch);
    }
  }
  out.push_back('\'');
  return out;
}

std::string
compiler_runner_path() {
  const char* runner = std::getenv("STYIO_COMPILER_EXE");
  if (runner != nullptr && runner[0] != '\0') {
    return runner;
  }
  return STYIO_COMPILER_EXE;
}

fs::path
materialize_source_path(const CompilerWorkload& workload, TempFileGuard& guard) {
  if (!workload.src.source_path.empty()) {
    return workload.src.source_path;
  }
  guard.path = make_temp_text_file(
    "styio_bench_",
    ".styio",
    workload.src.code);
  return guard.path;
}

FullStackBenchResult
run_full_stack_bench(
  const CompilerWorkload& workload,
  int loops,
  const std::string& runner
) {
  FullStackBenchResult result;
  TempFileGuard source_guard;
  TempFileGuard stdin_guard;
  const fs::path source_path = materialize_source_path(workload, source_guard);
  if (source_path.empty()) {
    result.error = "failed to materialize source file";
    return result;
  }

  fs::path stdin_path;
  if (!workload.stdin_text.empty()) {
    stdin_guard.path = make_temp_text_file("styio_bench_stdin_", ".txt", workload.stdin_text);
    stdin_path = stdin_guard.path;
    if (stdin_path.empty()) {
      result.error = "failed to materialize stdin file";
      return result;
    }
  }

  for (int i = 0; i < loops; ++i) {
    std::string cmd =
      shell_quote(runner) + " --file " + shell_quote(source_path.string());
    if (!stdin_path.empty()) {
      cmd += " < " + shell_quote(stdin_path.string());
    }
    cmd += " 2>/dev/null";

    const auto t0 = std::chrono::steady_clock::now();
    CommandCapture capture = run_command_capture_stdout(cmd);
    const auto t1 = std::chrono::steady_clock::now();

    if (capture.raw_status == -1) {
      result.error = "failed to spawn compiler";
      return result;
    }
    if (capture.exit_code != 0) {
      result.error =
        "compiler exited with code " + std::to_string(capture.exit_code);
      return result;
    }

    std::string got = capture.stdout_text;
    normalize_text(got);
    if (got != workload.expected_stdout) {
      result.error =
        "stdout mismatch on iter=" + std::to_string(i)
        + " expected=" + workload.expected_stdout
        + " got=" + got;
      return result;
    }

    result.wall += std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0);
    result.loops_completed += 1;
  }

  return result;
}

MicroBenchResult
run_micro_bench(const MicroBenchSpec& spec, int loops) {
  MicroBenchResult result;
  result.rss_before = read_rss_bytes();
  ensure_llvm_targets_initialized();

  CompilationSession session;
  size_t total_token_count = 0;
  size_t total_token_arena_bytes = 0;
  size_t total_ast_arena_bytes = 0;

  for (int i = 0; i < loops; ++i) {
    try {
      const auto t0 = std::chrono::steady_clock::now();
      session.adopt_tokens(StyioTokenizer::tokenize(spec.workload.src.code));
      const auto t1 = std::chrono::steady_clock::now();

      total_token_count += session.tokens().size();
      total_token_arena_bytes += session.token_arena_bytes();

      if (spec.focus == MicroBenchFocus::Lexer) {
        result.focus_time += std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0);
        session.reset();
        continue;
      }

      session.attach_context(StyioContext::Create(
        spec.workload.src.label,
        spec.workload.src.code,
        spec.workload.src.line_seps,
        session.tokens(),
        false));
      session.attach_ast(parse_main_block_with_engine_latest(
        *session.context(),
        StyioParserEngine::Nightly,
        nullptr));
      const auto t2 = std::chrono::steady_clock::now();
      total_ast_arena_bytes += session.ast_arena_bytes();

      if (spec.focus == MicroBenchFocus::Parser) {
        result.focus_time += std::chrono::duration_cast<std::chrono::nanoseconds>(t2 - t1);
        session.reset();
        continue;
      }

      AstToStyioIRLowerer analyzer;
      analyzer.typeInfer(session.ast());
      session.mark_type_checked();
      const auto t3 = std::chrono::steady_clock::now();

      if (spec.focus == MicroBenchFocus::TypeInfer) {
        result.focus_time += std::chrono::duration_cast<std::chrono::nanoseconds>(t3 - t2);
        session.reset();
        continue;
      }

      session.attach_ir(analyzer.toStyioIR(session.ast()));
      const auto t4 = std::chrono::steady_clock::now();

      if (spec.focus == MicroBenchFocus::Lower) {
        result.focus_time += std::chrono::duration_cast<std::chrono::nanoseconds>(t4 - t3);
        session.reset();
        continue;
      }

      auto jit_or_err = StyioJIT_ORC::Create();
      if (!jit_or_err) {
        llvm::handleAllErrors(
          jit_or_err.takeError(),
          [&](const llvm::ErrorInfoBase& e) { result.error = e.message(); });
        session.mark_failed();
        session.reset();
        return result;
      }
      StyioToLLVM generator(std::move(*jit_or_err));
      session.ir()->toLLVMIR(&generator);
      (void)generator.dump_llvm_ir();
      const auto t5 = std::chrono::steady_clock::now();
      result.focus_time += std::chrono::duration_cast<std::chrono::nanoseconds>(t5 - t4);
    } catch (const std::exception& ex) {
      result.error = ex.what();
      session.mark_failed();
      session.reset();
      return result;
    }

    session.reset();
  }

  result.rss_after = read_rss_bytes();
  result.avg_token_count =
    loops > 0 ? (total_token_count / static_cast<size_t>(loops)) : 0;
  result.avg_token_arena_bytes =
    loops > 0 ? (total_token_arena_bytes / static_cast<size_t>(loops)) : 0;
  result.avg_ast_arena_bytes =
    loops > 0 ? (total_ast_arena_bytes / static_cast<size_t>(loops)) : 0;
  return result;
}

ErrorBenchResult
run_error_bench(
  const ErrorBenchSpec& spec,
  int loops,
  const std::string& runner
) {
  ErrorBenchResult result;
  TempFileGuard source_guard;
  const fs::path source_path = materialize_source_path(spec.workload, source_guard);
  if (source_path.empty()) {
    result.error = "failed to materialize source file";
    return result;
  }

  size_t total_diag_bytes = 0;
  const std::string expected_diag =
    "\"code\":\"" + spec.expected_diag_code + "\"";

  for (int i = 0; i < loops; ++i) {
    std::string cmd =
      shell_quote(runner) + " --error-format jsonl --file "
      + shell_quote(source_path.string()) + " 2>&1 >/dev/null";

    const auto t0 = std::chrono::steady_clock::now();
    CommandCapture capture = run_command_capture_stdout(cmd);
    const auto t1 = std::chrono::steady_clock::now();

    if (capture.raw_status == -1) {
      result.error = "failed to spawn compiler";
      return result;
    }
    if (capture.exit_code != spec.expected_exit_code) {
      result.error =
        "unexpected exit code on iter=" + std::to_string(i)
        + " expected=" + std::to_string(spec.expected_exit_code)
        + " got=" + std::to_string(capture.exit_code)
        + " diag=" + capture.stdout_text;
      return result;
    }
    if (capture.stdout_text.find(expected_diag) == std::string::npos) {
      result.error =
        "missing diagnostic code on iter=" + std::to_string(i)
        + " expected=" + spec.expected_diag_code
        + " diag=" + capture.stdout_text;
      return result;
    }

    result.wall += std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0);
    result.loops_completed += 1;
    total_diag_bytes += capture.stdout_text.size();
  }

  result.avg_diag_bytes =
    loops > 0 ? (total_diag_bytes / static_cast<size_t>(loops)) : 0;
  return result;
}

void
print_full_stack_bench(
  const CompilerWorkload& workload,
  int loops,
  const FullStackBenchResult& result
) {
  const double per_iter_us =
    loops > 0
      ? static_cast<double>(result.wall.count()) / (1000.0 * static_cast<double>(loops))
      : 0.0;
  std::cout
    << "[full-stack-bench] module=" << workload.module_family
    << " label=" << workload.src.label
    << " loops=" << loops
    << " cli_wall_us=" << per_iter_us
    << "\n";
}

void
print_micro_bench(
  const MicroBenchSpec& spec,
  int loops,
  const MicroBenchResult& result
) {
  const double per_iter_us =
    loops > 0
      ? static_cast<double>(result.focus_time.count()) / (1000.0 * static_cast<double>(loops))
      : 0.0;
  const size_t rss_growth =
    result.rss_after > result.rss_before ? (result.rss_after - result.rss_before) : 0;

  std::cout
    << "[micro-bench] focus=" << micro_focus_name(spec.focus)
    << " name=" << spec.name
    << " module=" << spec.workload.module_family
    << " label=" << spec.workload.src.label
    << " loops=" << loops
    << " focus_us=" << per_iter_us
    << " avg_tokens=" << result.avg_token_count
    << " avg_token_arena_kib=" << (static_cast<double>(result.avg_token_arena_bytes) / 1024.0)
    << " avg_ast_arena_kib=" << (static_cast<double>(result.avg_ast_arena_bytes) / 1024.0)
    << " rss_growth_kib=" << (static_cast<double>(rss_growth) / 1024.0)
    << "\n";
}

void
print_error_bench(
  const ErrorBenchSpec& spec,
  int loops,
  const ErrorBenchResult& result
) {
  const double per_iter_us =
    loops > 0
      ? static_cast<double>(result.wall.count()) / (1000.0 * static_cast<double>(loops))
      : 0.0;

  std::cout
    << "[error-bench] category=" << error_category_name(spec.category)
    << " name=" << spec.name
    << " label=" << spec.workload.src.label
    << " loops=" << loops
    << " error_us=" << per_iter_us
    << " exit_code=" << spec.expected_exit_code
    << " diagnostic_code=" << spec.expected_diag_code
    << " avg_diag_bytes=" << result.avg_diag_bytes
    << "\n";
}

::testing::AssertionResult
run_file_cycle(const fs::path& path, int lines, int iter) {
  styio_runtime_clear_error();
  const std::string raw = path.string();
  const int64_t h = styio_file_open(raw.c_str());
  if (h == 0) {
    return ::testing::AssertionFailure() << "open failed, iter=" << iter;
  }

  int read_lines = 0;
  while (const char* line = styio_file_read_line(h)) {
    ++read_lines;
    (void)styio_cstr_to_i64(line);
  }
  if (read_lines != lines) {
    styio_file_close(h);
    return ::testing::AssertionFailure()
           << "line mismatch, iter=" << iter << ", got=" << read_lines
           << ", want=" << lines;
  }

  styio_file_rewind(h);
  const char* first = styio_file_read_line(h);
  if (first == nullptr) {
    styio_file_close(h);
    return ::testing::AssertionFailure() << "rewind/read failed, iter=" << iter;
  }
  if (styio_cstr_to_i64(first) != 0) {
    styio_file_close(h);
    return ::testing::AssertionFailure()
           << "first line mismatch, iter=" << iter
           << ", got=" << styio_cstr_to_i64(first);
  }

  styio_file_close(h);
  styio_file_close(h);
  if (styio_runtime_has_error() != 0) {
    return ::testing::AssertionFailure()
           << "runtime error flag set, iter=" << iter;
  }
  return ::testing::AssertionSuccess();
}

} // namespace

TEST(StyioSoakSingleThread, TokenizerIngestionLoop) {
  const int loops = read_env_i32("STYIO_SOAK_LEXER_ITERS", 120, 1, 200000);
  const int lines = read_env_i32("STYIO_SOAK_INGEST_LINES", 256, 16, 200000);

  std::string src;
  src.reserve(static_cast<size_t>(lines) * 24);
  for (int i = 0; i < lines; ++i) {
    src += "x" + std::to_string(i) + " = " + std::to_string(i) + "\n";
  }

  size_t total_tokens = 0;
  for (int i = 0; i < loops; ++i) {
    auto tokens = StyioTokenizer::tokenize(src);
    ASSERT_FALSE(tokens.empty());
    ASSERT_NE(tokens.back(), nullptr);
    EXPECT_EQ(tokens.back()->type, StyioTokenType::TOK_EOF);
    total_tokens += tokens.size();
    for (auto* tok : tokens) {
      delete tok;
    }
  }

  EXPECT_GT(total_tokens, static_cast<size_t>(loops) * 10);
}

TEST(StyioSoakSingleThread, FileHandleLifecycleLoop) {
  const int loops = read_env_i32("STYIO_SOAK_FILE_ITERS", 200, 1, 500000);
  const int lines = read_env_i32("STYIO_SOAK_FILE_LINES", 64, 1, 100000);

  TempFileGuard tmp { make_temp_line_file("styio_soak_", lines) };
  ASSERT_FALSE(tmp.path.empty());

  for (int i = 0; i < loops; ++i) {
    ASSERT_TRUE(run_file_cycle(tmp.path, lines, i));
  }
}

TEST(StyioSoakSingleThread, FileHandleMemoryGrowthBound) {
  const int loops = read_env_i32("STYIO_SOAK_MEM_ITERS", 600, 50, 500000);
  const int lines = read_env_i32("STYIO_SOAK_MEM_FILE_LINES", 96, 8, 200000);
  const int limit_kib =
    read_env_i32("STYIO_SOAK_RSS_GROWTH_LIMIT_KIB", 65536, 4096, 1024 * 1024);

  TempFileGuard tmp { make_temp_line_file("styio_soak_mem_", lines) };
  ASSERT_FALSE(tmp.path.empty());

  const size_t rss_before = read_rss_bytes();
  if (rss_before == 0) {
    GTEST_SKIP() << "rss probe unavailable on this platform";
  }

  for (int i = 0; i < loops; ++i) {
    ASSERT_TRUE(run_file_cycle(tmp.path, lines, i));
  }

  const size_t rss_after = read_rss_bytes();
  if (rss_after == 0) {
    GTEST_SKIP() << "rss probe unavailable on this platform";
  }

  const size_t growth = (rss_after > rss_before) ? (rss_after - rss_before) : 0;
  const size_t limit_bytes = static_cast<size_t>(limit_kib) * 1024ULL;
  EXPECT_LE(growth, limit_bytes)
    << "rss_before=" << rss_before
    << " rss_after=" << rss_after
    << " growth=" << growth
    << " limit=" << limit_bytes;
}

TEST(StyioSoakSingleThread, ConcatMemoryGrowthBound) {
  const int loops = read_env_i32("STYIO_SOAK_CONCAT_ITERS", 500, 20, 500000);
  const int chain = read_env_i32("STYIO_SOAK_CONCAT_CHAIN", 12, 2, 512);
  const int seg_bytes = read_env_i32("STYIO_SOAK_CONCAT_SEG_BYTES", 96, 8, 65536);
  const int limit_kib =
    read_env_i32("STYIO_SOAK_CONCAT_RSS_GROWTH_LIMIT_KIB", 65536, 4096, 1024 * 1024);

  const std::string segment(static_cast<size_t>(seg_bytes), 'x');
  size_t produced_bytes = 0;

  const size_t rss_before = read_rss_bytes();
  if (rss_before == 0) {
    GTEST_SKIP() << "rss probe unavailable on this platform";
  }

  for (int i = 0; i < loops; ++i) {
    const char* cur = styio_strcat_ab("", "");
    ASSERT_NE(cur, nullptr);
    for (int j = 0; j < chain; ++j) {
      const char* next = styio_strcat_ab(cur, segment.c_str());
      ASSERT_NE(next, nullptr);
      styio_free_cstr(cur);
      cur = next;
    }
    produced_bytes += std::strlen(cur);
    styio_free_cstr(cur);
  }

  EXPECT_GT(produced_bytes, 0U);

  const size_t rss_after = read_rss_bytes();
  if (rss_after == 0) {
    GTEST_SKIP() << "rss probe unavailable on this platform";
  }

  const size_t growth = (rss_after > rss_before) ? (rss_after - rss_before) : 0;
  const size_t limit_bytes = static_cast<size_t>(limit_kib) * 1024ULL;
  EXPECT_LE(growth, limit_bytes)
    << "rss_before=" << rss_before
    << " rss_after=" << rss_after
    << " growth=" << growth
    << " limit=" << limit_bytes
    << " loops=" << loops
    << " chain=" << chain
    << " seg_bytes=" << seg_bytes;
}

TEST(StyioSoakSingleThread, InvalidHandleDiagnosticsLoop) {
  const int loops = read_env_i32("STYIO_SOAK_INVALID_HANDLE_ITERS", 2000, 1, 2000000);
  constexpr int64_t kBaseHandle = 900000000;

  for (int i = 0; i < loops; ++i) {
    const int64_t h = kBaseHandle + static_cast<int64_t>(i);

    styio_runtime_clear_error();
    styio_file_rewind(h);
    EXPECT_EQ(styio_runtime_has_error(), 1) << "iter=" << i;

    styio_runtime_clear_error();
    styio_file_write_cstr(h, "x");
    EXPECT_EQ(styio_runtime_has_error(), 1) << "iter=" << i;

    styio_runtime_clear_error();
    EXPECT_EQ(styio_file_read_line(h), nullptr) << "iter=" << i;
    EXPECT_EQ(styio_runtime_has_error(), 1) << "iter=" << i;

    styio_runtime_clear_error();
    styio_file_close(h);
    EXPECT_EQ(styio_runtime_has_error(), 0) << "iter=" << i;
  }
}

TEST(StyioSoakSingleThread, DictHandleLookupUpdateLoop) {
  const int loops = read_env_i32("STYIO_SOAK_DICT_ITERS", 6, 1, 10000);
  const int keys = read_env_i32("STYIO_SOAK_DICT_KEYS", 12000, 64, 500000);

  std::vector<std::string> names;
  names.reserve(static_cast<size_t>(keys));
  for (int i = 0; i < keys; ++i) {
    names.push_back("dict_key_" + std::to_string(i));
  }

  const long long expected_sum =
    static_cast<long long>(keys - 1) * static_cast<long long>(keys) / 2LL;
  const long long updated_sum =
    expected_sum + 7LL * static_cast<long long>(keys);

  for (int iter = 0; iter < loops; ++iter) {
    styio_runtime_clear_error();
    const int64_t h = styio_dict_new_i64();
    ASSERT_NE(h, 0) << "iter=" << iter;

    for (int i = 0; i < keys; ++i) {
      styio_dict_set_i64(h, names[static_cast<size_t>(i)].c_str(), i);
    }

    ASSERT_EQ(styio_dict_len(h), keys) << "iter=" << iter;

    long long sum = 0;
    for (int i = keys - 1; i >= 0; --i) {
      sum += static_cast<long long>(
        styio_dict_get_i64(h, names[static_cast<size_t>(i)].c_str()));
    }
    EXPECT_EQ(sum, expected_sum) << "iter=" << iter;

    for (int i = 0; i < keys; ++i) {
      styio_dict_set_i64(h, names[static_cast<size_t>(i)].c_str(), i + 7);
    }

    long long sum_after = 0;
    for (int i = 0; i < keys; ++i) {
      sum_after += static_cast<long long>(
        styio_dict_get_i64(h, names[static_cast<size_t>(i)].c_str()));
    }
    EXPECT_EQ(sum_after, updated_sum) << "iter=" << iter;

    styio_dict_release(h);
    EXPECT_EQ(styio_dict_active_count(), 0) << "iter=" << iter;
    EXPECT_EQ(styio_runtime_has_error(), 0) << "iter=" << iter;
  }
}

TEST(StyioSoakSingleThread, StreamProgramLoop) {
  const int loops = read_env_i32("STYIO_SOAK_STREAM_ITERS", 20, 1, 100000);
  const fs::path src =
    fs::path(STYIO_SOURCE_DIR) / "tests" / "features" / "state_resources" / "t02_running_max.styio";
  const fs::path exp =
    fs::path(STYIO_SOURCE_DIR) / "tests" / "features" / "state_resources" / "expected" / "t02_running_max.out";

  const std::string runner = compiler_runner_path();
  ASSERT_FALSE(runner.empty());
  ASSERT_TRUE(fs::exists(src));
  ASSERT_TRUE(fs::exists(exp));

  std::string expected = read_text_file(exp);
  ASSERT_FALSE(expected.empty());
  normalize_text(expected);

  const std::string cmd =
    shell_quote(runner) + " --file " + shell_quote(src.string()) + " 2>/dev/null";

  for (int i = 0; i < loops; ++i) {
    std::string out = capture_stdout(cmd);
    normalize_text(out);
    EXPECT_EQ(out, expected) << "iter=" << i;
  }
}

TEST(StyioSoakSingleThread, StateInlineHelperProgramLoop) {
  const int loops = read_env_i32("STYIO_SOAK_STATE_INLINE_ITERS", 40, 1, 100000);
  const std::string runner = compiler_runner_path();
  ASSERT_FALSE(runner.empty());

  const CompilerWorkload workload = make_state_inline_workload("state_inline_helper_loop");
  TempFileGuard source_guard;
  const fs::path source_path = materialize_source_path(workload, source_guard);
  ASSERT_FALSE(source_path.empty());

  const std::string cmd =
    shell_quote(runner) + " --file " + shell_quote(source_path.string()) + " 2>/dev/null";
  for (int i = 0; i < loops; ++i) {
    std::string out = capture_stdout(cmd);
    normalize_text(out);
    EXPECT_EQ(out, workload.expected_stdout) << "iter=" << i;
  }
}

TEST(StyioSoakSingleThread, StateInlineMatchCasesProgramLoop) {
  const int loops = read_env_i32("STYIO_SOAK_STATE_MATCH_ITERS", 40, 1, 100000);
  const std::string runner = compiler_runner_path();
  ASSERT_FALSE(runner.empty());

  std::string code;
  code += "@out : i64|..3|\n";
  code += "[1, 2, 3] >> #(v) => {\n";
  code += "  next = v ?= {\n";
  code += "    1 => { <| @out[-1] + 10 }\n";
  code += "    _ => { <| @out[-1] + v }\n";
  code += "  }\n";
  code += "  next -> @out\n";
  code += "  >_(next)\n";
  code += "}\n";
  const CompilerWorkload workload = make_inline_workload(
    "StateAndSeries",
    "state_inline_matchcases_loop",
    code,
    "10\n12\n15\n");

  TempFileGuard source_guard;
  const fs::path source_path = materialize_source_path(workload, source_guard);
  ASSERT_FALSE(source_path.empty());

  const std::string cmd =
    shell_quote(runner) + " --file " + shell_quote(source_path.string()) + " 2>/dev/null";
  for (int i = 0; i < loops; ++i) {
    std::string out = capture_stdout(cmd);
    normalize_text(out);
    EXPECT_EQ(out, workload.expected_stdout) << "iter=" << i;
  }
}

TEST(StyioSoakSingleThread, StateInlineInfiniteProgramLoop) {
  const int loops = read_env_i32("STYIO_SOAK_STATE_INFINITE_ITERS", 40, 1, 100000);
  const std::string runner = compiler_runner_path();
  ASSERT_FALSE(runner.empty());

  std::string code;
  code += "@out : i64|..2|\n";
  code += "[1, 2] >> #(v) => {\n";
  code += "  0 -> @out\n";
  code += "  >_(0)\n";
  code += "}\n";
  const CompilerWorkload workload = make_inline_workload(
    "StateAndSeries",
    "state_inline_infinite_loop",
    code,
    "0\n0\n");

  TempFileGuard source_guard;
  const fs::path source_path = materialize_source_path(workload, source_guard);
  ASSERT_FALSE(source_path.empty());

  const std::string cmd =
    shell_quote(runner) + " --file " + shell_quote(source_path.string()) + " 2>/dev/null";
  for (int i = 0; i < loops; ++i) {
    std::string out = capture_stdout(cmd);
    normalize_text(out);
    EXPECT_EQ(out, workload.expected_stdout) << "iter=" << i;
  }
}

TEST(StyioSoakSingleThread, FrontendPhaseBreakdownReport) {
  const int loops = read_env_i32("STYIO_SOAK_PHASE_BENCH_ITERS", 0, 0, 200000);
  if (loops == 0) {
    GTEST_SKIP() << "set STYIO_SOAK_PHASE_BENCH_ITERS to run opt-in compiler stage benchmark";
  }

  const std::vector<CompilerWorkload> workloads = build_compiler_workloads();
  ASSERT_FALSE(workloads.empty());

  for (const CompilerWorkload& workload : workloads) {
    ASSERT_FALSE(workload.src.code.empty()) << workload.src.label;
    const CompilerStageBenchResult result = run_compiler_stage_bench(workload, loops);
    ASSERT_TRUE(result.error.empty()) << workload.src.label << ": " << result.error;
    print_compiler_stage_bench(workload, loops, result);
  }
}

TEST(StyioSoakSingleThread, FullStackWorkloadMatrixReport) {
  const int loops = read_env_i32("STYIO_SOAK_EXECUTE_BENCH_ITERS", 0, 0, 100000);
  if (loops == 0) {
    GTEST_SKIP() << "set STYIO_SOAK_EXECUTE_BENCH_ITERS to run opt-in full-stack benchmark";
  }

  const std::string runner = compiler_runner_path();
  ASSERT_FALSE(runner.empty());

  const std::vector<CompilerWorkload> workloads = build_compiler_workloads();
  ASSERT_FALSE(workloads.empty());

  for (const CompilerWorkload& workload : workloads) {
    if (!workload.run_full_stack) {
      continue;
    }
    const FullStackBenchResult result = run_full_stack_bench(workload, loops, runner);
    ASSERT_TRUE(result.error.empty()) << workload.src.label << ": " << result.error;
    ASSERT_EQ(result.loops_completed, loops) << workload.src.label;
    print_full_stack_bench(workload, loops, result);
  }
}

TEST(StyioSoakSingleThread, CompilerMicroBenchmarksReport) {
  const int loops = read_env_i32("STYIO_SOAK_MICRO_BENCH_ITERS", 0, 0, 200000);
  if (loops == 0) {
    GTEST_SKIP() << "set STYIO_SOAK_MICRO_BENCH_ITERS to run opt-in compiler micro benchmarks";
  }

  const std::vector<MicroBenchSpec> specs = build_micro_bench_specs();
  ASSERT_FALSE(specs.empty());

  for (const MicroBenchSpec& spec : specs) {
    ASSERT_FALSE(spec.workload.src.code.empty()) << spec.name;
    const MicroBenchResult result = run_micro_bench(spec, loops);
    ASSERT_TRUE(result.error.empty()) << spec.name << ": " << result.error;
    print_micro_bench(spec, loops, result);
  }
}

TEST(StyioSoakSingleThread, CompilerErrorPathBenchmarksReport) {
  const int loops = read_env_i32("STYIO_SOAK_ERROR_BENCH_ITERS", 0, 0, 100000);
  if (loops == 0) {
    GTEST_SKIP() << "set STYIO_SOAK_ERROR_BENCH_ITERS to run opt-in compiler error-path benchmarks";
  }

  const std::string runner = compiler_runner_path();
  ASSERT_FALSE(runner.empty());

  const std::vector<ErrorBenchSpec> specs = build_error_bench_specs();
  ASSERT_FALSE(specs.empty());

  for (const ErrorBenchSpec& spec : specs) {
    ASSERT_FALSE(spec.workload.src.code.empty()) << spec.name;
    const ErrorBenchResult result = run_error_bench(spec, loops, runner);
    ASSERT_TRUE(result.error.empty()) << spec.name << ": " << result.error;
    ASSERT_EQ(result.loops_completed, loops) << spec.name;
    print_error_bench(spec, loops, result);
  }
}
