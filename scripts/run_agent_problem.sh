#!/usr/bin/env bash
# Run exactly one solver session for one KernelBench problem.
#
# Run this script from the harness repo root.
#
# Required environment:
#   TOOL=codex|claude
#   KERNELBENCH_ROOT=/path/to/KernelBench
#   HARDWARE_NAME=<timings-subdir name, e.g. H100 or H100_tsubame>
#
# Common overrides:
#   DATA_ROOT=/path/for/archive-and-state   (defaults to ./ from the launch directory)
#   RUN_NAME=kernelbench-codex-h100-v4
#   LEVEL=1
#   PROBLEM_ID=1
#   MODEL=gpt-5.4|claude-opus-4-7
#   TIME_BUDGET_MINUTES=180
#   PRECISION=bf16
#   KERNELBENCH_TIMINGS_DIR=/path/to/KernelBench/results/timing/<hardware>  # optional override
set -euo pipefail

if [[ ! -f "./pyproject.toml" || ! -d "./src/kernel_bench_experiment_agents" ]]; then
  echo "Run scripts/run_agent_problem.sh from the harness repo root." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=./kb_python.sh
source "${SCRIPT_DIR}/kb_python.sh"
PYTHON_BIN="$(resolve_repo_python "${REPO_ROOT}" "./kb setup")"
LANDRUN_BIN="$(resolve_repo_landrun "${REPO_ROOT}" "./kb setup")"
export PATH="${REPO_ROOT}/scripts:${REPO_ROOT}/third_party/bin:${PATH}"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

DATA_ROOT="${DATA_ROOT:-.}"
mkdir -p "${DATA_ROOT}"
DATA_ROOT="$(cd "${DATA_ROOT}" && pwd)"
export DATA_ROOT
KERNELBENCH_ROOT="${KERNELBENCH_ROOT:-${REPO_ROOT}/third_party/KernelBench}"
export KERNELBENCH_ROOT

STATE_ROOT="${DATA_ROOT}/state"
ARCHIVE_ROOT="${DATA_ROOT}/archive"
KBHARNESS_CLI="${REPO_ROOT}/scripts/kbharness"

prepare_tool_state() {
  local config_root="$1"
  TOOL_CONFIG_ROOT="${config_root}" "${PYTHON_BIN}" - <<'PY'
import os
from pathlib import Path
from kernel_bench_experiment_agents.runtime.policy import write_shared_tool_state

write_shared_tool_state(Path(os.environ["TOOL_CONFIG_ROOT"]))
PY
}

terminate_agent_pipeline() {
  local parent_pid="$1"

  if command -v pkill >/dev/null 2>&1; then
    pkill -TERM -P "${parent_pid}" 2>/dev/null || true
  fi
  kill -TERM "${parent_pid}" 2>/dev/null || true
  sleep 5
  if kill -0 "${parent_pid}" 2>/dev/null; then
    if command -v pkill >/dev/null 2>&1; then
      pkill -KILL -P "${parent_pid}" 2>/dev/null || true
    fi
    kill -KILL "${parent_pid}" 2>/dev/null || true
  fi
}

cleanup_runtime() {
  if [[ -n "${COMMAND_BROKER_PID:-}" ]] && kill -0 "${COMMAND_BROKER_PID}" 2>/dev/null; then
    kill -TERM "${COMMAND_BROKER_PID}" 2>/dev/null || true
    wait "${COMMAND_BROKER_PID}" 2>/dev/null || true
  fi
  if [[ -n "${COMMAND_SOCKET_DIR:-}" && -d "${COMMAND_SOCKET_DIR}" ]]; then
    rm -rf "${COMMAND_SOCKET_DIR}"
  fi
  if [[ -n "${RUNTIME_DIR:-}" && -d "${RUNTIME_DIR}" ]]; then
    rm -rf "${RUNTIME_DIR}"
  fi
}

require_command() {
  local name="$1"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "Required command is not on PATH: ${name}" >&2
    exit 1
  fi
}

visible_gpu_slot_count() {
  local raw="${CUDA_VISIBLE_DEVICES:-}"
  local trimmed entry count=0

  if [[ -n "${SLURM_GPUS_ON_NODE:-}" && "${SLURM_GPUS_ON_NODE}" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "${SLURM_GPUS_ON_NODE}"
    return
  fi

  if [[ -z "${raw}" ]]; then
    printf '1\n'
    return
  fi

  IFS=',' read -r -a entries <<< "${raw}"
  for entry in "${entries[@]}"; do
    trimmed="${entry#${entry%%[![:space:]]*}}"
    trimmed="${trimmed%${trimmed##*[![:space:]]}}"
    [[ -n "${trimmed}" ]] || continue
    count=$((count + 1))
  done

  if (( count == 0 )); then
    printf '1\n'
  else
    printf '%s\n' "${count}"
  fi
}

# Resolve user-facing launcher settings.
TOOL="${TOOL:-codex}"
case "${TOOL}" in
  codex|claude) ;;
  *)
    echo "Unsupported TOOL=${TOOL}. Expected codex or claude." >&2
    exit 1
    ;;
esac

DEFAULT_MODEL="gpt-5.4"
if [[ "${TOOL}" == "claude" ]]; then
  DEFAULT_MODEL="claude-opus-4-7"
fi

RUN_NAME="${RUN_NAME:-kernelbench-${TOOL}-$(date -u +%Y%m%dT%H%M%SZ)}"
LEVEL="${LEVEL:-1}"
PROBLEM_ID="${PROBLEM_ID:-1}"
DATASET_SRC="${DATASET_SRC:-local}"
MODEL="${MODEL:-${DEFAULT_MODEL}}"
TIME_BUDGET_MINUTES="${TIME_BUDGET_MINUTES:-180}"
HARDWARE_NAME="${HARDWARE_NAME:-}"
KERNELBENCH_TIMINGS_DIR="${KERNELBENCH_TIMINGS_DIR:-}"
PRECISION="${PRECISION:-bf16}"
NUM_GPU_SLOTS="$(visible_gpu_slot_count)"
BUDGET_POLL_SECONDS=30
TOOL_CONFIG_ROOT="${STATE_ROOT}/tool_state/${RUN_NAME}/level_${LEVEL}/problem_${PROBLEM_ID}"
CODEX_PROBLEM_HOME="${TOOL_CONFIG_ROOT}/codex"
CLAUDE_PROBLEM_CONFIG_DIR="${TOOL_CONFIG_ROOT}/claude"

if [[ ! "${RUN_NAME}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "RUN_NAME may contain only ASCII letters, digits, dot, underscore, and hyphen." >&2
  exit 1
fi
if [[ -z "${KERNELBENCH_ROOT:-}" ]]; then
  echo "KERNELBENCH_ROOT must point to the official KernelBench checkout." >&2
  exit 1
fi
if [[ -z "${HARDWARE_NAME}" ]]; then
  echo "HARDWARE_NAME must name the KernelBench timings subdirectory to use." >&2
  exit 1
fi

require_command flock
require_command "${KBHARNESS_CLI}"
prepare_tool_state "${TOOL_CONFIG_ROOT}"

ARCHIVE_PROBLEM_DIR="${ARCHIVE_ROOT}/${RUN_NAME}/level_${LEVEL}/problem_${PROBLEM_ID}"
AGENT_ARTIFACT_DIR="${ARCHIVE_PROBLEM_DIR}/agent"
SOLVER_LOCK_DIR="${STATE_ROOT}/locks/solver"
PROBLEM_STATE_LOCK_DIR="${STATE_ROOT}/locks/problem_state"
GPU_LOCK_DIR="${STATE_ROOT}/locks/gpu"

mkdir -p \
  "${ARCHIVE_PROBLEM_DIR}" \
  "${AGENT_ARTIFACT_DIR}" \
  "${STATE_ROOT}" \
  "${TOOL_CONFIG_ROOT}" \
  "${SOLVER_LOCK_DIR}" \
  "${PROBLEM_STATE_LOCK_DIR}" \
  "${GPU_LOCK_DIR}"

SOLVER_LOCK_PATH="${SOLVER_LOCK_DIR}/${RUN_NAME}_level_${LEVEL}_problem_${PROBLEM_ID}.lock"
exec 9>"${SOLVER_LOCK_PATH}"
if ! flock -n 9; then
  echo "Another solver is already active for run=${RUN_NAME} level=${LEVEL} problem=${PROBLEM_ID}." >&2
  exit 1
fi

# Validate authentication before mutating workspace state.
if [[ "${TOOL}" == "codex" ]]; then
  require_command codex
  export CODEX_HOME="${CODEX_PROBLEM_HOME}"
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    :
  elif ! codex login status >/dev/null 2>&1; then
    echo "Codex needs either repo-root .codex/auth.json or OPENAI_API_KEY before launch." >&2
    echo "Preferred: CODEX_HOME=\"./.codex\" codex -c cli_auth_credentials_store=file login --device-auth" >&2
    echo "The harness copies only ./.codex/auth.json into ${CODEX_PROBLEM_HOME} on launch." >&2
    echo "If regular codex works but the harness does not, refresh repo-root ./.codex/auth.json; the harness intentionally ignores ~/.codex." >&2
    echo "Alternative: export OPENAI_API_KEY=..." >&2
    exit 1
  fi
else
  require_command claude
  export CLAUDE_CONFIG_DIR="${CLAUDE_PROBLEM_CONFIG_DIR}"
  if [[ -n "${ANTHROPIC_API_KEY:-}" || -n "${ANTHROPIC_AUTH_TOKEN:-}" || -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
    :
  elif [[ -f "${CLAUDE_PROBLEM_CONFIG_DIR}/.credentials.json" ]]; then
    :
  else
    echo "Claude Code needs either repo-root .claude/.credentials.json or exported API credentials before launch." >&2
    echo "Preferred: CLAUDE_CONFIG_DIR=\"./.claude\" claude login" >&2
    echo "The harness copies only ./.claude/.credentials.json into ${CLAUDE_PROBLEM_CONFIG_DIR} on launch." >&2
    echo "If regular claude works but the harness does not, refresh repo-root ./.claude/.credentials.json; the harness intentionally ignores ~/.claude." >&2
    echo "Alternatives: export ANTHROPIC_API_KEY=..., ANTHROPIC_AUTH_TOKEN=..., or CLAUDE_CODE_OAUTH_TOKEN=..." >&2
    exit 1
  fi
fi

PREP_OUTPUT="$({
  "${KBHARNESS_CLI}" prepare-problem-workspace \
    --run-name "${RUN_NAME}" \
    --level "${LEVEL}" \
    --problem-id "${PROBLEM_ID}" \
    --dataset-src "${DATASET_SRC}" \
    --kernelbench-root "${KERNELBENCH_ROOT}" \
    --timings-dir "${KERNELBENCH_TIMINGS_DIR}" \
    --hardware-name "${HARDWARE_NAME}" \
    --num-gpus "${NUM_GPU_SLOTS}" \
    --tool "${TOOL}" \
    --model "${MODEL}" \
    --time-budget-minutes "${TIME_BUDGET_MINUTES}" \
    --precision "${PRECISION}"
})"

WORKSPACE="$({
  PREP_OUTPUT="${PREP_OUTPUT}" "${PYTHON_BIN}" - <<'PY'
import json
import os
payload = json.loads(os.environ["PREP_OUTPUT"])
print(payload["workspace"])
PY
})"

WORKSPACE_CANDIDATE_PATH="${WORKSPACE}/candidate_model_new.py"
if [[ ! -f "${WORKSPACE_CANDIDATE_PATH}" ]]; then
  echo "Prepared workspace is missing candidate_model_new.py at ${WORKSPACE_CANDIDATE_PATH}." >&2
  exit 1
fi

INITIAL_PROMPT_PATH="${WORKSPACE}/INITIAL_PROMPT.md"
EVENTS_PATH="${AGENT_ARTIFACT_DIR}/events.jsonl"
MCP_EVENTS_PATH="${AGENT_ARTIFACT_DIR}/mcp_ir_events.jsonl"
FINAL_MESSAGE_PATH="${AGENT_ARTIFACT_DIR}/final_message.txt"
TRACE_PATH="${AGENT_ARTIFACT_DIR}/trace_ir.json"
COMPLETION_PATH="${AGENT_ARTIFACT_DIR}/completion.json"
WORKSPACE_COMPLETION_PATH="${WORKSPACE}/completion.json"
BUDGET_EXHAUSTED_MARKER_PATH="${AGENT_ARTIFACT_DIR}/budget_exhausted_goal_status.json"
RUNTIME_DIR="${STATE_ROOT}/runtime/${RUN_NAME}/level_${LEVEL}/problem_${PROBLEM_ID}"
RUNTIME_TMP_DIR="${RUNTIME_DIR}/tmp"
LANDRUN_HOME_DIR="${RUNTIME_DIR}/home"
COMMAND_SOCKET_ROOT="${STATE_ROOT}/s"
mkdir -p "${COMMAND_SOCKET_ROOT}"
COMMAND_SOCKET_DIR="$(mktemp -d -p "${COMMAND_SOCKET_ROOT}" "b.XXXXXX")"
COMMAND_SOCKET_PATH="${COMMAND_SOCKET_DIR}/c"
COMMAND_BROKER_STDOUT_PATH="${AGENT_ARTIFACT_DIR}/command_broker.stdout.txt"
COMMAND_BROKER_STDERR_PATH="${AGENT_ARTIFACT_DIR}/command_broker.stderr.txt"
RUNTIME_FINAL_MESSAGE_PATH="${RUNTIME_DIR}/final_message.txt"
TOOL_CWD="${WORKSPACE}"
COMMAND_BROKER_PID=""
rm -rf "${RUNTIME_DIR}"
mkdir -p "${RUNTIME_TMP_DIR}" "${LANDRUN_HOME_DIR}"
trap cleanup_runtime EXIT

export KBH_COMMAND_SOCKET="${COMMAND_SOCKET_PATH}"

start_command_broker() {
  "${PYTHON_BIN}" -m kernel_bench_experiment_agents.command_broker \
    --socket "${COMMAND_SOCKET_PATH}" \
    --workspace "${WORKSPACE}" \
    --run-name "${RUN_NAME}" \
    --level "${LEVEL}" \
    --problem-id "${PROBLEM_ID}" \
    --dataset-src "${DATASET_SRC}" \
    --kernelbench-root "${KERNELBENCH_ROOT}" \
    --num-gpu-slots "${NUM_GPU_SLOTS}" \
    --precision "${PRECISION}" \
    --tool "${TOOL}" \
    --events-path "${MCP_EVENTS_PATH}" \
    >"${COMMAND_BROKER_STDOUT_PATH}" \
    2>"${COMMAND_BROKER_STDERR_PATH}" &
  COMMAND_BROKER_PID=$!
}

wait_for_command_broker() {
  local attempts=200
  while (( attempts > 0 )); do
    if [[ -S "${COMMAND_SOCKET_PATH}" ]]; then
      return 0
    fi
    if ! kill -0 "${COMMAND_BROKER_PID}" 2>/dev/null; then
      echo "Launcher command broker exited before creating ${COMMAND_SOCKET_PATH}." >&2
      [[ -s "${COMMAND_BROKER_STDERR_PATH}" ]] && cat "${COMMAND_BROKER_STDERR_PATH}" >&2
      return 1
    fi
    attempts=$((attempts - 1))
    sleep 0.1
  done
  echo "Timed out waiting for launcher command broker socket at ${COMMAND_SOCKET_PATH}." >&2
  [[ -s "${COMMAND_BROKER_STDERR_PATH}" ]] && cat "${COMMAND_BROKER_STDERR_PATH}" >&2
  return 1
}

readarray -t PYTHON_ROOTS < <(
  "${PYTHON_BIN}" - <<'PY'
import os
import sys

for value in (sys.prefix, sys.base_prefix):
    print(os.path.abspath(value))
PY
)
PYTHON_ENV_ROOT="${PYTHON_ROOTS[0]}"
PYTHON_BASE_ROOT="${PYTHON_ROOTS[1]:-${PYTHON_ROOTS[0]}}"
TOOL_BIN_PATH="$(command -v "${TOOL}")"
TOOL_BIN_DIR="$(dirname "${TOOL_BIN_PATH}")"
TOOL_REAL_PATH="$(readlink -f "${TOOL_BIN_PATH}")"
TOOL_REAL_DIR="$(dirname "${TOOL_REAL_PATH}")"
TOOL_REAL_PARENT="$(dirname "${TOOL_REAL_DIR}")"
TOOL_REAL_GRANDPARENT="$(dirname "${TOOL_REAL_PARENT}")"
if [[ "${TOOL}" == "codex" ]]; then
  TOOL_RUNTIME_HOME="${CODEX_HOME}"
else
  TOOL_RUNTIME_HOME="${CLAUDE_CONFIG_DIR}"
fi

LANDRUN_ARGS=(
  --best-effort
  --unrestricted-network
  --ldd
  --add-exec
  --rwx /dev
  --ro "${WORKSPACE}"
  --rw "${WORKSPACE_CANDIDATE_PATH}"
  --rw "${RUNTIME_DIR}"
  --rw "${LANDRUN_HOME_DIR}"
  --rw "${COMMAND_SOCKET_DIR}"
  --rw "${TOOL_RUNTIME_HOME}"
)
for path in \
  /bin \
  /usr \
  /lib \
  /lib64 \
  /etc \
  /proc \
  /sys \
  "${PYTHON_ENV_ROOT}" \
  "${PYTHON_BASE_ROOT}" \
  "${TOOL_BIN_DIR}" \
  "${TOOL_REAL_DIR}" \
  "${TOOL_REAL_PARENT}" \
  "${TOOL_REAL_GRANDPARENT}"
do
  if [[ -e "${path}" ]]; then
    LANDRUN_ARGS+=(--rox "${path}")
  fi
done
for path in /run/systemd/resolve; do
  if [[ -e "${path}" ]]; then
    LANDRUN_ARGS+=(--ro "${path}")
  fi
done

append_landrun_env_if_set() {
  local name="$1"
  if [[ -n "${!name:-}" ]]; then
    LANDRUN_ENV_ARGS+=(--env "${name}")
  fi
}

LANDRUN_ENV_ARGS=(
  --env PATH
  --env HOME="${LANDRUN_HOME_DIR}"
  --env XDG_RUNTIME_DIR="${LANDRUN_HOME_DIR}"
  --env TMPDIR="${RUNTIME_TMP_DIR}"
  --env TMP="${RUNTIME_TMP_DIR}"
  --env TEMP="${RUNTIME_TMP_DIR}"
  --env PYTHON="${PYTHON_BIN}"
  --env KBH_COMMAND_SOCKET="${COMMAND_SOCKET_PATH}"
  --env LANG="${LANG:-C.UTF-8}"
)
append_landrun_env_if_set LD_LIBRARY_PATH
if [[ "${TOOL}" == "codex" ]]; then
  LANDRUN_ENV_ARGS+=(--env CODEX_HOME="${CODEX_HOME}")
  append_landrun_env_if_set OPENAI_API_KEY
  append_landrun_env_if_set OPENAI_BASE_URL
  append_landrun_env_if_set OPENAI_ORGANIZATION
  append_landrun_env_if_set OPENAI_PROJECT
else
  LANDRUN_ENV_ARGS+=(--env CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR}")
  append_landrun_env_if_set ANTHROPIC_API_KEY
  append_landrun_env_if_set ANTHROPIC_AUTH_TOKEN
  append_landrun_env_if_set ANTHROPIC_BASE_URL
  append_landrun_env_if_set CLAUDE_CODE_OAUTH_TOKEN
  append_landrun_env_if_set CLAUDE_CODE_MAX_CONTEXT_TOKENS
  append_landrun_env_if_set CLAUDE_CODE_AUTO_COMPACT_WINDOW
  append_landrun_env_if_set CLAUDE_AUTOCOMPACT_PCT_OVERRIDE
fi

if [[ "${TOOL}" == "codex" ]]; then
  echo "Launching Codex from ${TOOL_CWD} under Landrun with CODEX_HOME=${CODEX_HOME} and brokered harness commands" >&2
else
  echo "Launching Claude Code from ${TOOL_CWD} under Landrun with CLAUDE_CONFIG_DIR=${CLAUDE_CONFIG_DIR} and brokered harness commands" >&2
fi

rm -f "${EVENTS_PATH}" "${MCP_EVENTS_PATH}" "${FINAL_MESSAGE_PATH}" "${TRACE_PATH}" "${COMPLETION_PATH}" "${WORKSPACE_COMPLETION_PATH}" "${BUDGET_EXHAUSTED_MARKER_PATH}" "${COMMAND_BROKER_STDOUT_PATH}" "${COMMAND_BROKER_STDERR_PATH}" "${RUNTIME_FINAL_MESSAGE_PATH}"
: > "${MCP_EVENTS_PATH}"
start_command_broker
wait_for_command_broker

refresh_goal_status() {
  "${KBHARNESS_CLI}" goal-status \
    --run-name "${RUN_NAME}" \
    --level "${LEVEL}" \
    --problem-id "${PROBLEM_ID}" \
    --workspace "${WORKSPACE}" >/dev/null 2>&1
}

mark_budget_exhausted_if_needed() {
  local status_path="${WORKSPACE}/goal_status.json"
  local exhausted=""

  refresh_goal_status || return 1
  exhausted="$({
    STATUS_PATH="${status_path}" "${PYTHON_BIN}" - <<'PY'
import json
import os
payload = json.loads(open(os.environ["STATUS_PATH"], "r", encoding="utf-8").read())
remaining = payload.get("remaining_minutes")
print("false" if remaining is None else ("true" if float(remaining) <= 0 else "false"))
PY
  })"
  if [[ "${exhausted}" == "true" ]]; then
    cp -f "${status_path}" "${BUDGET_EXHAUSTED_MARKER_PATH}"
    return 0
  fi
  return 1
}

watch_budget_limit() {
  local remaining=""
  while kill -0 "${AGENT_PIPE_PID}" 2>/dev/null; do
    if [[ -f "${COMPLETION_PATH}" ]]; then
      return 0
    fi
    if mark_budget_exhausted_if_needed; then
      remaining="$({
        STATUS_PATH="${BUDGET_EXHAUSTED_MARKER_PATH}" "${PYTHON_BIN}" - <<'PY'
import json
import os
payload = json.loads(open(os.environ["STATUS_PATH"], "r", encoding="utf-8").read())
remaining = payload.get("remaining_minutes")
print("" if remaining is None else remaining)
PY
      })"
      echo "Budget exhausted for run=${RUN_NAME} level=${LEVEL} problem=${PROBLEM_ID} (remaining=${remaining}); stopping ${TOOL}." >&2
      terminate_agent_pipeline "${AGENT_PIPE_PID}"
      return 0
    fi
    sleep "${BUDGET_POLL_SECONDS}"
  done
}

set +e
if [[ "${TOOL}" == "codex" ]]; then
  CODEX_ARGS=(
    -a never
    --disable shell_tool
    exec
    --sandbox "workspace-write"
    --cd "${TOOL_CWD}"
    --skip-git-repo-check
    --model "${MODEL}"
    --json
  )
  (
    cd "${TOOL_CWD}" && \
      "${LANDRUN_BIN}" "${LANDRUN_ARGS[@]}" "${LANDRUN_ENV_ARGS[@]}" "${TOOL_BIN_PATH}" "${CODEX_ARGS[@]}" \
        --output-last-message "${RUNTIME_FINAL_MESSAGE_PATH}" \
        "$(cat "${INITIAL_PROMPT_PATH}")" | tee "${EVENTS_PATH}"
  ) &
else
  readarray -t CLAUDE_TOOL_FLAGS < <(
    "${PYTHON_BIN}" - <<'PY'
from kernel_bench_experiment_agents.runtime.policy import (
    CLAUDE_ALLOWED_TOOL_PATTERNS,
    CLAUDE_BUILTIN_TOOLS,
    CLAUDE_DENIED_TOOLS,
)

print(",".join(CLAUDE_BUILTIN_TOOLS))
print(",".join(CLAUDE_ALLOWED_TOOL_PATTERNS))
print(",".join(CLAUDE_DENIED_TOOLS))
PY
  )
  CLAUDE_ARGS=(
    -p
    --verbose
    --output-format stream-json
    --no-session-persistence
    --permission-mode dontAsk
    --setting-sources user
    --model "${MODEL}"
    --tools "${CLAUDE_TOOL_FLAGS[0]}"
    --allowed-tools "${CLAUDE_TOOL_FLAGS[1]}"
    --disallowed-tools "${CLAUDE_TOOL_FLAGS[2]}"
  )
  (
    cd "${TOOL_CWD}" && \
      "${LANDRUN_BIN}" "${LANDRUN_ARGS[@]}" "${LANDRUN_ENV_ARGS[@]}" "${TOOL_BIN_PATH}" "${CLAUDE_ARGS[@]}" \
        < "${INITIAL_PROMPT_PATH}" | tee "${EVENTS_PATH}"
  ) &
fi
AGENT_PIPE_PID=$!
watch_budget_limit &
BUDGET_WATCH_PID=$!
wait "${AGENT_PIPE_PID}"
AGENT_EXIT=$?
kill "${BUDGET_WATCH_PID}" 2>/dev/null || true
wait "${BUDGET_WATCH_PID}" 2>/dev/null || true
set -e

if [[ -f "${RUNTIME_FINAL_MESSAGE_PATH}" ]]; then
  cp -f "${RUNTIME_FINAL_MESSAGE_PATH}" "${FINAL_MESSAGE_PATH}"
fi
cleanup_runtime

if [[ ! -f "${COMPLETION_PATH}" ]]; then
  mark_budget_exhausted_if_needed >/dev/null 2>&1 || true
  if [[ -f "${BUDGET_EXHAUSTED_MARKER_PATH}" ]]; then
    "${KBHARNESS_CLI}" record-launcher-completion \
      --run-name "${RUN_NAME}" \
      --level "${LEVEL}" \
      --problem-id "${PROBLEM_ID}" \
      --workspace "${WORKSPACE}" \
      --state budget_exhausted \
      --summary "launcher stopped ${TOOL} after the corrected remaining budget reached zero without a solver-written completion" \
      --allow-overwrite >/dev/null
  else
    "${KBHARNESS_CLI}" record-launcher-completion \
      --run-name "${RUN_NAME}" \
      --level "${LEVEL}" \
      --problem-id "${PROBLEM_ID}" \
      --workspace "${WORKSPACE}" \
      --state failed_to_generate \
      --summary "${TOOL} exited with code ${AGENT_EXIT} without writing completion.json" \
      --allow-overwrite >/dev/null
  fi
fi

if ! "${KBHARNESS_CLI}" materialize-agent-trace \
  --tool "${TOOL}" \
  --events-path "${EVENTS_PATH}" \
  --mcp-events-path "${MCP_EVENTS_PATH}" \
  --completion-path "${COMPLETION_PATH}" \
  --final-message-path "${FINAL_MESSAGE_PATH}" \
  --output-path "${TRACE_PATH}" \
  --workspace "${WORKSPACE}" >/dev/null; then
  echo "warning: failed to materialize normalized ${TOOL} trace at ${TRACE_PATH}" >&2
fi

readarray -t COMPLETION_STATE < <(
  COMPLETION_PATH="${COMPLETION_PATH}" "${PYTHON_BIN}" - <<'PY'
import json
import os
payload = json.loads(open(os.environ["COMPLETION_PATH"], "r", encoding="utf-8").read())
print(payload.get("terminal_state", ""))
print("true" if payload.get("success") else "false")
print(payload.get("measured_outcome", ""))
PY
)
TERMINAL_STATE="${COMPLETION_STATE[0]:-}"
SUCCESS="${COMPLETION_STATE[1]:-false}"
MEASURED_OUTCOME="${COMPLETION_STATE[2]:-}"

echo "Completion state: ${TERMINAL_STATE} (measured_outcome=${MEASURED_OUTCOME}, success=${SUCCESS})" >&2
case "${TERMINAL_STATE}" in
  harness_failure|failed_to_generate)
    exit 1
    ;;
  done|budget_exhausted)
    exit 0
    ;;
  *)
    echo "Unexpected terminal state: ${TERMINAL_STATE}" >&2
    exit 1
    ;;
esac
