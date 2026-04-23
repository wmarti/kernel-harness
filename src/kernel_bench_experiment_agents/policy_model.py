"""Canonical typed policy for solver-visible harness behavior and runtime config.

This module is the single source of truth for the policy objects that must stay aligned across
runtime config rendering, workspace docs, trace audit, and helper-agent rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .candidate_contract import CANDIDATE_FILENAME

MCP_SERVER_NAME = "kernelbench"


@dataclass(frozen=True)
class WrapperCommandSpec:
    """Describes one backend wrapper command that the harness still records in traces."""

    name: str
    path: str
    purpose: str
    uses_gpu: bool = False


@dataclass(frozen=True)
class McpToolSpec:
    """Describes one solver-visible MCP tool."""

    name: str
    purpose: str
    uses_gpu: bool = False
    read_only: bool = False
    destructive: bool = False


@dataclass(frozen=True)
class HelperAgentSpec:
    """Captures the shared intent for rendered Codex and Claude helper agents."""

    name: str
    description: str
    mcp_tools: tuple[str, ...]
    read_paths: tuple[str, ...]
    summary_focus: str


ALLOWED_WEB_DOMAINS: tuple[str, ...] = ("docs.nvidia.com",)
SOLVER_TERMINAL_STATES: tuple[str, ...] = ("done",)
LAUNCHER_TERMINAL_STATES: tuple[str, ...] = (
    "budget_exhausted",
    "failed_to_generate",
)

# These wrapper paths remain the backend command surface used by the harness itself. Synthetic MCP
# trace events refer to them so the existing counting/audit logic can stay stable.
WORKSPACE_COMMAND_SPECS: tuple[WrapperCommandSpec, ...] = (
    WrapperCommandSpec(
        name="hardware_info",
        path="./bin/hardware_info.sh",
        purpose="print frozen hardware facts for this workspace",
    ),
    WrapperCommandSpec(
        name="run_candidate",
        path="./bin/run_candidate.sh",
        purpose="evaluate correctness and runtime for the current candidate",
        uses_gpu=True,
    ),
    WrapperCommandSpec(
        name="profile_ncu",
        path="./bin/profile_ncu.sh",
        purpose="profile the current candidate with Nsight Compute",
        uses_gpu=True,
    ),
    WrapperCommandSpec(
        name="goal_status",
        path="./bin/goal_status.sh",
        purpose="refresh and print live goal status",
    ),
    WrapperCommandSpec(
        name="best_result",
        path="./bin/best_result.sh",
        purpose="print the best measured correct result so far",
    ),
    WrapperCommandSpec(
        name="complete_problem",
        path="./bin/complete_problem.sh",
        purpose="record a terminal completion summary",
    ),
)

MCP_TOOL_SPECS: tuple[McpToolSpec, ...] = (
    McpToolSpec(
        name="workspace_overview",
        purpose="return the assigned problem metadata, key files, and available harness tools",
        read_only=True,
    ),
    McpToolSpec(
        name="list_workspace_dir",
        purpose="list a safe workspace directory such as '.', 'samples', or 'profiles'",
        read_only=True,
    ),
    McpToolSpec(
        name="read_workspace_file",
        purpose="read one allowed workspace file as text",
        read_only=True,
    ),
    McpToolSpec(
        name="write_candidate",
        purpose=f"overwrite {CANDIDATE_FILENAME} with new source text",
    ),
    McpToolSpec(
        name="run_candidate",
        purpose="evaluate correctness and runtime for the current candidate",
        uses_gpu=True,
    ),
    McpToolSpec(
        name="profile_ncu",
        purpose="profile the current candidate with Nsight Compute",
        uses_gpu=True,
    ),
    McpToolSpec(
        name="goal_status",
        purpose="refresh and return the live goal-status snapshot",
        read_only=True,
    ),
    McpToolSpec(
        name="best_result",
        purpose="return the best measured correct result so far",
        read_only=True,
    ),
    McpToolSpec(
        name="complete_problem",
        purpose="record a terminal completion summary",
    ),
)

WORKSPACE_STANDING_ORDERS: tuple[str, ...] = (
    "Work independently. There is no human approval, acceptance, or confirmation step during the run.",
    "Do not ask whether to proceed. Pick the next reasonable action yourself.",
    "Do not end with a plain assistant message. The only valid exit is the `complete_problem` MCP tool.",
    "Never start a second harness MCP call while another one is still running.",
    "After every measured run or profile, re-read GOAL_STATUS.md or call `goal_status`; keep iterating if it still says UNRESOLVED.",
    "If one branch fails, start another one. Failed attempts are normal, not a stop signal.",
    "`run_candidate` and `profile_ncu` may take a while. Wait for them to finish instead of assuming they hung.",
)

WORKSPACE_STUCK_PROTOCOL: tuple[str, ...] = (
    "Re-read SPEC.md, HARDWARE.md, and GOAL_STATUS.md.",
    "Call `profile_ncu` if you do not already have profiling for the current idea.",
    "Read profiles/latest.summary.txt first, then profiles/latest.details.txt if needed.",
    "Use hosted web search only for docs.nvidia.com when you need CUDA or hardware guidance.",
    "Make a new implementation plan and continue without asking the user for permission.",
)

WORKSPACE_READ_PATHS: tuple[str, ...] = (
    ".",
    "AGENTS.md",
    "SPEC.md",
    "HARDWARE.md",
    "GOAL_STATUS.md",
    "goal_status.json",
    "hardware.json",
    "workspace_contract.json",
    "problem.json",
    "problem_reference.py",
    CANDIDATE_FILENAME,
    "samples/",
    "profiles/",
)

WORKSPACE_EDIT_PATHS: tuple[str, ...] = (CANDIDATE_FILENAME,)

HELPER_SPECS: tuple[HelperAgentSpec, ...] = (
    HelperAgentSpec(
        name="runner",
        description=(
            "Execution-focused helper for one assigned optimization problem. "
            "Use proactively to run measured evaluations and summarize results without polluting the main context."
        ),
        mcp_tools=("read_workspace_file", "run_candidate", "goal_status", "best_result"),
        read_paths=(
            "AGENTS.md",
            "SPEC.md",
            "HARDWARE.md",
            "GOAL_STATUS.md",
            "goal_status.json",
            "problem_reference.py",
            CANDIDATE_FILENAME,
            "samples/",
            "samples/best_result.json",
        ),
        summary_focus=(
            "Return a compact summary covering correctness failures, compiler failures, runtime measurements, the current best sample, and the most likely next implementation branch."
        ),
    ),
    HelperAgentSpec(
        name="profiler",
        description=(
            "Profiling helper for one assigned optimization problem. "
            "Use proactively to run Nsight Compute profiling and summarize bottlenecks and likely next steps."
        ),
        mcp_tools=("read_workspace_file", "profile_ncu", "goal_status"),
        read_paths=(
            "AGENTS.md",
            "SPEC.md",
            "HARDWARE.md",
            "GOAL_STATUS.md",
            "problem_reference.py",
            CANDIDATE_FILENAME,
            "profiles/latest.summary.txt",
            "profiles/latest.details.txt",
        ),
        summary_focus=(
            "Return short, actionable summaries focused on bottlenecks, dominant kernels, occupancy, memory behavior, and the most promising next optimization directions."
        ),
    ),
)


WORKSPACE_WRAPPER_TRACE_KEYS: dict[str, str] = {
    spec.path: f"{spec.name}_calls" for spec in WORKSPACE_COMMAND_SPECS
}
GPU_WRAPPER_PATHS: tuple[str, ...] = tuple(
    spec.path for spec in WORKSPACE_COMMAND_SPECS if spec.uses_gpu
)


def mcp_tool_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in MCP_TOOL_SPECS)


def claude_mcp_tool_names() -> tuple[str, ...]:
    return tuple(f"mcp__{MCP_SERVER_NAME}__{name}" for name in mcp_tool_names())


def _resolve_workspace_surface(
    workspace: Path,
    relative_paths: tuple[str, ...],
) -> tuple[set[Path], tuple[Path, ...]]:
    workspace = workspace.resolve()
    exact_paths: set[Path] = set()
    rooted_paths: list[Path] = []
    for relative_path in relative_paths:
        resolved = (workspace / relative_path).resolve()
        if relative_path.endswith("/"):
            rooted_paths.append(resolved)
        else:
            exact_paths.add(resolved)
    return exact_paths, tuple(rooted_paths)


def workspace_read_surface(workspace: Path) -> tuple[set[Path], tuple[Path, ...]]:
    return _resolve_workspace_surface(workspace, WORKSPACE_READ_PATHS)


def workspace_edit_surface(workspace: Path) -> set[Path]:
    exact_paths, rooted_paths = _resolve_workspace_surface(workspace, WORKSPACE_EDIT_PATHS)
    if rooted_paths:
        raise RuntimeError("workspace edit surface must contain only exact file paths")
    return exact_paths
