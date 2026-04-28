"""Render Codex and Claude runtime config from the harness policy.

The launcher creates one per-problem tool-private config home under `state/tool_state/`.
Those dirs hold runtime config, copied auth, helper agents, and tool-managed local state, while the
problem workspace remains free of tool auth/config files.
"""

from __future__ import annotations

import json
import shlex
import shutil
import sys
from pathlib import Path

from kernel_bench_experiment_agents.agent_contract.agent_specs import write_shared_helper_agent_specs
from kernel_bench_experiment_agents.agent_contract.policy import (
    ALLOWED_WEB_DOMAINS,
    COMMAND_MCP_SERVER_NAME,
    COMMAND_TOOL_SPECS,
    claude_command_mcp_tool_names,
)
from kernel_bench_experiment_agents.runtime.project import ensure_dir, make_executable, write_text


COMMAND_MCP_SERVER_ENV_VARS: tuple[str, ...] = (
    "KBH_COMMAND_SOCKET",
)

CLAUDE_BUILTIN_TOOLS: tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "LS",
    "Glob",
    "Grep",
    "Task",
    "WebSearch",
    "WebFetch",
)

CLAUDE_ALLOWED_TOOL_PATTERNS: tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "LS",
    "Glob",
    "Grep",
    "Task",
    "WebSearch",
    *(f"WebFetch(domain:{domain})" for domain in ALLOWED_WEB_DOMAINS),
    *claude_command_mcp_tool_names(),
)

CLAUDE_DENIED_TOOLS: tuple[str, ...] = (
    "AskUserQuestion",
    "Bash",
    "CronCreate",
    "CronDelete",
    "CronList",
    "EnterPlanMode",
    "EnterWorktree",
    "ExitPlanMode",
    "ExitWorktree",
    "LSP",
    "Monitor",
    "NotebookEdit",
    "PowerShell",
    "RemoteTrigger",
    "SendMessage",
    "Skill",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "TaskUpdate",
    "TeamCreate",
    "TeamDelete",
    "TodoWrite",
    "ToolSearch",
)


def _python_command() -> str:
    """Return the exact Python executable path that launched the harness.

    Do not resolve symlinks here. Virtualenv/pyenv interpreters are often shim or symlink paths into
    a base interpreter; resolving them can bypass the environment that actually has the harness and
    MCP SDK installed.
    """
    return str(Path(sys.executable).expanduser())


def _command_mcp_server_path(tool_config_dir: Path) -> Path:
    return tool_config_dir / "kernelbench_command_mcp.py"


def _mirror_optional_file(source: Path, target: Path) -> Path | None:
    if source.exists() and source.is_file():
        ensure_dir(target.parent)
        shutil.copy2(source, target)
        return target
    if target.exists() or target.is_symlink():
        target.unlink()
    return None


def sync_repo_auth_into_shared_tool_state(config_root: Path, *, repo_root: Path | None = None) -> list[Path]:
    """Copy repo-root auth caches into the generated per-problem tool homes.

    The user authenticates once under repo-root `.codex/` and `.claude/`; the harness recreates
    `state/tool_state/` as needed and re-seeds just the auth files from those source dirs.
    """
    repo_root = (repo_root or Path.cwd()).expanduser().resolve()
    config_root = ensure_dir(config_root.expanduser().resolve())
    written: list[Path] = []

    copied = _mirror_optional_file(
        repo_root / ".codex" / "auth.json",
        config_root / "codex" / "auth.json",
    )
    if copied is not None:
        written.append(copied)

    copied = _mirror_optional_file(
        repo_root / ".claude" / ".credentials.json",
        config_root / "claude" / ".credentials.json",
    )
    if copied is not None:
        written.append(copied)

    return written

def render_command_mcp_server() -> str:
    """Render a standalone command MCP server for the solver sandbox.

    The solver runtime should not need the harness source tree mounted just to expose brokered
    commands. The generated server imports only the MCP SDK and the standard library.
    """
    specs = {
        spec.name: {
            "purpose": spec.purpose,
            "read_only": spec.read_only,
            "destructive": spec.destructive,
        }
        for spec in COMMAND_TOOL_SPECS
    }
    return f'''#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.fastmcp import FastMCP


SERVER_NAME = {COMMAND_MCP_SERVER_NAME!r}
TOOL_SPECS = json.loads({json.dumps(json.dumps(specs, sort_keys=True))})
BROKER_SOCKET_TIMEOUT_SECONDS = 1200.0

mcp = FastMCP(SERVER_NAME)


def _required_socket_path() -> Path:
    value = os.environ.get("KBH_COMMAND_SOCKET", "").strip()
    if not value:
        raise RuntimeError("KBH_COMMAND_SOCKET is required for command MCP access")
    return Path(value).expanduser().resolve()


def _send_request(*, socket_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(BROKER_SOCKET_TIMEOUT_SECONDS)
        try:
            client.connect(str(socket_path))
            client.sendall((json.dumps(payload, sort_keys=True) + "\\n").encode("utf-8"))
            client.shutdown(socket.SHUT_WR)
            response = b""
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                response += chunk
        except socket.timeout as exc:
            raise RuntimeError(
                f"command broker at {{socket_path}} timed out after {{BROKER_SOCKET_TIMEOUT_SECONDS:g}}s"
            ) from exc
    if not response.strip():
        raise RuntimeError(f"command broker at {{socket_path}} returned an empty response")
    decoded = json.loads(response.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError(f"command broker at {{socket_path}} returned a non-object response")
    return decoded


def _annotations(*, read_only: bool = False, destructive: bool = False) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=destructive,
        openWorldHint=False,
        idempotentHint=read_only,
    )


def _tool_result(response: dict[str, Any]) -> types.CallToolResult:
    text = str(response.get("stdout") or "")
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=response.get("payload") if isinstance(response.get("payload"), dict) else None,
        isError=False,
    )


def _invoke(command: str, **arguments: Any) -> types.CallToolResult:
    request: dict[str, Any] = {{"command": command}}
    request.update(arguments)
    response = _send_request(socket_path=_required_socket_path(), payload=request)
    if response.get("ok") is True:
        return _tool_result(response)
    error = str(response.get("error") or "command broker request failed")
    stdout = str(response.get("stdout") or "")
    payload = response.get("payload")
    detail = error if not stdout else f"{{error}}\\n\\n{{stdout}}"
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=detail)],
        structuredContent=payload if isinstance(payload, dict) else None,
        isError=True,
    )


def _purpose(name: str) -> str:
    return str(TOOL_SPECS[name]["purpose"])


@mcp.resource(
    "kb://commands",
    name="workspace_commands",
    description="The privileged command tools available through the launcher-owned broker.",
    mime_type="application/json",
)
def workspace_commands() -> str:
    return json.dumps({{"server": SERVER_NAME, "tools": list(TOOL_SPECS)}}, indent=2, sort_keys=True)


@mcp.tool(name="run_candidate", description=_purpose("run_candidate"), annotations=_annotations(destructive=True))
def run_candidate() -> types.CallToolResult:
    return _invoke("run_candidate")


@mcp.tool(name="profile_ncu", description=_purpose("profile_ncu"), annotations=_annotations(destructive=True))
def profile_ncu() -> types.CallToolResult:
    return _invoke("profile_ncu")


@mcp.tool(name="goal_status", description=_purpose("goal_status"), annotations=_annotations(read_only=True))
def goal_status() -> types.CallToolResult:
    return _invoke("goal_status")


@mcp.tool(name="best_result", description=_purpose("best_result"), annotations=_annotations(read_only=True))
def best_result() -> types.CallToolResult:
    return _invoke("best_result")


@mcp.tool(name="complete_problem", description=_purpose("complete_problem"), annotations=_annotations(destructive=True))
def complete_problem(summary: str) -> types.CallToolResult:
    return _invoke("complete_problem", summary=summary)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
'''


def render_codex_config(*, command_mcp_path: Path) -> str:
    """Render the per-problem Codex config.toml."""
    allowed_domains = ", ".join(f'"{domain}"' for domain in ALLOWED_WEB_DOMAINS)
    env_vars = ", ".join(f'"{name}"' for name in COMMAND_MCP_SERVER_ENV_VARS)
    python_command = json.dumps(_python_command())
    command_mcp_arg = json.dumps(str(command_mcp_path))
    payload = (
        '# Generated from src/kernel_bench_experiment_agents/runtime/policy.py\n'
        'personality = "pragmatic"\n'
        'approval_policy = "never"\n'
        'sandbox_mode = "workspace-write"\n'
        'project_root_markers = []\n'
        'allow_login_shell = false\n'
        'web_search = "live"\n'
        'project_doc_max_bytes = 65536\n'
        'model_auto_compact_token_limit = 240000\n\n'
        '[features]\n'
        'unified_exec = false\n'
        'shell_tool = false\n\n'
        '[agents]\n'
        'max_threads = 6\n'
        'max_depth = 1\n\n'
        f'[mcp_servers.{COMMAND_MCP_SERVER_NAME}]\n'
        f'command = {python_command}\n'
        f'args = [{command_mcp_arg}]\n'
        f'env_vars = [{env_vars}]\n'
        'required = true\n'
        'startup_timeout_sec = 20\n'
        'tool_timeout_sec = 1200\n\n'
        '[tools]\n'
        f'web_search = {{ context_size = "low", allowed_domains = [{allowed_domains}] }}\n'
    )
    return payload


def claude_websearch_hook_path(claude_config_dir: Path) -> Path:
    return claude_config_dir / "hooks" / "restrict_nvidia_docs_websearch.py"



def render_claude_websearch_hook() -> str:
    allowed_domains = ", ".join(json.dumps(domain) for domain in ALLOWED_WEB_DOMAINS)
    reason = json.dumps(
        f"Restrict WebSearch to {', '.join(ALLOWED_WEB_DOMAINS)} for this harness."
    )
    return (
        "#!/usr/bin/env python3\n"
        "from __future__ import annotations\n\n"
        "import json\n"
        "import sys\n\n"
        f"ALLOWED_DOMAINS = [{allowed_domains}]\n\n"
        "def main() -> int:\n"
        "    try:\n"
        "        event = json.load(sys.stdin)\n"
        "    except Exception:\n"
        "        return 0\n\n"
        "    tool_input = event.get('tool_input')\n"
        "    if not isinstance(tool_input, dict):\n"
        "        return 0\n\n"
        "    updated_input = dict(tool_input)\n"
        "    updated_input['allowed_domains'] = ALLOWED_DOMAINS\n"
        "    payload = {\n"
        "        'hookSpecificOutput': {\n"
        "            'hookEventName': 'PreToolUse',\n"
        "            'permissionDecision': 'allow',\n"
        f"            'permissionDecisionReason': {reason},\n"
        "            'updatedInput': updated_input,\n"
        "        }\n"
        "    }\n"
        "    json.dump(payload, sys.stdout)\n"
        "    sys.stdout.write('\\n')\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n"
    )



def _claude_websearch_hook_command(hook_path: Path) -> str:
    return f"{shlex.quote(_python_command())} {shlex.quote(str(hook_path))}"



def claude_settings_payload(*, websearch_hook_path: Path) -> dict[str, object]:
    """Build the Claude settings payload that lives under CLAUDE_CONFIG_DIR/settings.json."""
    return {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "permissions": {
            "allow": list(CLAUDE_ALLOWED_TOOL_PATTERNS),
            "deny": list(CLAUDE_DENIED_TOOLS),
            "disableBypassPermissionsMode": "disable",
        },
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "WebSearch",
                    "hooks": [
                        {
                            "type": "command",
                            "command": _claude_websearch_hook_command(websearch_hook_path),
                        }
                    ],
                }
            ]
        },
        "sandbox": {"enabled": False},
    }



def claude_user_config_payload(*, command_mcp_path: Path) -> dict[str, object]:
    """Build the per-problem Claude user config that registers the command MCP server."""
    return {
        "mcpServers": {
            COMMAND_MCP_SERVER_NAME: {
                "type": "stdio",
                "command": _python_command(),
                "args": [str(command_mcp_path)],
                "env": {name: f"${{{name}:-}}" for name in COMMAND_MCP_SERVER_ENV_VARS},
            }
        }
    }



def render_claude_settings(*, websearch_hook_path: Path) -> str:
    return json.dumps(claude_settings_payload(websearch_hook_path=websearch_hook_path), indent=2, sort_keys=True) + "\n"



def render_claude_user_config(*, command_mcp_path: Path) -> str:
    return json.dumps(
        claude_user_config_payload(command_mcp_path=command_mcp_path),
        indent=2,
        sort_keys=True,
    ) + "\n"


def write_shared_tool_state(config_root: Path, *, repo_root: Path | None = None) -> list[Path]:
    config_root = ensure_dir(config_root.expanduser().resolve())
    codex_dir = ensure_dir(config_root / "codex")
    claude_dir = ensure_dir(config_root / "claude")
    codex_path = codex_dir / "config.toml"
    claude_settings_path = claude_dir / "settings.json"
    claude_user_config_path = claude_dir / ".claude.json"
    claude_websearch_hook = claude_websearch_hook_path(claude_dir)
    codex_command_mcp = _command_mcp_server_path(codex_dir)
    claude_command_mcp = _command_mcp_server_path(claude_dir)
    command_mcp_server = render_command_mcp_server()
    write_text(codex_command_mcp, command_mcp_server)
    make_executable(codex_command_mcp)
    write_text(claude_command_mcp, command_mcp_server)
    make_executable(claude_command_mcp)
    write_text(codex_path, render_codex_config(command_mcp_path=codex_command_mcp))
    write_text(claude_websearch_hook, render_claude_websearch_hook())
    make_executable(claude_websearch_hook)
    write_text(claude_settings_path, render_claude_settings(websearch_hook_path=claude_websearch_hook))
    write_text(claude_user_config_path, render_claude_user_config(command_mcp_path=claude_command_mcp))
    written = [
        codex_command_mcp,
        claude_command_mcp,
        codex_path,
        claude_websearch_hook,
        claude_settings_path,
        claude_user_config_path,
    ]
    written.extend(sync_repo_auth_into_shared_tool_state(config_root, repo_root=repo_root))
    written.extend(
        write_shared_helper_agent_specs(codex_home=codex_dir, claude_config_dir=claude_dir)
    )
    return written
