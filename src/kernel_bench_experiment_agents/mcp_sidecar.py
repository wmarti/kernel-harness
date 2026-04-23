"""Host a launcher-owned Unix-socket relay for the real stdio MCP server."""

from __future__ import annotations

import argparse
import os
import socket
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

from .runtime_policy import MCP_SERVER_CONTEXT_ENV_VARS


BUFFER_SIZE = 64 * 1024


class McpRelayServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(
        self,
        socket_path: str,
        *,
        command: list[str],
        env: dict[str, str],
    ) -> None:
        self.command = command
        self.env = env
        super().__init__(socket_path, McpRelayHandler)


class McpRelayHandler(socketserver.BaseRequestHandler):
    server: McpRelayServer

    def handle(self) -> None:
        process = subprocess.Popen(
            self.server.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=self.server.env,
            bufsize=0,
        )

        assert process.stdin is not None
        assert process.stdout is not None

        to_server = threading.Thread(
            target=self._pump_socket_to_stdin,
            args=(process,),
            daemon=True,
        )
        from_server = threading.Thread(
            target=self._pump_stdout_to_socket,
            args=(process,),
            daemon=True,
        )
        to_server.start()
        from_server.start()
        to_server.join()
        from_server.join()
        self._terminate_if_needed(process)

    def _pump_socket_to_stdin(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stdin is not None
        try:
            while True:
                chunk = self.request.recv(BUFFER_SIZE)
                if not chunk:
                    break
                process.stdin.write(chunk)
                process.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    def _pump_stdout_to_socket(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stdout is not None
        fileno = process.stdout.fileno()
        try:
            while True:
                chunk = os.read(fileno, BUFFER_SIZE)
                if not chunk:
                    break
                self.request.sendall(chunk)
        except BrokenPipeError:
            pass
        finally:
            try:
                self.request.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    @staticmethod
    def _terminate_if_needed(process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait(timeout=1.0)
            return
        except subprocess.TimeoutExpired:
            pass

        process.terminate()
        try:
            process.wait(timeout=5.0)
            return
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the KernelBench MCP sidecar relay.")
    parser.add_argument("--socket", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    socket_path = Path(args.socket).expanduser().resolve()
    missing_env = [name for name in MCP_SERVER_CONTEXT_ENV_VARS if not os.environ.get(name, "").strip()]
    if missing_env:
        raise SystemExit(
            "Missing required environment variables for MCP sidecar: "
            + ", ".join(sorted(missing_env))
        )
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()

    command = [sys.executable, "-m", "kernel_bench_experiment_agents.mcp"]
    env = os.environ.copy()
    server = McpRelayServer(str(socket_path), command=command, env=env)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
