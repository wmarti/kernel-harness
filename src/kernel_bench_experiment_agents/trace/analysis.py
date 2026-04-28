"""Analyze normalized solver traces into counts, audits, and cost summaries.

Goal status, completion audit, and run reporting all depend on this module once raw tool traces have been normalized.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

from kernel_bench_experiment_agents.runtime.common import as_float, normalize_tool_name
from kernel_bench_experiment_agents.agent_contract.policy import (
    ALLOWED_WEB_DOMAINS,
    COMMAND_TOOL_SPECS,
    GPU_WRAPPER_PATHS,
    WORKSPACE_WRAPPER_TRACE_KEYS,
    workspace_edit_surface,
    workspace_read_surface,
)

_ALLOWED_WEB_SEARCH_HOSTS = ALLOWED_WEB_DOMAINS
_WORKSPACE_WRAPPER_NAMES = WORKSPACE_WRAPPER_TRACE_KEYS
_GPU_WRAPPER_PREFIXES = GPU_WRAPPER_PATHS
_FORBIDDEN_INSPECTION_MARKERS = (
    ".ptx",
    ".cubin",
    "cuobjdump",
    "nvdisasm",
    "torchinductor",
    "torch_compile_debug",
    "triton",
    "inductor",
)
_FORBIDDEN_DISCUSSION_MARKERS = (
    ".ptx",
    ".cubin",
    "cuobjdump",
    "nvdisasm",
    "torchinductor",
    "torch_compile_debug",
)
_FORBIDDEN_MONITORING_PREFIXES = (
    "ps",
    "pgrep",
    "top",
    "htop",
    "nvidia-smi",
    "strace",
)
_FORBIDDEN_MONITORING_MARKERS = (
    "/proc",
)
_ALLOWED_CLAUDE_BASH_COMMANDS = {spec.wrapper_path: spec for spec in COMMAND_TOOL_SPECS}


def _is_allowed_claude_shell_command(command: str) -> bool:
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not argv:
        return False

    spec = _ALLOWED_CLAUDE_BASH_COMMANDS.get(argv[0])
    if spec is None:
        return False
    if spec.name != "complete_problem":
        return len(argv) == 1

    args = argv[1:]
    if len(args) == 1:
        return args[0].startswith("--summary=")
    if len(args) == 2:
        return args[0] == "--summary"
    return False


# Usage accounting is tool-specific even though the returned shape is shared.
#
# Codex `--json` usage comes from `turn.completed` events. Claude stream-json may
# report cumulative usage in final `result` events, or per-message usage in
# assistant messages. Keep the two extraction paths separate instead of relying
# on a single mixed fallthrough parser.


def _empty_usage_summary() -> dict[str, Any]:
    return {
        "turns_completed": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": 0,
        "uncached_input_tokens": 0,
    }


def _claude_result_usage_summary(payload: dict[str, Any]) -> dict[str, int] | None:
    turns_completed = int(as_float(payload.get("num_turns")) or 0)

    model_usage = payload.get("modelUsage")
    if isinstance(model_usage, dict):
        model_usage_blocks = [
            value for value in model_usage.values() if isinstance(value, dict)
        ]
        if model_usage_blocks:
            direct_input_tokens = 0
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 0
            output_tokens = 0
            for block in model_usage_blocks:
                direct_input_tokens += int(as_float(block.get("inputTokens")) or 0)
                cache_creation_input_tokens += int(
                    as_float(block.get("cacheCreationInputTokens")) or 0
                )
                cache_read_input_tokens += int(
                    as_float(block.get("cacheReadInputTokens")) or 0
                )
                output_tokens += int(as_float(block.get("outputTokens")) or 0)

            uncached_input_tokens = direct_input_tokens + cache_creation_input_tokens
            return {
                "turns_completed": turns_completed,
                "input_tokens": cache_read_input_tokens + uncached_input_tokens,
                "cached_input_tokens": cache_read_input_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
                "output_tokens": output_tokens,
                "uncached_input_tokens": uncached_input_tokens,
            }

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None

    direct_input_tokens = int(as_float(usage.get("input_tokens")) or 0)
    cache_creation_input_tokens = int(
        as_float(usage.get("cache_creation_input_tokens")) or 0
    )
    cache_read_input_tokens = int(
        as_float(usage.get("cache_read_input_tokens")) or 0
    )
    output_tokens = int(as_float(usage.get("output_tokens")) or 0)
    uncached_input_tokens = direct_input_tokens + cache_creation_input_tokens
    return {
        "turns_completed": turns_completed,
        "input_tokens": cache_read_input_tokens + uncached_input_tokens,
        "cached_input_tokens": cache_read_input_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "output_tokens": output_tokens,
        "uncached_input_tokens": uncached_input_tokens,
    }


def _claude_trace_usage_summary(raw_events: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _empty_usage_summary()

    result_candidates: list[dict[str, int]] = []
    for payload in raw_events:
        if payload.get("type") != "result":
            continue
        usage_summary = _claude_result_usage_summary(payload)
        if usage_summary is not None:
            result_candidates.append(usage_summary)

    if result_candidates:
        max_turns_completed = max(
            candidate["turns_completed"] for candidate in result_candidates
        )
        summary = max(
            result_candidates,
            key=lambda candidate: (
                candidate["input_tokens"] + candidate["output_tokens"],
                candidate["turns_completed"],
            ),
        )
        summary["turns_completed"] = max_turns_completed or summary["turns_completed"] or 1
        return summary

    seen_assistant_message_ids: set[str] = set()
    for payload in raw_events:
        if payload.get("type") != "assistant":
            continue
        message = payload.get("message")
        if not isinstance(message, dict):
            continue
        message_id = message.get("id")
        if isinstance(message_id, str) and message_id:
            if message_id in seen_assistant_message_ids:
                continue
            seen_assistant_message_ids.add(message_id)
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        direct_input_tokens = int(as_float(usage.get("input_tokens")) or 0)
        cache_creation_input_tokens = int(
            as_float(usage.get("cache_creation_input_tokens")) or 0
        )
        cache_read_input_tokens = int(
            as_float(usage.get("cache_read_input_tokens")) or 0
        )
        summary["turns_completed"] += 1
        summary["cached_input_tokens"] += cache_read_input_tokens
        summary["cache_creation_input_tokens"] += cache_creation_input_tokens
        summary["uncached_input_tokens"] += (
            direct_input_tokens + cache_creation_input_tokens
        )
        summary["output_tokens"] += int(as_float(usage.get("output_tokens")) or 0)

    summary["input_tokens"] = (
        summary["cached_input_tokens"] + summary["uncached_input_tokens"]
    )
    return summary


def _codex_trace_usage_summary(raw_events: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _empty_usage_summary()
    for payload in raw_events:
        if payload.get("type") != "turn.completed":
            continue
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            continue
        summary["turns_completed"] += 1
        summary["input_tokens"] += int(as_float(usage.get("input_tokens")) or 0)
        summary["cached_input_tokens"] += int(
            as_float(usage.get("cached_input_tokens")) or 0
        )
        summary["output_tokens"] += int(as_float(usage.get("output_tokens")) or 0)

    summary["uncached_input_tokens"] = max(
        summary["input_tokens"] - summary["cached_input_tokens"],
        0,
    )
    return summary


def trace_usage_summary(
    raw_events: list[dict[str, Any]],
    *,
    tool: str = "codex",
) -> dict[str, Any]:
    normalized_tool = normalize_tool_name(tool)
    if normalized_tool == "claude":
        return _claude_trace_usage_summary(raw_events)
    if normalized_tool == "codex":
        return _codex_trace_usage_summary(raw_events)
    raise ValueError(f"Unsupported trace tool: {tool!r}")


def _claude_trace_cost_usd(raw_events: list[dict[str, Any]]) -> float | None:
    max_cost = None
    for payload in raw_events:
        if payload.get("type") != "result":
            continue

        explicit_cost = as_float(payload.get("total_cost_usd"))

        model_usage = payload.get("modelUsage")
        model_usage_cost = None
        if isinstance(model_usage, dict):
            usage_blocks = [
                value for value in model_usage.values() if isinstance(value, dict)
            ]
            if usage_blocks:
                model_usage_cost = sum(
                    float(as_float(block.get("costUSD")) or 0.0)
                    for block in usage_blocks
                )

        cost = explicit_cost
        if cost is None and model_usage_cost is not None:
            cost = model_usage_cost
        if cost is None:
            continue

        max_cost = cost if max_cost is None else max(max_cost, cost)

    return max_cost


def trace_cost_usd(
    raw_events: list[dict[str, Any]],
    *,
    tool: str = "codex",
) -> float | None:
    normalized_tool = normalize_tool_name(tool)
    if normalized_tool == "claude":
        return _claude_trace_cost_usd(raw_events)
    if normalized_tool == "codex":
        return None
    raise ValueError(f"Unsupported trace tool: {tool!r}")


def _empty_trace_counts() -> dict[str, Any]:
    return {
        "command_executions": 0,
        "file_change_events": 0,
        "wrapper_commands": 0,
        "gpu_wrapper_commands": 0,
        "hardware_info_calls": 0,
        "run_candidate_calls": 0,
        "profile_ncu_calls": 0,
        "goal_status_calls": 0,
        "best_result_calls": 0,
        "complete_problem_calls": 0,
        "spawn_agent_calls": 0,
        "wait_calls": 0,
        "web_search_calls": 0,
        "subagents_spawned": 0,
    }



def _extract_shell_snippet(command: str) -> str:
    prefix = "/bin/bash -lc "
    if not command.startswith(prefix):
        return command.strip()
    snippet = command[len(prefix) :].strip()
    if len(snippet) >= 2 and snippet[0] in {"'", '"'} and snippet[-1] == snippet[0]:
        snippet = snippet[1:-1]
    return snippet.strip()



def _split_leading_cd(snippet: str) -> tuple[str, str] | None:
    match = re.match(
        r"^cd\s+(?P<path>'[^']*'|\"[^\"]*\"|[^;&]+?)\s*(?:&&|;)\s*(?P<rest>.+)$",
        snippet,
        flags=re.DOTALL,
    )
    if not match:
        return None
    raw_path = match.group("path").strip()
    if len(raw_path) >= 2 and raw_path[0] in {"'", '"'} and raw_path[-1] == raw_path[0]:
        raw_path = raw_path[1:-1]
    return raw_path, match.group("rest").strip()



def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False



def _normalize_workspace_snippet(
    snippet: str,
    workspace: Path,
) -> tuple[str | None, str | None]:
    stripped = snippet.strip()
    cd_parts = _split_leading_cd(stripped)
    if cd_parts is None:
        return stripped, None

    raw_target, rest = cd_parts
    target = Path(raw_target).expanduser()
    if not target.is_absolute():
        target = workspace / target
    try:
        resolved_target = target.resolve()
    except OSError:
        return None, "command execution left the problem workspace"
    if not _is_relative_to(resolved_target, workspace.resolve()):
        return None, "command execution left the problem workspace"
    return rest, None



def trace_counts(
    ir_events: list[dict[str, Any]],
    *,
    raw_events: list[dict[str, Any]] | None = None,
    tool: str | None = None,
) -> dict[str, Any]:
    counts = _empty_trace_counts()
    spawned_threads: set[str] = set()

    for event in ir_events:
        kind = str(event.get("kind") or "")
        if kind == "command_execution":
            counts["command_executions"] += 1
            command = event.get("command")
            if not isinstance(command, str):
                continue
            snippet = _extract_shell_snippet(command)
            cd_parts = _split_leading_cd(snippet)
            effective_snippet = (cd_parts[1] if cd_parts else snippet).strip()
            for prefix, key in _WORKSPACE_WRAPPER_NAMES.items():
                if effective_snippet.startswith(prefix):
                    counts[key] += 1
                    counts["wrapper_commands"] += 1
                    if prefix in _GPU_WRAPPER_PREFIXES:
                        counts["gpu_wrapper_commands"] += 1
                    break
            continue

        if kind == "file_change":
            counts["file_change_events"] += 1
            continue

        if kind == "web_search":
            counts["web_search_calls"] += 1
            continue

        if kind == "subagent_spawn":
            counts["spawn_agent_calls"] += 1
            receiver_ids = event.get("metadata", {}).get("receiver_thread_ids")
            if isinstance(receiver_ids, list):
                for receiver_id in receiver_ids:
                    if isinstance(receiver_id, str) and receiver_id:
                        spawned_threads.add(receiver_id)
            else:
                counts["subagents_spawned"] += 1
            continue

        if kind == "wait":
            counts["wait_calls"] += 1
            continue

    if spawned_threads:
        counts["subagents_spawned"] = len(spawned_threads)

    normalized_tool = normalize_tool_name(tool) if tool is not None else None
    if normalized_tool == "claude" and raw_events is not None:
        usage_web_search_calls = 0
        for payload in raw_events:
            if payload.get("type") != "result":
                continue
            usage = payload.get("usage")
            if not isinstance(usage, dict):
                continue
            server_tool_use = usage.get("server_tool_use")
            if not isinstance(server_tool_use, dict):
                continue
            usage_web_search_calls = max(
                usage_web_search_calls,
                int(as_float(server_tool_use.get("web_search_requests")) or 0),
            )
        counts["web_search_calls"] = max(
            counts["web_search_calls"],
            usage_web_search_calls,
        )
    return counts



def web_searches_from_ir(ir_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    searches: list[dict[str, Any]] = []
    for event in ir_events:
        if event.get("kind") != "web_search":
            continue
        metadata = event.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        searches.append(
            {
                "line": event.get("line"),
                "query": metadata.get("query"),
                "queries": metadata.get("queries") or [],
                "domains": metadata.get("domains") or [],
            }
        )
    return searches



def _is_allowed_workspace_read(
    path: Path,
    *,
    allowed_read_paths: set[Path],
    allowed_read_roots: tuple[Path, ...],
) -> bool:
    resolved = path.resolve()
    if resolved in allowed_read_paths:
        return True
    return any(_is_relative_to(resolved, root) for root in allowed_read_roots)



def audit_trace(
    *,
    ir_events: list[dict[str, Any]],
    workspace: Path,
    raw_events: list[dict[str, Any]] | None = None,
    tool: str | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    allowed_edit_paths = workspace_edit_surface(workspace)
    allowed_read_paths, allowed_read_roots = workspace_read_surface(workspace)
    violations: list[dict[str, Any]] = []
    counts = trace_counts(ir_events, raw_events=raw_events, tool=tool)
    web_searches = web_searches_from_ir(ir_events)

    for search in web_searches:
        domains = [
            str(domain).strip().lower()
            for domain in search.get("domains", [])
            if isinstance(domain, str) and domain.strip()
        ]
        disallowed_domains = [
            domain
            for domain in domains
            if not any(
                domain == allowed or domain.endswith(f".{allowed}")
                for allowed in _ALLOWED_WEB_SEARCH_HOSTS
            )
        ]
        if disallowed_domains:
            violations.append(
                {
                    "line": search.get("line"),
                    "kind": "web_search_outside_allowed_domains",
                    "domains": disallowed_domains,
                    "message": "web search touched domains outside the allowed NVIDIA docs scope",
                }
            )

    for event in ir_events:
        line_number = int(event.get("line") or 0)
        kind = str(event.get("kind") or "")
        tool_name = str(event.get("tool_name") or "").strip().lower()
        text = str(event.get("text") or "")
        path_value = event.get("path")

        if kind == "file_read":
            if isinstance(path_value, str) and path_value.strip():
                read_path = Path(path_value).expanduser()
                if not read_path.is_absolute():
                    read_path = workspace / read_path
                read_path = read_path.resolve()
                if not _is_allowed_workspace_read(
                    read_path,
                    allowed_read_paths=allowed_read_paths,
                    allowed_read_roots=allowed_read_roots,
                ):
                    violations.append(
                        {
                            "line": line_number,
                            "kind": "read_outside_allowed_workspace_surface",
                            "path": str(read_path),
                            "message": "file read left the allowed workspace surface",
                        }
                    )
            continue

        if kind == "file_change":
            if isinstance(path_value, str) and path_value.strip():
                write_path = Path(path_value).expanduser()
                if not write_path.is_absolute():
                    write_path = workspace / write_path
                write_path = write_path.resolve()
                if write_path not in allowed_edit_paths:
                    violations.append(
                        {
                            "line": line_number,
                            "kind": "edit_outside_candidate",
                            "path": str(write_path),
                            "message": "file edit touched something other than candidate_model_new.py",
                        }
                    )
            continue

        if kind == "command_execution":
            command = event.get("command")
            if not isinstance(command, str):
                continue
            snippet = _extract_shell_snippet(command)
            snippet, outside_workspace_message = _normalize_workspace_snippet(
                snippet,
                workspace,
            )
            if outside_workspace_message:
                violations.append(
                    {
                        "line": line_number,
                        "kind": "command_outside_workspace",
                        "command": command,
                        "message": outside_workspace_message,
                    }
                )
                continue
            effective = (snippet or "").strip()
            if not _is_allowed_claude_shell_command(effective):
                violations.append(
                    {
                        "line": line_number,
                        "kind": "disallowed_shell_command",
                        "command": command,
                        "message": "shell execution used something other than the local wrapper commands",
                    }
                )
            lowered = effective.lower()
            if any(marker in lowered for marker in _FORBIDDEN_INSPECTION_MARKERS):
                violations.append(
                    {
                        "line": line_number,
                        "kind": "forbidden_compiler_artifact_inspection",
                        "command": command,
                        "message": "command inspected compiler or generated-kernel artifacts",
                    }
                )
            if any(lowered.startswith(prefix) for prefix in _FORBIDDEN_MONITORING_PREFIXES) or any(
                marker in lowered for marker in _FORBIDDEN_MONITORING_MARKERS
            ):
                violations.append(
                    {
                        "line": line_number,
                        "kind": "forbidden_process_or_gpu_monitoring",
                        "command": command,
                        "message": "command used process/GPU monitoring outside the wrapper contract",
                    }
                )
            continue

        if kind in {"assistant_text", "raw_event", "assistant_block", "tool_use", "subagent_spawn", "wait"}:
            lowered_text = text.lower()
            if any(marker in lowered_text for marker in _FORBIDDEN_DISCUSSION_MARKERS):
                violations.append(
                    {
                        "line": line_number,
                        "kind": "forbidden_compiler_artifact_discussion",
                        "message": "trace discussed compiler/generated-kernel artifacts",
                    }
                )

    return {
        "valid": not violations,
        "violations": violations,
        "trace_counts": counts,
        "web_searches": web_searches,
    }
