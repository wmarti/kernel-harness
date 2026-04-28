"""Call the launcher-owned workspace command broker from wrappers or MCP."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any

from kernel_bench_experiment_agents.agent_contract.policy import COMMAND_TOOL_SPECS


DEFAULT_BROKER_SOCKET_TIMEOUT_SECONDS = 1200.0


def send_request(
    *,
    socket_path: Path,
    payload: dict[str, Any],
    timeout_seconds: float = DEFAULT_BROKER_SOCKET_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout_seconds)
        try:
            client.connect(str(socket_path))
            client.sendall((json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
            client.shutdown(socket.SHUT_WR)
            response = b""
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response += chunk
        except socket.timeout as exc:
            raise SystemExit(
                f"command broker at {socket_path} timed out after {timeout_seconds:g}s"
            ) from exc
    if not response.strip():
        raise SystemExit(f"command broker at {socket_path} returned an empty response")
    decoded = json.loads(response.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise SystemExit(f"command broker at {socket_path} returned a non-object response")
    return decoded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kbh-command-client")
    parser.add_argument("--socket", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for spec in COMMAND_TOOL_SPECS:
        command = subparsers.add_parser(spec.cli_name, help=spec.purpose)
        if spec.name == "complete_problem":
            command.add_argument("--summary", required=True)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    request: dict[str, Any] = {"command": args.command.replace("-", "_")}
    if request["command"] == "complete_problem":
        request["summary"] = args.summary

    response = send_request(
        socket_path=Path(args.socket).expanduser().resolve(),
        payload=request,
    )
    stdout = response.get("stdout")
    if isinstance(stdout, str) and stdout:
        sys.stdout.write(stdout)
    if response.get("ok") is True:
        return 0
    error = response.get("error")
    if isinstance(error, str) and error.strip():
        print(error, file=sys.stderr)
    else:
        print("command broker request failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
