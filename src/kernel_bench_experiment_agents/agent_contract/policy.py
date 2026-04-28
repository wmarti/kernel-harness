"""Canonical typed policy for solver-visible harness behavior and runtime config.

This module is the single source of truth for the policy objects that must stay aligned across
runtime config rendering, workspace docs, trace audit, and helper-agent rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kernel_bench_experiment_agents.kernelbench.candidate.contract import CANDIDATE_FILENAME

MCP_SERVER_NAME = "kernelbench"
COMMAND_MCP_SERVER_NAME = "kernelbench_commands"


@dataclass(frozen=True)
class CommandToolSpec:
    """Describes one brokered privileged command available to the solver."""

    name: str
    cli_name: str
    wrapper_name: str
    purpose: str
    uses_gpu: bool = False
    read_only: bool = False
    destructive: bool = False

    @property
    def wrapper_path(self) -> str:
        return f"./bin/{self.wrapper_name}"


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

COMMAND_TOOL_SPECS: tuple[CommandToolSpec, ...] = (
    CommandToolSpec(
        name="run_candidate",
        cli_name="run-candidate",
        wrapper_name="run_candidate.sh",
        purpose="evaluate correctness and runtime for the current candidate",
        uses_gpu=True,
        destructive=True,
    ),
    CommandToolSpec(
        name="profile_ncu",
        cli_name="profile-ncu",
        wrapper_name="profile_ncu.sh",
        purpose="profile the current candidate with Nsight Compute",
        uses_gpu=True,
        destructive=True,
    ),
    CommandToolSpec(
        name="goal_status",
        cli_name="goal-status",
        wrapper_name="goal_status.sh",
        purpose="refresh and print live goal status",
        read_only=True,
    ),
    CommandToolSpec(
        name="best_result",
        cli_name="best-result",
        wrapper_name="best_result.sh",
        purpose="print the best measured correct result so far",
        read_only=True,
    ),
    CommandToolSpec(
        name="complete_problem",
        cli_name="complete-problem",
        wrapper_name="complete_problem.sh",
        purpose="record the final solver summary and end the run",
        destructive=True,
    ),
)

MCP_TOOL_SPECS: tuple[McpToolSpec, ...] = (
    McpToolSpec(
        name="workspace_overview",
        purpose="workspace_overview() -> JSON overview of the assigned problem, the fixed read-only resources, the allowed history directories, and the available MCP action tools",
        read_only=True,
    ),
    McpToolSpec(
        name="list_workspace_dir",
        purpose="list_workspace_dir(path='samples'|'profiles') -> JSON directory listing for one allowed history directory only",
        read_only=True,
    ),
    McpToolSpec(
        name="read_workspace_file",
        purpose="read_workspace_file(path) -> file text for one fixed resource or one listed text artifact under `samples/` or `profiles/`",
        read_only=True,
    ),
    McpToolSpec(
        name="write_candidate",
        purpose=f"write_candidate(content) -> validate and overwrite {CANDIDATE_FILENAME}; the only writable workspace file",
        destructive=True,
    ),
    *(
        McpToolSpec(
            name=spec.name,
            purpose=spec.purpose,
            uses_gpu=spec.uses_gpu,
            read_only=spec.read_only,
            destructive=spec.destructive,
        )
        for spec in COMMAND_TOOL_SPECS
    ),
)

WORKSPACE_STANDING_ORDERS: tuple[str, ...] = (
    "Act as the planner-manager for this problem. Keep the main context focused on strategy, debugging, and choosing the next branch.",
    "Work independently. There is no human approval, acceptance, or confirmation step during the run.",
    "Do not ask whether to proceed. Pick the next reasonable action yourself.",
    "Do not end with a plain assistant message. The only valid exit is the `complete_problem` MCP tool.",
    "Never start a second harness MCP call while another one is still running.",
    "WHEN you want a measured evaluation, spawn the `runner` helper if available; use direct `run_candidate` yourself only when helper spawning is unavailable.",
    "WHEN you want Nsight Compute output or profile interpretation, spawn the `profiler` helper if available; use direct `profile_ncu` yourself only when helper spawning is unavailable.",
    "After every measured run or profile, re-read GOAL_STATUS.md or call `goal_status`; keep iterating if it still says UNRESOLVED.",
    "Stay inside the benchmark contract: no cuBLAS, CUTLASS, Triton, ATen compute helpers, or extra CUDA streams.",
    "If one branch fails, start another one. Failed attempts are normal, not a stop signal.",
    "`run_candidate` and `profile_ncu` may take a while. Wait for them to finish instead of assuming they hung.",
)

WORKSPACE_STUCK_PROTOCOL: tuple[str, ...] = (
    "Re-read SPEC.md, HARDWARE.md, and GOAL_STATUS.md.",
    "WHEN you do not already have profiling for the current idea, call `profile_ncu`.",
    "Read `profiles/latest.summary.txt` first, then `profiles/latest.details.txt` if needed.",
    "WHEN the next idea depends on hardware-specific behavior, use hosted web search on docs.nvidia.com only for topics like tensor cores, WMMA, async copy/pipelining, occupancy, bank conflicts, and memory hierarchy limits. Other domains are blocked by policy.",
    "WHEN choosing the next branch, inspect `samples/` and `profiles/` so you do not retry the same failed idea.",
    "Do not switch to library wrappers or extra CUDA streams; they are forbidden by the benchmark contract.",
    "Make a new implementation plan and continue without asking the user for permission.",
)

FIXED_WORKSPACE_RESOURCE_PATHS: tuple[str, ...] = (
    "AGENTS.md",
    "INITIAL_PROMPT.md",
    "SPEC.md",
    "HARDWARE.md",
    "GOAL_STATUS.md",
    "problem_reference.py",
    CANDIDATE_FILENAME,
)

WORKSPACE_BROWSE_DIRS: tuple[str, ...] = (
    "samples/",
    "profiles/",
)

WORKSPACE_READ_PATHS: tuple[str, ...] = (
    *FIXED_WORKSPACE_RESOURCE_PATHS,
    *WORKSPACE_BROWSE_DIRS,
)

WORKSPACE_EDIT_PATHS: tuple[str, ...] = (CANDIDATE_FILENAME,)

HELPER_SPECS: tuple[HelperAgentSpec, ...] = (
    HelperAgentSpec(
        name="runner",
        description=(
            "Execution-focused helper for one assigned optimization problem. "
            "The main solver should delegate measured evaluations to this helper by default so the main context stays focused on planning."
        ),
        mcp_tools=("read_workspace_file", "run_candidate", "goal_status", "best_result"),
        read_paths=(
            "AGENTS.md",
            "SPEC.md",
            "HARDWARE.md",
            "GOAL_STATUS.md",
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
            "The main solver should delegate Nsight Compute work to this helper by default so the main context stays focused on planning."
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
    spec.wrapper_path: f"{spec.name}_calls" for spec in COMMAND_TOOL_SPECS
}
GPU_WRAPPER_PATHS: tuple[str, ...] = tuple(
    spec.wrapper_path for spec in COMMAND_TOOL_SPECS if spec.uses_gpu
)


def mcp_tool_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in MCP_TOOL_SPECS)


def claude_mcp_tool_names() -> tuple[str, ...]:
    return tuple(f"mcp__{MCP_SERVER_NAME}__{name}" for name in mcp_tool_names())


def command_tool_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in COMMAND_TOOL_SPECS)


def claude_command_mcp_tool_names() -> tuple[str, ...]:
    return tuple(f"mcp__{COMMAND_MCP_SERVER_NAME}__{name}" for name in command_tool_names())


def command_tool_spec(name: str) -> CommandToolSpec:
    for spec in COMMAND_TOOL_SPECS:
        if spec.name == name:
            return spec
    raise KeyError(name)


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
