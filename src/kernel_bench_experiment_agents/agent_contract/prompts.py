"""Render the solver-facing markdown and helper prompt text for one workspace.

Keeping the long agent-facing strings here makes the contract easier to review without mixing them with snapshot logic.
"""

from __future__ import annotations

from typing import Any

from kernel_bench_experiment_agents.runtime.common import as_float
from kernel_bench_experiment_agents.kernelbench.candidate.contract import CANDIDATE_FILENAME
from kernel_bench_experiment_agents.agent_contract.policy import COMMAND_MCP_SERVER_NAME, LAUNCHER_TERMINAL_STATES


def render_workspace_agents_md(*, contract: dict[str, Any]) -> str:
    assignment = contract["assignment"]
    behavior = contract.get("behavior") or {}
    helper_names = ", ".join(f"`{name}`" for name in contract.get("helper_agents", []))
    precision = assignment.get("precision") or "bf16"
    lines = [
        "# Solver Instructions",
        "",
        "You are the autonomous solver for exactly one optimization problem.",
        "",
        "Assignment:",
        "",
        f"- run name: `{assignment['run_name']}`",
        f"- level: `{assignment['level']}`",
        f"- problem id: `{assignment['problem_id']}`",
        f"- dataset source: `{assignment['dataset_src']}`",
        f"- problem name: `{assignment.get('problem_name') or 'unknown'}`",
        f"- reported GPU name: `{assignment.get('gpu_name') or 'not provided'}`",
        f"- available GPU slots for measured tool execution: `{assignment.get('num_gpus')}`",
        f"- total solver budget: `{assignment.get('time_budget_minutes')}` minutes",
        f"- judged precision path: `{precision}`",
        "",
        "Read order:",
        "",
        "1. `AGENTS.md`",
        "2. `SPEC.md`",
        "3. `HARDWARE.md`",
        "4. `GOAL_STATUS.md`",
        "",
        "Scope:",
        "",
        "- your current working directory is the problem workspace",
        "- read workspace files directly with native file tools",
        f"- edit only `{CANDIDATE_FILENAME}`; the filesystem sandbox only permits writes to that file",
        f"- use only the `{COMMAND_MCP_SERVER_NAME}` command MCP tools for measured runs, profiling, status, best-result lookup, and completion",
        "- do not inspect repository-maintainer docs, hidden harness storage, or tool-private config state",
        "- do not inspect generated PTX, cubins, Triton output, Inductor output, or compiler-emitted kernels for solution ideas",
        "- use `problem_reference.py` as the problem reference",
        f"- the judged path is `{precision}`; internal mixed precision is allowed only if the final candidate still passes the `{precision}` correctness checks",
        "",
        f"Allowed `{COMMAND_MCP_SERVER_NAME}` command tools:",
        "",
        *[
            f"- `{tool['name']}` — {tool['purpose']}"
            for tool in contract["command_tools"]
        ],
        "",
        "Tool policy:",
        "",
        "- `run_candidate` validates the full candidate file for required custom CUDA/C++ extension code and forbidden shortcuts before executing it; fix the exact violation it reports",
        "- `complete_problem` is the only harness tool that accepts solver-supplied completion text",
        "- never overlap harness tool calls; start a new one only after the previous one has returned",
        "- `run_candidate` and `profile_ncu` may take a while; trust them and wait for them to return instead of treating them as hung",
        "- fixed docs/code are local workspace files; history browsing is limited to `samples/` and `profiles/`",
        "- if a measured run is reported as suspicious or cheating, it does not count toward progress; discard it and keep working",
        "",
        "Allowed local reads:",
        "",
        *[f"- `{path}`" for path in contract["reads"]],
        "",
        "Allowed local edits:",
        "",
        *[f"- `{path}`" for path in contract["edits"]],
        "",
        "Web policy:",
        "",
        "- hosted `WebSearch` and `WebFetch` are permission-restricted to `docs.nvidia.com` only; any other domain will be blocked",
        "- do not use shell networking at all",
        "- do not use online code, papers, forums, or blogs for solution ideas",
        "",
        "Planner / helper-agent policy:",
        "",
        f"- if your runtime exposes generated helper agents, act as the planner-manager and use them proactively by default: {helper_names}",
        "- WHEN you want a measured evaluation, spawn `runner` so the main context does not get polluted by run output",
        "- WHEN you want Nsight Compute output or profile interpretation, spawn `profiler` so the main context stays focused on design decisions",
        "- use direct command-tool run/profile calls yourself only when helper spawning is unavailable",
        "- helper agents are execution aids, not a reason to stop or ask the user for approval",
        "",
        "Completion:",
        "",
        "- the solver may terminate only through `complete_problem(summary=...)`",
        "- solver-written completion is always recorded as `done`; the harness infers the measured outcome from actual artifacts",
        "- `budget_exhausted` and `failed_to_generate` are launcher-only states",
        "- post-hoc harness invalidation is handled by archive policy, not by solver-supplied terminal states",
        "- do not end with a plain assistant message",
        "",
        "Standing orders:",
        "",
        *[f"- {value}" for value in list(behavior.get("standing_orders") or [])],
        "",
        "When stuck:",
        "",
        *[f"- {value}" for value in list(behavior.get("stuck_protocol") or [])],
        "",
        "Rules:",
        "",
        "- LOOP UNTIL DONE. DO NOT STOP EARLY.",
        "- every measured attempt must go through `run_candidate`",
        "- profiling is a normal tool, not a last resort",
        "- trust tool output; do not monitor progress with `ps`, `pgrep`, `top`, `htop`, `nvidia-smi`, `strace`, `/proc`, or build-tree inspection",
        "- `HARDWARE.md` is the supported hardware surface; do not probe the machine yourself",
        "- do not use ad hoc shell, Python, or benchmarking commands outside the exposed harness tools",
        "- re-read `SPEC.md`, `HARDWARE.md`, and `GOAL_STATUS.md` before any major strategy change and before any termination decision",
        "- keep working until both baselines are beaten or a truthful terminal state is reached",
    ]
    return "\n".join(lines) + "\n"


def render_workspace_spec_md(
    *,
    problem_name: str | None,
    metadata: dict[str, Any],
    baseline: dict[str, Any],
    hardware_markdown_name: str,
) -> str:
    precision = metadata.get("precision", "bf16")
    lines = [
        "# Orders",
        "",
        "You are optimizing one problem. The harness decides the measured outcome from actual runs; your job is to keep iterating until you have either solved the problem or truthfully exhausted the allowed stopping conditions.",
        "",
        "## Target",
        "",
        f"- problem: `{problem_name or 'unknown'}` (level `{metadata['level']}`, problem `{metadata['problem_id']}`)",
        f"- eager PyTorch baseline: `{baseline['eager']['runtime_ms']}` ms",
        f"- `torch.compile` baseline: `{baseline['compile']['runtime_ms']}` ms",
        "- the strongest outcome is to beat both baselines with one correct candidate",
        f"- optimize `problem_reference.py` by editing only `{CANDIDATE_FILENAME}`",
        "- the evaluated implementation must be raw custom CUDA/C++ extension code with minimal glue; cuBLAS, CUTLASS, Triton, ATen compute helpers, and extra CUDA streams are forbidden",
        f"- correctness and runtime are evaluated on the harness `{precision}` path",
        "",
        "## Autonomy",
        "",
        "- there is no human confirmation step during the run",
        "- do not ask whether to proceed, whether a plan is acceptable, or whether the user wants you to continue",
        "- if one attempt fails, make the next plan yourself and continue",
        "- if stuck, re-read the local docs, use profiling, consult allowed NVIDIA docs, and try another implementation branch",
        "",
        "## Tool loop",
        "",
        "1. Read the fixed problem docs/resources from the workspace.",
        f"2. Overwrite `{CANDIDATE_FILENAME}`.",
        "3. Run `run_candidate`.",
        "4. Read `GOAL_STATUS.md` again or run `goal_status`.",
        "5. If needed, run `profile_ncu` and read `profiles/latest.summary.txt` first.",
        "6. Repeat until `complete_problem` is justified.",
        "",
        "Never overlap harness tool calls. Start a new one only after the previous one has fully returned.",
        "`run_candidate` and `profile_ncu` may take a while; trust them and wait for the result instead of treating them as hung.",
        "If `run_candidate` reports a validation or cheating-related rejection, fix the exact issue it names; that attempt does not count.",
        "",
        "## Termination",
        "",
        "The only valid exit path is `complete_problem(summary=...)`.",
        "",
        "Solver-written completion:",
        "",
        "- use `complete_problem(summary=...)` when you believe the current search is complete",
        "- the harness records that as `done` and infers whether you beat eager, compile, both, or neither from measured artifacts",
        "",
        "Launcher-only terminal states:",
        "",
        *[f"- `{state}`" for state in LAUNCHER_TERMINAL_STATES],
        "",
        "## Budget and status",
        "",
        f"- total budget: `{metadata['time_budget_minutes']}` minutes",
        "- remaining budget: read `GOAL_STATUS.md` or call `goal_status` through the harness tools",
        "- the budget clock is wall time since workspace creation minus recorded GPU wait time and any live GPU lease wait currently in progress",
        "- recorded GPU lock wait time and an active GPU lease wait in progress are excluded from the budget",
        "- failed attempts are normal; they are not a stop signal",
        "",
        "## References",
        "",
        "- problem code: `problem_reference.py`",
        f"- solution file: `{CANDIDATE_FILENAME}`",
        f"- hardware facts: `{hardware_markdown_name}`",
        "- live status: `GOAL_STATUS.md`",
        "- local mirrors of measured attempts/profiles: `samples/` and `profiles/`",
        "- machine-readable metadata is available in local `problem.json`, `hardware.json`, and `workspace_contract.json`",
    ]
    return "\n".join(lines) + "\n"


def render_initial_prompt(*, contract: dict[str, Any], baseline: dict[str, Any]) -> str:
    assignment = contract["assignment"]
    precision = assignment.get("precision") or "bf16"
    lines = [
        "Optimize exactly one problem.",
        "",
        f"- run name: {assignment['run_name']}",
        f"- level: {assignment['level']}",
        f"- problem id: {assignment['problem_id']}",
        f"- dataset source: {assignment['dataset_src']}",
        f"- problem name: {assignment.get('problem_name') or 'unknown'}",
        f"- eager baseline: {baseline['eager']['runtime_ms']} ms",
        f"- compile baseline: {baseline['compile']['runtime_ms']} ms",
        f"- total solver budget: {assignment.get('time_budget_minutes')} minutes",
        f"- judged precision path: {precision}",
        "",
        "Start by reading `AGENTS.md`, `INITIAL_PROMPT.md`, `SPEC.md`, `HARDWARE.md`, and `GOAL_STATUS.md` from the workspace.",
        f"Stay inside the workspace. Only edit `{CANDIDATE_FILENAME}`. Use `run_candidate`, `profile_ncu`, `goal_status`, `best_result`, and `complete_problem` for measured harness actions.",
        "Act as the planner-manager. Keep the main context focused on strategy and decision-making.",
        "WHEN you want a measured evaluation, spawn the `runner` helper if available. WHEN you want profiling or profile interpretation, spawn the `profiler` helper if available. Fall back to direct command calls only when helper spawning is unavailable.",
        "Work independently. There is no user approval step in this run. Do not ask for permission, confirmation, or whether to continue.",
        "The benchmark contract forbids cuBLAS, CUTLASS, Triton, ATen compute helpers, and extra CUDA streams. Stay within raw custom CUDA/C++ extension code with minimal glue.",
        "Hosted WebSearch/WebFetch are restricted to docs.nvidia.com only.",
        "Never overlap harness tool calls. Start a new one only after the previous one has returned.",
        "If a strategy fails, re-read the docs, profile when useful, consult allowed NVIDIA docs when needed, and start the next strategy yourself.",
        "If `run_candidate` says a run does not count because of validation or suspected cheating, discard that attempt, fix the exact issue it names, and keep going.",
        "`run_candidate` and `profile_ncu` may take a while; trust the tool output and wait for them to finish.",
        "Do not stop early. When you are truly finished, terminate only through `complete_problem(summary=...)`.",
        "The harness will infer the measured outcome from the recorded runs.",
    ]
    return "\n".join(lines) + "\n"


def render_codex_helper_instructions(*, spec: Any) -> str:
    tool_list = ", ".join(f"`{name}`" for name in spec.mcp_tools)
    read_list = ", ".join(f"`{path}`" for path in spec.read_paths)
    return (
        f"You are a narrow delegated helper for one assigned optimization problem.\n\n"
        "The main solver should treat you as an execution-focused delegate, not as another planner.\n"
        f"Use only the `{COMMAND_MCP_SERVER_NAME}` command MCP tools: {tool_list}.\n"
        f"Read local problem files directly, and only for {read_list}.\n"
        "Do not inspect unrelated files, local config, or hidden harness state.\n"
        "Do not use ad hoc shell commands, Python snippets, or local file tools.\n"
        "Hosted WebSearch/WebFetch, if available at all, are restricted to docs.nvidia.com only.\n"
        "Benchmark constraints are strict: do not propose or use cuBLAS, CUTLASS, Triton, ATen compute helpers, torch.matmul-style shortcuts, or extra CUDA streams.\n"
        "If one of the allowed command tools is slow, wait for it to finish instead of trying to inspect processes or the GPU.\n"
        "Never start a second harness command while another one is still running.\n"
        "If a measured run is flagged as suspicious, cheating, or non-counting, say so plainly and tell the main solver to discard it.\n"
        "Do not edit any files unless the main agent explicitly delegated candidate writing to you.\n"
        "Work independently: do not ask the user or the main agent for permission to proceed once assigned.\n"
        "When finished, return only a concise actionable summary; do not ask follow-up questions.\n"
        f"{spec.summary_focus}\n"
    )


def render_claude_helper_body(*, spec: Any) -> str:
    tool_list = ", ".join(f"`{name}`" for name in spec.mcp_tools)
    read_list = ", ".join(f"`{path}`" for path in spec.read_paths)
    return (
        "You are a narrow delegated helper for one assigned optimization problem.\n\n"
        "The main solver should treat you as an execution-focused delegate, not as another planner.\n"
        f"Use only the `{COMMAND_MCP_SERVER_NAME}` command MCP tools: {tool_list}.\n"
        f"Read local problem files directly, and only for {read_list}.\n"
        "Do not inspect unrelated files, local config, or hidden harness state.\n"
        "Do not use shell commands or Python snippets to inspect profiler outputs or parse files.\n"
        "Hosted WebSearch/WebFetch, if available at all, are restricted to docs.nvidia.com only.\n"
        "Benchmark constraints are strict: do not propose or use cuBLAS, CUTLASS, Triton, ATen compute helpers, torch.matmul-style shortcuts, or extra CUDA streams.\n"
        "If one of the allowed command tools is slow, wait for it to finish instead of trying to inspect processes or the GPU.\n"
        "Never start a second harness command while another one is still running.\n"
        "If a measured run is flagged as suspicious, cheating, or non-counting, say so plainly and tell the main solver to discard it.\n"
        "Do not edit any files unless the main agent explicitly delegated candidate writing to you.\n"
        "Work independently: do not ask the user or the main agent for permission to proceed once assigned.\n"
        "When finished, return only a concise actionable summary; do not ask follow-up questions.\n"
        f"{spec.summary_focus}\n"
    )


def render_goal_status_markdown(snapshot: dict[str, Any]) -> str:
    best_runtime = snapshot.get("best_correct_runtime_ms")
    eager_baseline = snapshot.get("eager_baseline_ms")
    compile_baseline = snapshot.get("compile_baseline_ms")
    problem_name = snapshot.get("problem_name") or "unknown"
    wall_clock_elapsed_minutes = as_float(snapshot.get("wall_clock_elapsed_minutes"))
    elapsed_minutes = as_float(snapshot.get("elapsed_minutes"))
    recorded_gpu_wait_minutes = as_float(snapshot.get("recorded_gpu_wait_minutes"))
    live_gpu_wait_minutes = as_float(snapshot.get("live_gpu_wait_minutes"))
    gpu_wait_minutes_total = as_float(snapshot.get("gpu_wait_minutes_total"))
    remaining_minutes = as_float(snapshot.get("remaining_minutes"))
    time_budget_minutes = as_float(snapshot.get("time_budget_minutes"))
    substantial_budget_remains = (
        remaining_minutes is not None
        and time_budget_minutes is not None
        and remaining_minutes > max(60.0, time_budget_minutes * 0.25)
    )
    unresolved = not snapshot["beats_both"]
    latest_attempt_blocked_reason = str(snapshot.get("latest_attempt_blocked_reason") or "").strip()
    if unresolved:
        heading = "# Goal Status: UNRESOLVED — keep working"
        if latest_attempt_blocked_reason:
            heading = "# Goal Status: UNRESOLVED — latest attempt was discarded; keep working"
        standing_orders = [
            "- You MUST NOT stop, summarize, or hand back control. Keep working.",
            "- Do NOT ask the user for confirmation, approval, or whether to continue. Choose the next action yourself.",
            "- Re-read `SPEC.md` and `HARDWARE.md` before every major strategy change.",
            "- Timing and profiling are normal tools, not expensive last resorts. Use them even for small constant or layout changes.",
            "- Never overlap command tool calls. Start a new harness tool call only after the previous one has fully returned.",
            "- Harness command tools are authoritative. If one is slow, wait for it. Do NOT monitor it with `ps`, `pgrep`, `top`, `htop`, `nvidia-smi`, `strace`, `/proc`, or build-tree inspection.",
            "- If the latest run was discarded as suspicious, cheating, or invalid, it does not count. Fix the exact reported issue and keep working.",
            "- If stuck: call `profile_ncu`, read `HARDWARE.md`, search NVIDIA docs on docs.nvidia.com only, make a new plan, and try a new branch without asking for approval.",
            "- The benchmark contract forbids cuBLAS, CUTLASS, Triton, ATen compute helpers, and extra CUDA streams.",
            "- The budget clock is wall time since workspace creation minus recorded GPU wait time and any live GPU lease wait currently in progress. End through `complete_problem` before remaining time reaches zero.",
            "- A plain assistant message is NEVER a valid way to end this run. The ONLY exit is `complete_problem(summary=...)`.",
            "- `run_candidate` and `profile_ncu` may take a while; wait for the tool result instead of treating them as hung.",
        ]
    else:
        heading = "# Goal Status: RESOLVED — both baselines beaten; complete with success"
        standing_orders = [
            "- Re-check `SPEC.md` once, then end through `complete_problem(summary='both baselines beaten')`.",
        ]

    if remaining_minutes is None:
        remaining_line = "unknown"
    elif substantial_budget_remains:
        remaining_line = (
            f"{remaining_minutes} (most of your budget remains — stopping now wastes it)"
        )
    else:
        remaining_line = str(remaining_minutes)

    if best_runtime is None:
        best_runtime_line = "none yet"
    else:
        best_runtime_line = (
            f"{best_runtime} ms (must be below {eager_baseline} ms and {compile_baseline} ms)"
        )

    profiler_line = str(snapshot["num_profile_runs"])
    attempt_breakdown = (
        f"{snapshot['num_correct_attempts']} correct, "
        f"{snapshot['num_incorrect_attempts']} incorrect, "
        f"{snapshot['num_execution_failed_attempts']} execution-failed"
    )
    if snapshot.get("num_other_attempts"):
        attempt_breakdown += f", {snapshot['num_other_attempts']} other"
    lines = [
        heading,
        "",
        "Standing orders (active until both baselines are beaten):",
        "",
        *standing_orders,
        "",
        "## Current State",
        "",
        f"- problem: level {snapshot['level']} problem {snapshot['problem_id']} ({problem_name})",
        f"- best correct runtime: {best_runtime_line}",
        f"- beats eager ({eager_baseline} ms): {snapshot['beats_eager']}",
        f"- beats compile ({compile_baseline} ms): {snapshot['beats_compile']}",
        f"- beats both: {snapshot['beats_both']}",
        f"- latest attempt sample: {snapshot.get('latest_attempt_sample_id')}",
        f"- latest attempt counts toward progress: {snapshot.get('latest_attempt_counts_toward_progress', True)}",
        f"- latest attempt discard reason: {snapshot.get('latest_attempt_blocked_reason') or 'none'}",
        f"- attempts counted toward progress: {snapshot['num_attempts']} ({attempt_breakdown})",
        f"- timing calls: {snapshot['num_timing_runs']}",
        f"- profiler calls: {profiler_line}",
        f"- best correct sample: {snapshot.get('best_correct_sample_id')}",
        f"- best result warnings: {snapshot.get('best_result_warnings') or []}",
        f"- wall-clock minutes since workspace creation: {wall_clock_elapsed_minutes}",
        f"- completed gpu wait minutes excluded from budget: {recorded_gpu_wait_minutes}",
        f"- currently active gpu queue-wait minutes excluded from budget: {live_gpu_wait_minutes}",
        f"- total gpu wait minutes excluded from budget: {gpu_wait_minutes_total}",
        f"- elapsed minutes counted against budget: {elapsed_minutes}",
        f"- remaining minutes: {remaining_line}",
        "- static docs: `AGENTS.md`, `SPEC.md`, `HARDWARE.md`",
        "- live docs: `GOAL_STATUS.md`, `goal_status.json`",
        "- local sample mirrors: `samples/`, `samples/best_sample.py`, `samples/best_result.json`",
        "- latest profiler mirrors: `profiles/latest.summary.txt` and `profiles/latest.details.txt`",
        "",
        "Source of truth: measured run history plus the live solver trace. Refresh via the `goal_status` or `run_candidate` command tools.",
    ]
    return "\n".join(lines) + "\n"
