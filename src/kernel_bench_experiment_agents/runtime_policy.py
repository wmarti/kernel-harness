"""Render shared Codex and Claude runtime config from the harness policy.

The launcher keeps one shared tool-private config home per tool under `state/config/`. Those dirs hold
runtime config, copied auth, helper agents, and any tool-managed local state, while the workspace
remains free of tool auth/config files. Tool runtimes talk to a launcher-owned MCP sidecar through a
small stdio-to-socket proxy so the real MCP backend can stay outside any future runtime sandbox.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .agent_specs import write_shared_helper_agent_specs
from .policy_model import ALLOWED_WEB_DOMAINS, MCP_SERVER_NAME, claude_mcp_tool_names
from .project import ensure_dir, write_text


MCP_PROXY_ENV_VARS: tuple[str, ...] = ("KBH_MCP_SOCKET",)


MCP_SERVER_CONTEXT_ENV_VARS: tuple[str, ...] = (
    "DATA_ROOT",
    "KBH_WORKSPACE",
    "KBH_CLIENT_TOOL",
    "KBH_MCP_EVENTS_PATH",
)


def _python_command() -> str:
    """Return the exact Python executable that launched the harness.

    The MCP server must start under the same environment that has the harness installed. Hard-coding
    `python` is fragile when Codex or Claude launch from outside the activated environment.
    """
    # Preserve the venv entrypoint path instead of resolving symlinks down to the base interpreter.
    # The resolved base interpreter may not have the harness installed, which breaks `-m ...mcp_proxy`.
    return str(Path(sys.executable).expanduser())


def _copy_if_exists(source: Path, target: Path) -> Path | None:
    if not source.exists() or not source.is_file():
        return None
    ensure_dir(target.parent)
    shutil.copy2(source, target)
    return target


def sync_repo_auth_into_shared_tool_state(config_root: Path, *, repo_root: Path | None = None) -> list[Path]:
    """Copy repo-root auth caches into the generated shared tool homes.

    The user authenticates once under repo-root `.codex/` and `.claude/`; the harness recreates
    `state/config/` as needed and re-seeds just the auth files from those source dirs.
    """
    repo_root = (repo_root or Path.cwd()).expanduser().resolve()
    config_root = ensure_dir(config_root.expanduser().resolve())
    written: list[Path] = []

    copied = _copy_if_exists(repo_root / ".codex" / "auth.json", config_root / "codex" / "auth.json")
    if copied is not None:
        written.append(copied)

    copied = _copy_if_exists(
        repo_root / ".claude" / ".credentials.json",
        config_root / "claude" / ".credentials.json",
    )
    if copied is not None:
        written.append(copied)

    return written



def render_codex_config() -> str:
    """Render the shared Codex config that lives under CODEX_HOME."""
    allowed_domains = ", ".join(f'"{domain}"' for domain in ALLOWED_WEB_DOMAINS)
    env_vars = ", ".join(f'"{name}"' for name in MCP_PROXY_ENV_VARS)
    python_command = json.dumps(_python_command())
    return (
        '# Generated from src/kernel_bench_experiment_agents/runtime_policy.py\n'
        'personality = "pragmatic"\n'
        'approval_policy = "never"\n'
        'sandbox_mode = "read-only"\n'
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
        f'[mcp_servers.{MCP_SERVER_NAME}]\n'
        f'command = {python_command}\n'
        'args = ["-m", "kernel_bench_experiment_agents.mcp_proxy"]\n'
        f'env_vars = [{env_vars}]\n'
        'required = true\n'
        'startup_timeout_sec = 20\n\n'
        '[tools]\n'
        f'web_search = {{ context_size = "low", allowed_domains = [{allowed_domains}] }}\n'
    )



def claude_settings_payload() -> dict[str, object]:
    """Build the Claude settings payload that lives under CLAUDE_CONFIG_DIR/settings.json."""
    allow_tools = [
        *(f"WebFetch(domain:{domain})" for domain in ALLOWED_WEB_DOMAINS),
        *claude_mcp_tool_names(),
    ]
    return {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "disableBypassPermissionsMode": "disable",
        "permissions": {
            "allow": allow_tools,
            "deny": [
                "Read",
                "Write",
                "Edit",
                "MultiEdit",
                "Bash",
                "Glob",
                "Grep",
                "LS",
            ],
        },
        "sandbox": {"enabled": False},
    }



def claude_user_config_payload() -> dict[str, object]:
    """Build the shared Claude user config that registers the harness MCP proxy."""
    return {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "type": "stdio",
                "command": _python_command(),
                "args": ["-m", "kernel_bench_experiment_agents.mcp_proxy"],
                "env": {name: f"${{{name}:-}}" for name in MCP_PROXY_ENV_VARS},
            }
        }
    }



def render_claude_settings() -> str:
    return json.dumps(claude_settings_payload(), indent=2, sort_keys=True) + "\n"



def render_claude_user_config() -> str:
    return json.dumps(claude_user_config_payload(), indent=2, sort_keys=True) + "\n"



def write_shared_tool_state(config_root: Path, *, repo_root: Path | None = None) -> list[Path]:
    config_root = ensure_dir(config_root.expanduser().resolve())
    codex_dir = ensure_dir(config_root / "codex")
    claude_dir = ensure_dir(config_root / "claude")
    codex_path = codex_dir / "config.toml"
    claude_settings_path = claude_dir / "settings.json"
    claude_user_config_path = claude_dir / ".claude.json"
    write_text(codex_path, render_codex_config())
    write_text(claude_settings_path, render_claude_settings())
    write_text(claude_user_config_path, render_claude_user_config())
    written = [codex_path, claude_settings_path, claude_user_config_path]
    written.extend(sync_repo_auth_into_shared_tool_state(config_root, repo_root=repo_root))
    written.extend(
        write_shared_helper_agent_specs(codex_home=codex_dir, claude_config_dir=claude_dir)
    )
    return written
