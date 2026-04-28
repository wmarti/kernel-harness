"""Define the durable archive file layout and helpers for reading archived problem manifests.

Other modules use these paths when they need stable, cross-run locations instead of live workspace state.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from kernel_bench_experiment_agents.runtime.project import (
    archive_agent_dir,
    archive_attempts_dir,
    archive_contract_dir,
    archive_problem_dir,
    archive_profiles_dir,
    write_json,
)


def archive_problem_contract_dir(run_name: str, level: int, problem_id: int) -> Path:
    return archive_contract_dir(run_name, level, problem_id)


def archive_problem_attempts_dir(run_name: str, level: int, problem_id: int) -> Path:
    return archive_attempts_dir(run_name, level, problem_id)


def archive_problem_profiles_dir(run_name: str, level: int, problem_id: int) -> Path:
    return archive_profiles_dir(run_name, level, problem_id)


def sample_manifest_path(run_name: str, level: int, problem_id: int, sample_id: int) -> Path:
    return archive_problem_attempts_dir(run_name, level, problem_id) / f"sample_{sample_id}.json"


def goal_status_archive_path(run_name: str, level: int, problem_id: int) -> Path:
    return archive_agent_dir(run_name, level, problem_id) / "goal_status.json"


def trace_events_path(run_name: str, level: int, problem_id: int) -> Path:
    return archive_agent_dir(run_name, level, problem_id) / "events.jsonl"


def mcp_trace_events_path(run_name: str, level: int, problem_id: int) -> Path:
    return archive_agent_dir(run_name, level, problem_id) / "mcp_ir_events.jsonl"


def archive_manifest_path(run_name: str, level: int, problem_id: int) -> Path:
    return archive_problem_dir(run_name, level, problem_id) / "archive_manifest.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _sample_id_from_path(path: Path) -> int | None:
    match = re.fullmatch(r"sample_(\d+)\.json", path.name)
    return int(match.group(1)) if match else None


def _profile_index_from_path(path: Path) -> int | None:
    match = re.fullmatch(r"profile_(\d+)\.json", path.name)
    return int(match.group(1)) if match else None


def sample_manifest_entries(run_name: str, level: int, problem_id: int) -> list[dict[str, Any]]:
    attempts_dir = archive_problem_attempts_dir(run_name, level, problem_id)
    entries: list[tuple[int, dict[str, Any]]] = []
    for child in attempts_dir.glob("sample_*.json"):
        sample_id = _sample_id_from_path(child)
        payload = _read_json(child)
        if sample_id is None or payload is None:
            continue
        entries.append((sample_id, payload))
    return [payload for _, payload in sorted(entries, key=lambda item: item[0])]


def profile_manifest_entries(run_name: str, level: int, problem_id: int) -> list[dict[str, Any]]:
    profiles_dir = archive_problem_profiles_dir(run_name, level, problem_id)
    entries: list[tuple[int, dict[str, Any]]] = []
    for child in profiles_dir.glob("profile_*.json"):
        index = _profile_index_from_path(child)
        payload = _read_json(child)
        if index is None or payload is None:
            continue
        entries.append((index, payload))
    return [payload for _, payload in sorted(entries, key=lambda item: item[0])]


def next_archive_profile_index(run_name: str, level: int, problem_id: int) -> int:
    profiles_dir = archive_problem_profiles_dir(run_name, level, problem_id)
    max_index = 0
    for child in profiles_dir.glob("profile_*.json"):
        index = _profile_index_from_path(child)
        if index is not None:
            max_index = max(max_index, index)
    return max_index + 1


def build_archive_problem_manifest(run_name: str, level: int, problem_id: int) -> dict[str, Any]:
    workspace_stub = f"state/workspaces/{run_name}/level_{level}/problem_{problem_id}"
    return {
        "schema_version": 2,
        "run_name": run_name,
        "level": level,
        "problem_id": problem_id,
        "copy_this_problem_directory": ".",
        "copy_this_run_directory": "../..",
        "canonical_subdirs": {
            "contract": {
                "path": "contract/",
                "purpose": "exact rendered solver contract and frozen problem inputs",
            },
            "agent": {
                "path": "agent/",
                "purpose": "raw agent events, normalized trace IR, completion, goal-status snapshot, and final message",
            },
            "attempts": {
                "path": "attempts/",
                "purpose": "measured attempt manifests, candidate kernel snapshots, and per-attempt stdout/stderr",
            },
            "profiles": {
                "path": "profiles/",
                "purpose": "Nsight Compute manifests plus summary/details/stdout/stderr text artifacts",
            },
        },
        "canonical_files": [
            {"path": "archive_manifest.json", "purpose": "map of what in this problem archive is canonical"},
            {"path": "contract/problem.json", "purpose": "problem metadata, baseline runtimes, and budget start time"},
            {"path": "contract/hardware.json", "purpose": "frozen hardware facts"},
            {"path": "contract/provenance.json", "purpose": "archive-only provenance for the original KernelBench checkout and baseline inputs"},
            {"path": "contract/workspace_contract.json", "purpose": "machine-readable solver contract"},
            {"path": "contract/candidate_model_new.py", "purpose": "initial free-form candidate stub shown to the solver"},
            {"path": "contract/candidate_final.py", "purpose": "final workspace candidate captured at completion when available"},
            {"path": "contract/AGENTS.md", "purpose": "rendered solver instructions"},
            {"path": "contract/SPEC.md", "purpose": "rendered problem spec"},
            {"path": "contract/HARDWARE.md", "purpose": "rendered hardware guidance"},
            {"path": "contract/INITIAL_PROMPT.md", "purpose": "exact initial solver prompt"},
            {"path": "contract/helper_agents/", "purpose": "rendered helper-agent specs for the chosen tool runtime"},
            {"path": "agent/events.jsonl", "purpose": "raw streamed agent trace"},
            {"path": "agent/trace_ir.json", "purpose": "normalized trace IR"},
            {"path": "agent/mcp_ir_events.jsonl", "purpose": "synthetic MCP tool events merged into trace counts and audit"},
            {"path": "agent/completion.json", "purpose": "final terminal state plus measured outcome"},
            {"path": "agent/final_message.txt", "purpose": "last model message when available"},
            {"path": "agent/goal_status.json", "purpose": "latest archived goal-status snapshot"},
            {"path": "attempts/sample_<id>.json", "purpose": "per-attempt measured result metadata"},
            {"path": "attempts/sample_<id>.stdout.txt", "purpose": "evaluation subprocess stdout for an attempt"},
            {"path": "attempts/sample_<id>.stderr.txt", "purpose": "evaluation subprocess stderr for an attempt"},
            {"path": "attempts/kernels/", "purpose": "snapshots of candidate code submitted for measurement"},
            {"path": "profiles/profile_<id>.json", "purpose": "per-profile metadata"},
            {"path": "profiles/profile_<id>.summary.txt", "purpose": "first-pass text summary for the solver"},
            {"path": "profiles/profile_<id>.details.txt", "purpose": "full text export from ncu --page details"},
            {"path": "profiles/profile_<id>.stdout.txt", "purpose": "profiling command stdout"},
            {"path": "profiles/profile_<id>.stderr.txt", "purpose": "profiling command stderr"},
        ],
        "workspace_mirrors_not_required_for_copy_out": [
            f"{workspace_stub}/samples/",
            f"{workspace_stub}/profiles/",
            f"{workspace_stub}/GOAL_STATUS.md",
            f"{workspace_stub}/goal_status.json",
            f"{workspace_stub}/completion.json",
        ],
    }


def write_archive_problem_manifest(run_name: str, level: int, problem_id: int) -> Path:
    path = archive_manifest_path(run_name, level, problem_id)
    write_json(path, build_archive_problem_manifest(run_name, level, problem_id))
    return path
