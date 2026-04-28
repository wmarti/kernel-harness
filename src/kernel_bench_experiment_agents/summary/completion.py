"""Compute derived completion fields such as measured outcomes and audit annotations.

Status-writing commands call into this module so completion payload semantics stay consistent across solver and launcher exits.
"""

from __future__ import annotations

from typing import Any

from kernel_bench_experiment_agents.kernelbench.metrics import suspicious_attempt_count
from kernel_bench_experiment_agents.runtime.common import as_float


def infer_measured_outcome(goal_status: dict[str, Any] | None) -> str:
    if not isinstance(goal_status, dict):
        return "unknown"
    if not bool(goal_status.get("has_correct_solution")):
        return "no_correct_candidate"
    beats_eager = bool(goal_status.get("beats_eager"))
    beats_compile = bool(goal_status.get("beats_compile"))
    if beats_eager and beats_compile:
        return "beats_both"
    if beats_eager:
        return "beats_eager_only"
    if beats_compile:
        return "beats_compile_only"
    return "beats_none"


def substantial_budget_remaining(snapshot: dict[str, Any]) -> bool:
    remaining_minutes = as_float(snapshot.get("remaining_minutes"))
    time_budget_minutes = as_float(snapshot.get("time_budget_minutes"))
    if remaining_minutes is None or time_budget_minutes is None:
        return False
    return remaining_minutes > max(60.0, time_budget_minutes * 0.25)


def apply_trace_audit_to_completion(
    completion_payload: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    completion_payload["audit"] = audit
    trace_counts = audit.get("trace_counts")
    if isinstance(trace_counts, dict):
        completion_payload["trace_counts"] = trace_counts
    if audit.get("valid", True):
        if (
            completion_payload.get("terminal_state") == "harness_failure"
            and completion_payload.get("reported_terminal_state")
            and str(completion_payload.get("summary") or "").startswith("invalidated by trace audit:")
        ):
            completion_payload["terminal_state"] = completion_payload["reported_terminal_state"]
            if "reported_summary" in completion_payload:
                completion_payload["summary"] = completion_payload["reported_summary"]
            if "reported_success" in completion_payload:
                completion_payload["success"] = completion_payload["reported_success"]
        return completion_payload

    completion_payload.setdefault(
        "reported_terminal_state",
        completion_payload.get("terminal_state") or completion_payload.get("solver_state"),
    )
    completion_payload.setdefault("reported_summary", completion_payload.get("summary"))
    completion_payload.setdefault("reported_success", completion_payload.get("success"))
    completion_payload["terminal_state"] = "harness_failure"
    completion_payload["success"] = False
    completion_payload["summary"] = f"invalidated by trace audit: {audit.get('summary')}"
    return completion_payload


def annotate_completion_outcomes(
    completion_payload: dict[str, Any],
    *,
    sample_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    goal_status = completion_payload.get("goal_status")
    if isinstance(goal_status, dict):
        raw_best_runtime = as_float(goal_status.get("best_correct_runtime_ms"))
        raw_beats_eager = bool(goal_status.get("beats_eager"))
        raw_beats_compile = bool(goal_status.get("beats_compile"))
        raw_beats_both = bool(goal_status.get("beats_both"))
    else:
        raw_best_runtime = None
        raw_beats_eager = False
        raw_beats_compile = False
        raw_beats_both = False

    completion_payload["raw_best_correct_runtime_ms"] = raw_best_runtime
    completion_payload["raw_beats_eager"] = raw_beats_eager
    completion_payload["raw_beats_compile"] = raw_beats_compile
    completion_payload["raw_beats_both"] = raw_beats_both
    completion_payload["outside_harness_success"] = raw_beats_both
    if sample_entries is not None:
        suspicious_attempts = suspicious_attempt_count(sample_entries)
    else:
        suspicious_attempts = int(
            completion_payload.get("kernelbench_hacked_kernel_attempt_warnings") or 0
        )
    completion_payload["kernelbench_hacked_kernel_attempt_warnings"] = suspicious_attempts
    completion_payload["measured_outcome"] = infer_measured_outcome(
        goal_status if isinstance(goal_status, dict) else None
    )
    if completion_payload.get("success") is None:
        completion_payload["success"] = completion_payload["measured_outcome"] == "beats_both"
    return completion_payload
