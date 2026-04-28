"""Build and render the live per-problem goal status that the solver re-reads after each step.

This module bridges archived attempts, profiler outputs, and live trace counts back into workspace-facing status files.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kernel_bench_experiment_agents.workspace.archive import (
    goal_status_archive_path,
    mcp_trace_events_path,
    profile_manifest_entries,
    sample_manifest_entries,
    trace_events_path,
)
from kernel_bench_experiment_agents.runtime.common import as_float
from kernel_bench_experiment_agents.runtime.live_gpu_wait import active_live_gpu_wait_seconds
from kernel_bench_experiment_agents.mcp.trace import load_mcp_ir_events
from kernel_bench_experiment_agents.runtime.project import now_iso, write_json, write_text
from kernel_bench_experiment_agents.kernelbench.metrics import (
    best_correct_payload,
    blocked_run_reason,
    candidate_runtime,
    payload_counts_toward_progress,
    sum_numeric_field,
)
from kernel_bench_experiment_agents.trace.analysis import trace_counts, web_searches_from_ir
from kernel_bench_experiment_agents.trace.ir import load_trace_event_entries, materialize_trace_ir
from kernel_bench_experiment_agents.workspace.paths import (
    load_workspace_baseline,
    load_workspace_metadata,
    write_workspace_best_sample,
)
from kernel_bench_experiment_agents.agent_contract.prompts import render_goal_status_markdown


def _elapsed_minutes_since(started_at: Any) -> float | None:
    if not isinstance(started_at, str) or not started_at.strip():
        return None
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return max(0.0, (now - started.astimezone(timezone.utc)).total_seconds() / 60.0)


def _attempt_warnings(payload: dict[str, Any] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("warnings")
    if isinstance(raw, list):
        return [str(value) for value in raw if value]
    return []



def live_trace_counts_for_problem(
    run_name: str,
    level: int,
    problem_id: int,
    *,
    tool: str = "codex",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_events, raw_event_entries = load_trace_event_entries(
        trace_events_path(run_name, level, problem_id)
    )
    ir_events = materialize_trace_ir(raw_event_entries, tool=tool)
    ir_events.extend(
        load_mcp_ir_events(
            mcp_trace_events_path(run_name, level, problem_id),
            starting_line=len(raw_event_entries) + 1_000_000,
        )
    )
    return (
        trace_counts(ir_events, raw_events=raw_events, tool=tool),
        web_searches_from_ir(ir_events),
    )


def goal_status_snapshot(
    *,
    run_name: str,
    level: int,
    problem_id: int,
    workspace: Path,
) -> dict[str, Any]:
    """Collect the current archived measurements and trace-derived activity into one live snapshot."""
    metadata = load_workspace_metadata(workspace)
    baseline = load_workspace_baseline(workspace)
    entries = sample_manifest_entries(run_name, level, problem_id)
    profiles = profile_manifest_entries(run_name, level, problem_id)
    latest_attempt = entries[-1] if entries else None
    latest_attempt_sample_id = latest_attempt.get("sample_id") if isinstance(latest_attempt, dict) else None
    latest_attempt_blocked_reason = blocked_run_reason(latest_attempt) if isinstance(latest_attempt, dict) else None
    latest_attempt_counts_toward_progress = payload_counts_toward_progress(latest_attempt) if isinstance(latest_attempt, dict) else True
    progress_entries = [payload for payload in entries if payload_counts_toward_progress(payload)]
    best_payload = best_correct_payload(progress_entries)
    best_runtime_ms = None
    best_sample_id = None
    if best_payload is not None:
        result = best_payload.get("result")
        if isinstance(result, dict):
            best_runtime_ms = candidate_runtime(result)
        best_sample_id = best_payload.get("sample_id")

    eager_ms = as_float(baseline.get("eager", {}).get("runtime_ms"))
    compile_ms = as_float(baseline.get("compile", {}).get("runtime_ms"))
    best_result_warnings = _attempt_warnings(best_payload)
    beats_eager = best_runtime_ms is not None and eager_ms is not None and best_runtime_ms < eager_ms
    beats_compile = best_runtime_ms is not None and compile_ms is not None and best_runtime_ms < compile_ms

    num_attempts = len(progress_entries)
    num_correct_attempts = sum(
        1
        for payload in progress_entries
        if isinstance(payload.get("result"), dict)
        and bool(payload["result"].get("correctness"))
    )
    num_incorrect_attempts = sum(
        1
        for payload in progress_entries
        if isinstance(payload.get("result"), dict)
        and payload["result"].get("correctness") is False
    )
    num_execution_failed_attempts = sum(
        1 for payload in progress_entries if payload.get("status") == "failed"
    )
    num_other_attempts = max(
        0,
        num_attempts - num_correct_attempts - num_incorrect_attempts - num_execution_failed_attempts,
    )
    timing_runs = sum(
        1
        for payload in progress_entries
        if isinstance(payload.get("result"), dict)
        and candidate_runtime(payload["result"]) is not None
    )
    recorded_gpu_wait_minutes = (
        sum_numeric_field(entries, "gpu_wait_seconds")
        + sum_numeric_field(profiles, "gpu_wait_seconds")
    ) / 60.0
    live_gpu_wait_minutes = (
        active_live_gpu_wait_seconds(run_name, level, problem_id) / 60.0
    )
    gpu_wait_minutes_total = recorded_gpu_wait_minutes + live_gpu_wait_minutes
    started_at = metadata.get("created_at")
    wall_clock_elapsed_minutes = _elapsed_minutes_since(started_at)
    budget_minutes = as_float(metadata.get("time_budget_minutes"))
    counted_elapsed_minutes = None
    if budget_minutes is not None and wall_clock_elapsed_minutes is not None:
        counted_elapsed_minutes = max(0.0, wall_clock_elapsed_minutes - gpu_wait_minutes_total)
        remaining_minutes = max(0.0, budget_minutes - counted_elapsed_minutes)
    else:
        remaining_minutes = None

    tool = str(metadata.get("tool") or "codex")
    live_trace_counts, live_web_searches = live_trace_counts_for_problem(
        run_name,
        level,
        problem_id,
        tool=tool,
    )
    resolved = beats_eager and beats_compile
    recommended_actions = []
    if latest_attempt_blocked_reason and not resolved:
        if latest_attempt_blocked_reason.startswith("candidate rejected by harness validation:"):
            recommended_actions.append(
                "The latest attempted run does not count toward progress. The harness rejected the candidate before evaluation. Fix the exact validation error from the tool output and try again."
            )
        else:
            recommended_actions.append(
                "The latest attempted run does not count toward progress. KernelBench flagged it as suspicious or cheating. Discard it and keep iterating until you have a clean measured win."
            )
    if resolved:
        recommended_actions.append(
            "Re-check SPEC.md once, then end via the `complete_problem` command tool with a short success summary."
        )
    else:
        recommended_actions.extend(
            [
                "Keep iterating until both baselines are beaten or another truthful terminal state is justified.",
                "Act as the planner-manager. Keep the main context focused on strategy and delegate measured evaluation to `runner` and Nsight profiling to `profiler` whenever those helper agents are available.",
                "Re-read SPEC.md and HARDWARE.md before each major strategy change.",
                "WHEN you are stuck or a candidate is slower than expected, use `profile_ncu`; read `profiles/latest.summary.txt` first, then `profiles/latest.details.txt` if needed.",
                "WHEN the next optimization idea depends on NVIDIA-specific behavior, use hosted web search on docs.nvidia.com only for topics like tensor cores, WMMA, async copy/pipelining, occupancy, bank conflicts, and memory hierarchy limits. Other domains are blocked by policy.",
                "WHEN you are choosing the next branch, inspect `samples/` and `profiles/` so you do not retry the same failed idea.",
                "Do not end with a plain assistant message. The only valid exit path is the `complete_problem` command tool.",
                "Never overlap command tool calls. Start a new harness tool call only after the previous one has fully returned.",
                "`run_candidate` and `profile_ncu` may take a while; wait for the tool result instead of treating them as hung.",
            ]
        )

    return {
        "generated_at": now_iso(),
        "started_at": started_at,
        "run_name": run_name,
        "level": level,
        "problem_id": problem_id,
        "tool": tool,
        "problem_name": metadata.get("problem_name"),
        "time_budget_minutes": budget_minutes,
        "wall_clock_elapsed_minutes": wall_clock_elapsed_minutes,
        "elapsed_minutes": counted_elapsed_minutes,
        "recorded_gpu_wait_minutes": recorded_gpu_wait_minutes,
        "live_gpu_wait_minutes": live_gpu_wait_minutes,
        "gpu_wait_minutes_total": gpu_wait_minutes_total,
        "remaining_minutes": remaining_minutes,
        "status_mode": "resolved" if resolved else "unresolved",
        "solver_should_continue": not resolved,
        "num_attempts": num_attempts,
        "num_correct_attempts": num_correct_attempts,
        "num_incorrect_attempts": num_incorrect_attempts,
        "num_execution_failed_attempts": num_execution_failed_attempts,
        "num_other_attempts": num_other_attempts,
        "num_timing_runs": timing_runs,
        "num_profile_runs": len(profiles),
        "best_correct_sample_id": best_sample_id,
        "best_correct_runtime_ms": best_runtime_ms,
        "eager_baseline_ms": eager_ms,
        "compile_baseline_ms": compile_ms,
        "beats_eager": beats_eager,
        "beats_compile": beats_compile,
        "beats_both": resolved,
        "best_result_warnings": best_result_warnings,
        "has_correct_solution": best_payload is not None,
        "latest_attempt_sample_id": latest_attempt_sample_id,
        "latest_attempt_counts_toward_progress": latest_attempt_counts_toward_progress,
        "latest_attempt_blocked_reason": latest_attempt_blocked_reason,
        "trace_counts": live_trace_counts,
        "web_searches": live_web_searches,
        "static_docs": ["AGENTS.md", "SPEC.md", "HARDWARE.md"],
        "live_docs": ["GOAL_STATUS.md", "goal_status.json"],
        "workspace_mirrors": {
            "samples_dir": "samples/",
            "best_sample": "samples/best_sample.py",
            "best_result": "samples/best_result.json",
            "profiles_dir": "profiles/",
            "latest_profile_summary": "profiles/latest.summary.txt",
            "latest_profile_details": "profiles/latest.details.txt",
        },
        "recommended_actions": recommended_actions,
    }


def goal_status_markdown(snapshot: dict[str, Any]) -> str:
    """Render the live goal-status markdown that the solver re-reads during the loop."""
    return render_goal_status_markdown(snapshot)


def write_goal_status_files(
    *,
    run_name: str,
    level: int,
    problem_id: int,
    workspace: Path,
) -> dict[str, Any]:
    snapshot = goal_status_snapshot(
        run_name=run_name,
        level=level,
        problem_id=problem_id,
        workspace=workspace,
    )
    write_workspace_best_sample(
        workspace,
        best_correct_payload(sample_manifest_entries(run_name, level, problem_id)),
    )
    write_json(workspace / "goal_status.json", snapshot)
    write_text(workspace / "GOAL_STATUS.md", goal_status_markdown(snapshot))
    write_json(goal_status_archive_path(run_name, level, problem_id), snapshot)
    return snapshot
