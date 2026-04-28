"""Build the solver-visible workspace contract for one problem.

Workspace materialization uses these helpers to turn typed metadata and shared policy into the files the agent actually reads.
"""

from __future__ import annotations

from typing import Any

from kernel_bench_experiment_agents.agent_contract.policy import (
    ALLOWED_WEB_DOMAINS,
    COMMAND_TOOL_SPECS,
    HELPER_SPECS,
    LAUNCHER_TERMINAL_STATES,
    SOLVER_TERMINAL_STATES,
    WORKSPACE_EDIT_PATHS,
    WORKSPACE_READ_PATHS,
    WORKSPACE_STANDING_ORDERS,
    WORKSPACE_STUCK_PROTOCOL,
)
from kernel_bench_experiment_agents.agent_contract.prompts import (
    render_initial_prompt,
    render_workspace_agents_md,
    render_workspace_spec_md,
)


def build_workspace_contract(*, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "assignment": {
            "run_name": metadata["run_name"],
            "level": metadata["level"],
            "problem_id": metadata["problem_id"],
            "dataset_src": metadata["dataset_src"],
            "problem_name": metadata.get("problem_name"),
            "gpu_name": metadata.get("gpu_name"),
            "num_gpus": metadata.get("num_gpus"),
            "time_budget_minutes": metadata.get("time_budget_minutes"),
            "model": metadata.get("model"),
            "precision": metadata.get("precision", "bf16"),
        },
        "reads": list(WORKSPACE_READ_PATHS),
        "edits": list(WORKSPACE_EDIT_PATHS),
        "command_tools": [
            {
                "name": spec.name,
                "gpu": spec.uses_gpu,
                "purpose": spec.purpose,
                "read_only": spec.read_only,
                "destructive": spec.destructive,
            }
            for spec in COMMAND_TOOL_SPECS
        ],
        "helper_agents": [spec.name for spec in HELPER_SPECS],
        "behavior": {
            "independent_execution": True,
            "no_user_confirmation": True,
            "no_plain_message_exit": True,
            "standing_orders": list(WORKSPACE_STANDING_ORDERS),
            "stuck_protocol": list(WORKSPACE_STUCK_PROTOCOL),
        },
        "web": {
            "allowed_domains": list(ALLOWED_WEB_DOMAINS),
            "shell_network_forbidden": True,
        },
        "solver_terminal_states": list(SOLVER_TERMINAL_STATES),
        "launcher_terminal_states": list(LAUNCHER_TERMINAL_STATES),
    }
