"""Test harness library.

Public API:

    @stub             — decorator that marks a function as a placeholder.
    parse(text)       — parse a plan-file string into Steps.
    run_text(text)    — run an inline plan.
    run_plan_file(p)  — run a plan file (used by the CLI).
    discover_plans(d, lib_filter, mode) — enumerate plan files.
    plan_glob_for_mode(mode) — the plan-filename glob a mode selects.
    check_stub_coverage(d) — verify @stub/test_stub_<fn>.txt pairing.
    learned_text(text) — run a plan and return the learn-mode rewritten text.

Helpers used by the self-test plan:

    count_stanzas, result_status, result_stubbed_calls,
    result_step_count, result_first_message
"""
from libs.test_harness.parser import parse, ParseError
from libs.test_harness.stub import stub, is_stub
from libs.test_harness.harness import (
    PlanResult,
    StepResult,
    CoverageReport,
    HARNESS_MASTER_SEED,
    check_stub_coverage,
    count_stanzas,
    coverage_ok,
    discover_plans,
    learned_text,
    plan_glob_for_mode,
    result_first_message,
    result_status,
    result_step_count,
    result_stubbed_calls,
    run_plan_file,
    run_text,
)

__all__ = [
    "PlanResult",
    "StepResult",
    "CoverageReport",
    "HARNESS_MASTER_SEED",
    "ParseError",
    "stub",
    "is_stub",
    "parse",
    "check_stub_coverage",
    "count_stanzas",
    "coverage_ok",
    "discover_plans",
    "learned_text",
    "plan_glob_for_mode",
    "result_first_message",
    "result_status",
    "result_step_count",
    "result_stubbed_calls",
    "run_plan_file",
    "run_text",
]
