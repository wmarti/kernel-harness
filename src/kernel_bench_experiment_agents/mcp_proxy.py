"""Proxy stdio MCP traffic from the runtime into a launcher-owned Unix socket."""

from __future__ import annotations

import os
import socket
import sys
import threading


BUFFER_SIZE = 64 * 1024


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable for MCP proxy: {name}")
    return value


def _pump_reader_to_socket(reader, sock: socket.socket) -> None:
    fileno = reader.fileno()
    try:
        while True:
            chunk = os.read(fileno, BUFFER_SIZE)
            if not chunk:
                break
            sock.sendall(chunk)
    finally:
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _pump_socket_to_writer(sock: socket.socket, writer) -> None:
    try:
        while True:
            chunk = sock.recv(BUFFER_SIZE)
            if not chunk:
                break
            writer.write(chunk)
            writer.flush()
    finally:
        try:
            writer.flush()
        except Exception:
            pass


def main() -> int:
    socket_path = _required_env("KBH_MCP_SOCKET")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(socket_path)
    except OSError as exc:
        raise SystemExit(f"Failed to connect to launcher MCP socket at {socket_path}: {exc}") from exc

    stdin_thread = threading.Thread(
        target=_pump_reader_to_socket,
        args=(sys.stdin.buffer, sock),
        daemon=True,
    )
    stdout_thread = threading.Thread(
        target=_pump_socket_to_writer,
        args=(sock, sys.stdout.buffer),
        daemon=True,
    )
    stdin_thread.start()
    stdout_thread.start()
    stdin_thread.join()
    stdout_thread.join()
    sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
