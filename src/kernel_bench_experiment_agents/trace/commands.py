"""Implement the trace-materialization command that normalizes raw Codex and Claude event streams.

The launcher runs this after each solver session so later modules can read one common trace IR format.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from kernel_bench_experiment_agents.runtime.common import emit_json, normalize_tool_name
from kernel_bench_experiment_agents.summary.completion import (
    annotate_completion_outcomes,
    apply_trace_audit_to_completion,
)
from kernel_bench_experiment_agents.trace.sidecar import load_mcp_ir_events
from kernel_bench_experiment_agents.runtime.project import now_iso, relative_path_within, write_json, write_text
from kernel_bench_experiment_agents.trace.analysis import audit_trace, trace_cost_usd, trace_counts, trace_usage_summary, web_searches_from_ir
from kernel_bench_experiment_agents.trace.ir import final_message_from_raw_events, load_trace_event_entries, materialize_trace_ir
from kernel_bench_experiment_agents.workspace.archive import sample_manifest_entries
from kernel_bench_experiment_agents.workspace.paths import read_json_file



def write_final_message(
    *,
    output_path: Path,
    tool: str,
    raw_events: list[dict],
) -> None:
    if (
        normalize_tool_name(tool) == "codex"
        and output_path.exists()
        and output_path.read_text(encoding="utf-8").strip()
    ):
        return
    final_text = final_message_from_raw_events(raw_events, tool=tool)
    if final_text:
        write_text(output_path, final_text)




def _trace_source_reference(*, events_path: Path, output_path: Path) -> str:
    problem_root = output_path.parent.parent
    try:
        return relative_path_within(events_path, problem_root)
    except ValueError:
        return events_path.name


def command_materialize_agent_trace(args: argparse.Namespace) -> None:
    tool = normalize_tool_name(args.tool)
    events_path = Path(args.events_path).expanduser().resolve()
    output_path = Path(args.output_path).expanduser().resolve()
    raw_events, raw_event_entries = load_trace_event_entries(events_path, warn=True)
    ir_events = materialize_trace_ir(raw_event_entries, tool=tool)
    if getattr(args, 'mcp_events_path', None):
        mcp_events_path = Path(args.mcp_events_path).expanduser().resolve()
        ir_events.extend(load_mcp_ir_events(mcp_events_path, warn=True, starting_line=len(raw_event_entries) + 1_000_000))

    token_usage = trace_usage_summary(raw_events, tool=tool)
    cost_usd = trace_cost_usd(raw_events, tool=tool)
    trace_counts_payload = trace_counts(ir_events, raw_events=raw_events, tool=tool)
    web_searches = web_searches_from_ir(ir_events)
    audit = None
    if args.workspace:
        audit = audit_trace(
            ir_events=ir_events,
            workspace=Path(args.workspace).expanduser().resolve(),
            raw_events=raw_events,
            tool=tool,
        )
    source_events_path = _trace_source_reference(events_path=events_path, output_path=output_path)
    mcp_source_events_path = None
    if getattr(args, 'mcp_events_path', None):
        try:
            mcp_source_events_path = _trace_source_reference(
                events_path=Path(args.mcp_events_path).expanduser().resolve(),
                output_path=output_path,
            )
        except FileNotFoundError:
            mcp_source_events_path = None
    payload = {
        "tool": tool,
        "source_events_path": source_events_path,
        "generated_at": now_iso(),
        "trace_ir_version": 1,
        "mcp_source_events_path": mcp_source_events_path,
        "num_raw_events": len(raw_events),
        "num_ir_events": len(ir_events),
        "token_usage": token_usage,
        "cost_usd": cost_usd,
        "trace_counts": trace_counts_payload,
        "web_searches": web_searches,
        "audit": audit,
        "ir_events": ir_events,
    }
    write_json(output_path, payload)
    if args.final_message_path:
        write_final_message(
            output_path=Path(args.final_message_path).expanduser().resolve(),
            tool=tool,
            raw_events=raw_events,
        )
    if args.completion_path:
        completion_path = Path(args.completion_path).expanduser().resolve()
        if completion_path.exists():
            completion_payload = read_json_file(completion_path)
            completion_payload["tool"] = tool
            completion_payload["token_usage"] = token_usage
            completion_payload["cost_usd"] = cost_usd
            completion_payload["trace_counts"] = trace_counts_payload
            completion_payload["web_searches"] = web_searches
            if audit is not None:
                completion_payload = apply_trace_audit_to_completion(
                    completion_payload,
                    audit,
                )
            completion_payload = annotate_completion_outcomes(
                completion_payload,
                sample_entries=(
                    sample_manifest_entries(
                        str(completion_payload.get("run_name") or ""),
                        int(completion_payload.get("level") or 0),
                        int(completion_payload.get("problem_id") or 0),
                    )
                    if completion_payload.get("run_name") and completion_payload.get("level") and completion_payload.get("problem_id")
                    else None
                ),
            )
            write_json(completion_path, completion_payload)
            if args.workspace:
                write_json(
                    Path(args.workspace).expanduser().resolve() / "completion.json",
                    completion_payload,
                )
    emit_json(
        {
            "output_path": str(output_path),
            "num_raw_events": len(raw_events),
            "num_ir_events": len(ir_events),
            "source_events_path": source_events_path,
            "token_usage": token_usage,
            "cost_usd": cost_usd,
            "audit": audit,
        }
    )
