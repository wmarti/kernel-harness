"""Render shell wrappers that call the launcher-owned command broker."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from kernel_bench_experiment_agents.agent_contract.policy import COMMAND_TOOL_SPECS, CommandToolSpec
from kernel_bench_experiment_agents.runtime.project import make_executable, write_text


def write_workspace_script(path: Path, content: str) -> None:
    write_text(path, content)
    make_executable(path)


def workspace_wrapper_common() -> str:
    return dedent(
        """
        #!/usr/bin/env bash
        set -euo pipefail

        if [[ -z "${KBH_COMMAND_SOCKET:-}" ]]; then
          echo "KBH_COMMAND_SOCKET is not set; brokered workspace commands require the launcher-owned command broker." >&2
          exit 1
        fi

        PYTHON_BIN="${PYTHON:-python}"
        if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
          echo "python is not on PATH. Export PYTHON to the harness environment interpreter." >&2
          exit 1
        fi
        """
    ).lstrip()


def generate_command_wrapper(spec: CommandToolSpec) -> str:
    command = (
        '"${PYTHON_BIN}" -m kernel_bench_experiment_agents.command_client '
        f'--socket "${{KBH_COMMAND_SOCKET}}" {spec.cli_name}'
    )
    if spec.name == "complete_problem":
        validation = dedent(
            """
            ORIGINAL_ARGS=("$@")
            HAVE_SUMMARY=false
            while [[ $# -gt 0 ]]; do
              case "$1" in
                --summary)
                  shift
                  if [[ $# -eq 0 ]]; then
                    echo "complete_problem.sh: --summary requires a value" >&2
                    exit 2
                  fi
                  HAVE_SUMMARY=true
                  ;;
                --summary=*)
                  HAVE_SUMMARY=true
                  ;;
                *)
                  echo "complete_problem.sh only accepts --summary" >&2
                  exit 2
                  ;;
              esac
              shift
            done

            if [[ "${HAVE_SUMMARY}" != true ]]; then
              echo "complete_problem.sh requires --summary" >&2
              exit 2
            fi
            """
        ).lstrip()
        return workspace_wrapper_common() + validation + command + ' "${ORIGINAL_ARGS[@]}"\n'
    return workspace_wrapper_common() + command + "\n"


def write_default_workspace_wrappers(
    *,
    bin_dir: Path,
    run_name: str,
    level: int,
    problem_id: int,
    dataset_src: str,
    num_gpus: int,
    precision: str,
) -> list[Path]:
    _ = (run_name, level, problem_id, dataset_src, num_gpus, precision)
    written: list[Path] = []
    for spec in COMMAND_TOOL_SPECS:
        path = bin_dir / spec.wrapper_name
        write_workspace_script(path, generate_command_wrapper(spec))
        written.append(path)
    return written
