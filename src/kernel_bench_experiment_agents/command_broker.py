"""Host a launcher-owned Unix-socket command broker for one problem workspace."""

from __future__ import annotations

import argparse
import io
import json
import signal
import socketserver
import sys
import threading
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from kernel_bench_experiment_agents.agent_contract.policy import (
    COMMAND_MCP_SERVER_NAME,
    COMMAND_TOOL_SPECS,
    CommandToolSpec,
    command_tool_spec,
)
from kernel_bench_experiment_agents.kernelbench.commands.profile import command_profile_ncu
from kernel_bench_experiment_agents.kernelbench.commands.run_candidate import command_run_candidate
from kernel_bench_experiment_agents.kernelbench.commands.status import (
    command_best_result,
    command_complete_problem,
    command_goal_status,
)
from kernel_bench_experiment_agents.mcp.trace import append_mcp_event
from kernel_bench_experiment_agents.runtime.project import archive_problem_dir, build_problem_root, repo_root, state_dir
from kernel_bench_experiment_agents.workspace.paths import (
    validate_workspace_assignment,
    workspace_candidate_path,
)


@dataclass(frozen=True)
class BrokerContext:
    workspace: Path
    run_name: str
    level: int
    problem_id: int
    dataset_src: str
    kernelbench_root: str | None
    num_gpu_slots: int
    precision: str
    client_tool: str
    events_path: Path


class InvocationError(RuntimeError):
    def __init__(self, message: str, *, stdout: str, payload: dict[str, Any]) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.payload = payload


def _append_trace_event(
    ctx: BrokerContext,
    *,
    kind: str,
    spec: CommandToolSpec,
    text: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    append_mcp_event(
        ctx.events_path,
        {
            "tool": ctx.client_tool,
            "kind": kind,
            "tool_name": f"mcp__{COMMAND_MCP_SERVER_NAME}__{spec.name}",
            "command": spec.wrapper_path,
            "text": text,
            "metadata": metadata or {},
        },
    )


def _invoke(handler: Callable[[argparse.Namespace], None], namespace: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            handler(namespace)
    except (Exception, SystemExit) as exc:
        stdout = buffer.getvalue()
        payload: dict[str, Any] = {}
        if stdout.strip():
            try:
                decoded = json.loads(stdout)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                payload = decoded
        raise InvocationError(str(exc), stdout=stdout, payload=payload) from exc
    stdout = buffer.getvalue()
    payload = json.loads(stdout) if stdout.strip() else {}
    return stdout, payload


def _redact_solver_text(ctx: BrokerContext, text: str) -> str:
    replacements = {
        str(ctx.workspace): ".",
        str(archive_problem_dir(ctx.run_name, ctx.level, ctx.problem_id)): "<archive problem>",
        str(build_problem_root(ctx.run_name, ctx.level, ctx.problem_id)): "<build problem>",
        str(state_dir()): "<harness state>",
        str(repo_root()): "<harness repo>",
    }
    for python_path in {sys.prefix, sys.base_prefix, str(Path(sys.executable).parent)}:
        if python_path:
            replacements[str(Path(python_path).expanduser().resolve())] = "<python runtime>"
    redacted = text
    for original, replacement in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        redacted = redacted.replace(original, replacement)
    return redacted


def _sanitize_solver_payload(ctx: BrokerContext, value: Any, *, key: str | None = None) -> Any:
    if key == "traceback" and isinstance(value, str):
        return "[traceback omitted from solver output; use the reported stderr excerpt and workspace mirrors]"
    if isinstance(value, str):
        return _redact_solver_text(ctx, value)
    if isinstance(value, list):
        return [_sanitize_solver_payload(ctx, item) for item in value]
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_solver_payload(ctx, child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    return value


def _solver_response_stdout(ctx: BrokerContext, *, stdout: str, payload: dict[str, Any]) -> str:
    if payload:
        return json.dumps(_sanitize_solver_payload(ctx, payload), indent=2, sort_keys=True) + "\n"
    return _redact_solver_text(ctx, stdout)


def _handle_run_candidate(ctx: BrokerContext) -> tuple[str, dict[str, Any]]:
    return _invoke(
        command_run_candidate,
        argparse.Namespace(
            candidate=str(workspace_candidate_path(ctx.workspace)),
            run_name=ctx.run_name,
            level=ctx.level,
            problem_id=ctx.problem_id,
            dataset_src=ctx.dataset_src,
            kernelbench_root=ctx.kernelbench_root,
            gpu_id=None,
            num_gpu_slots=ctx.num_gpu_slots,
            timing_method=None,
            backend="cuda",
            precision=ctx.precision,
            num_correct_trials=5,
            num_perf_trials=100,
            prompt_path=None,
            workspace=str(ctx.workspace),
        ),
    )


def _handle_profile_ncu(ctx: BrokerContext) -> tuple[str, dict[str, Any]]:
    return _invoke(
        command_profile_ncu,
        argparse.Namespace(
            candidate=str(workspace_candidate_path(ctx.workspace)),
            run_name=ctx.run_name,
            level=ctx.level,
            problem_id=ctx.problem_id,
            dataset_src=ctx.dataset_src,
            kernelbench_root=ctx.kernelbench_root,
            gpu_id=None,
            num_gpu_slots=ctx.num_gpu_slots,
            sample_id=None,
            ncu_set="full",
            precision=ctx.precision,
            workspace=str(ctx.workspace),
        ),
    )


def _handle_goal_status(ctx: BrokerContext) -> tuple[str, dict[str, Any]]:
    return _invoke(
        command_goal_status,
        argparse.Namespace(
            run_name=ctx.run_name,
            level=ctx.level,
            problem_id=ctx.problem_id,
            workspace=str(ctx.workspace),
        ),
    )


def _handle_best_result(ctx: BrokerContext) -> tuple[str, dict[str, Any]]:
    return _invoke(
        command_best_result,
        argparse.Namespace(
            run_name=ctx.run_name,
            level=ctx.level,
            problem_id=ctx.problem_id,
        ),
    )


def _handle_complete_problem(ctx: BrokerContext, *, summary: str) -> tuple[str, dict[str, Any]]:
    return _invoke(
        command_complete_problem,
        argparse.Namespace(
            run_name=ctx.run_name,
            level=ctx.level,
            problem_id=ctx.problem_id,
            workspace=str(ctx.workspace),
            summary=summary,
            allow_overwrite=False,
        ),
    )


def _dispatch_request(ctx: BrokerContext, request: dict[str, Any]) -> tuple[CommandToolSpec, str, dict[str, Any]]:
    command = str(request.get("command") or "").strip()
    spec = command_tool_spec(command)
    if command == "run_candidate":
        stdout, payload = _handle_run_candidate(ctx)
    elif command == "profile_ncu":
        stdout, payload = _handle_profile_ncu(ctx)
    elif command == "goal_status":
        stdout, payload = _handle_goal_status(ctx)
    elif command == "best_result":
        stdout, payload = _handle_best_result(ctx)
    elif command == "complete_problem":
        summary = str(request.get("summary") or "").strip()
        if not summary:
            raise RuntimeError("summary is required for complete_problem")
        stdout, payload = _handle_complete_problem(ctx, summary=summary)
    else:
        raise RuntimeError(f"unsupported broker command: {command}")
    return spec, stdout, payload


class BrokerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, socket_path: str, *, context: BrokerContext) -> None:
        self.context = context
        self.command_lock = threading.Lock()
        self._active_requests = 0
        self._active_request_condition = threading.Condition()
        super().__init__(socket_path, BrokerHandler)

    def note_request_start(self) -> None:
        with self._active_request_condition:
            self._active_requests += 1

    def note_request_end(self) -> None:
        with self._active_request_condition:
            self._active_requests -= 1
            self._active_request_condition.notify_all()

    def wait_for_idle(self, *, timeout: float | None = None) -> None:
        with self._active_request_condition:
            self._active_request_condition.wait_for(
                lambda: self._active_requests == 0,
                timeout=timeout,
            )


class BrokerHandler(socketserver.StreamRequestHandler):
    server: BrokerServer

    def handle(self) -> None:
        self.server.note_request_start()
        try:
            raw_request = self.rfile.readline()
            if not raw_request:
                return
            try:
                request = json.loads(raw_request.decode("utf-8"))
                if not isinstance(request, dict):
                    raise RuntimeError("request payload must be a JSON object")
                command_name = str(request.get("command") or "")
                spec = command_tool_spec(command_name)
                with self.server.command_lock:
                    spec, stdout, payload = _dispatch_request(self.server.context, request)
                _append_trace_event(
                    self.server.context,
                    kind="command_execution",
                    spec=spec,
                    text=request.get("summary") if spec.name == "complete_problem" else None,
                    metadata=_trace_metadata(spec.name, payload),
                )
                response = {
                    "ok": True,
                    "stdout": _solver_response_stdout(self.server.context, stdout=stdout, payload=payload),
                    "payload": _sanitize_solver_payload(self.server.context, payload),
                }
            except InvocationError as exc:
                payload = exc.payload
                sanitized_error = _redact_solver_text(self.server.context, str(exc))
                try:
                    spec = command_tool_spec(str(request.get("command") or ""))
                    _append_trace_event(
                        self.server.context,
                        kind="command_execution",
                        spec=spec,
                        text=request.get("summary") if spec.name == "complete_problem" else None,
                        metadata={**_trace_metadata(spec.name, payload), "error": sanitized_error},
                    )
                except Exception:
                    pass
                response = {
                    "ok": False,
                    "error": sanitized_error,
                    "stdout": _solver_response_stdout(self.server.context, stdout=exc.stdout, payload=payload),
                    "payload": _sanitize_solver_payload(self.server.context, payload),
                }
            except Exception as exc:  # noqa: BLE001
                response = {"ok": False, "error": _redact_solver_text(self.server.context, str(exc))}
            self.wfile.write((json.dumps(response, sort_keys=True) + "\n").encode("utf-8"))
        finally:
            self.server.note_request_end()


def _trace_metadata(command_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"command_name": command_name}
    if command_name == "run_candidate":
        metadata.update({"status": payload.get("status"), "sample_id": payload.get("sample_id")})
    elif command_name == "profile_ncu":
        metadata.update({"status": payload.get("status"), "profile_id": payload.get("profile_id")})
    elif command_name == "goal_status":
        metadata.update({"status_mode": payload.get("status_mode")})
    elif command_name == "best_result":
        metadata.update({"sample_id": payload.get("sample_id")})
    elif command_name == "complete_problem":
        metadata.update({"terminal_state": payload.get("terminal_state")})
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the KernelBench workspace command broker.")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--level", type=int, required=True)
    parser.add_argument("--problem-id", type=int, required=True)
    parser.add_argument("--dataset-src", default="local")
    parser.add_argument("--kernelbench-root", default=None)
    parser.add_argument("--num-gpu-slots", type=int, default=1)
    parser.add_argument("--precision", default="bf16")
    parser.add_argument("--tool", required=True)
    parser.add_argument("--events-path", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    socket_path = Path(args.socket).expanduser().resolve()
    workspace = Path(args.workspace).expanduser().resolve()
    validate_workspace_assignment(
        workspace,
        run_name=args.run_name,
        level=args.level,
        problem_id=args.problem_id,
    )
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    context = BrokerContext(
        workspace=workspace,
        run_name=args.run_name,
        level=args.level,
        problem_id=args.problem_id,
        dataset_src=args.dataset_src,
        kernelbench_root=args.kernelbench_root,
        num_gpu_slots=args.num_gpu_slots,
        precision=args.precision,
        client_tool=args.tool,
        events_path=Path(args.events_path).expanduser().resolve(),
    )
    server = BrokerServer(str(socket_path), context=context)
    shutdown_requested = threading.Event()

    def _request_shutdown(*_: object) -> None:
        if shutdown_requested.is_set():
            return
        shutdown_requested.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.wait_for_idle(timeout=30.0)
        server.server_close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
