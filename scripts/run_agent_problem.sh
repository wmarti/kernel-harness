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
#   RUN_NAME=kernelbench-codex-h100-v3
#   LEVEL=1
#   PROBLEM_ID=1
#   MODEL=gpt-5.4|opus-4.6
#   TIME_BUDGET_MINUTES=180
#   PRECISION=bf16
#   KERNELBENCH_TIMINGS_DIR=/path/to/KernelBench/results/timing/<hardware>  # optional override
set -euo pipefail

if [[ ! -f "./pyproject.toml" || ! -d "./src/kernel_bench_experiment_agents" ]]; then
  echo "Run scripts/run_agent_problem.sh from the harness repo root." >&2
  exit 1
fi

DATA_ROOT="${DATA_ROOT:-.}"
mkdir -p "${DATA_ROOT}"
DATA_ROOT="$(cd "${DATA_ROOT}" && pwd)"
export DATA_ROOT

STATE_ROOT="${DATA_ROOT}/state"
ARCHIVE_ROOT="${DATA_ROOT}/archive"
TOOL_CONFIG_ROOT="${STATE_ROOT}/config"
CODEX_SHARED_HOME="${TOOL_CONFIG_ROOT}/codex"
CLAUDE_SHARED_CONFIG_DIR="${TOOL_CONFIG_ROOT}/claude"
KBHARNESS_CLI="kbharness"

prepare_shared_tool_state() {
  python - <<'PY'
from pathlib import Path
from kernel_bench_experiment_agents.runtime_policy import write_shared_tool_state
from kernel_bench_experiment_agents.project import state_dir

write_shared_tool_state(state_dir() / "config")
PY
}

# Stop the launched agent process tree when the budget watcher fires.
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

cleanup_mcp_sidecar() {
  if [[ -n "${MCP_SIDECAR_PID:-}" ]]; then
    kill -TERM "${MCP_SIDECAR_PID}" 2>/dev/null || true
    wait "${MCP_SIDECAR_PID}" 2>/dev/null || true
    MCP_SIDECAR_PID=""
  fi
  if [[ -n "${MCP_SOCKET_DIR:-}" && -d "${MCP_SOCKET_DIR}" ]]; then
    rm -rf "${MCP_SOCKET_DIR}"
    MCP_SOCKET_DIR=""
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
  DEFAULT_MODEL="opus-4.6"
fi

RUN_NAME="${RUN_NAME:-kernelbench-${TOOL}-h100-v3}"
LEVEL="${LEVEL:-1}"
PROBLEM_ID="${PROBLEM_ID:-1}"
DATASET_SRC="${DATASET_SRC:-local}"
MODEL="${MODEL:-${DEFAULT_MODEL}}"
TIME_BUDGET_MINUTES="${TIME_BUDGET_MINUTES:-180}"
HARDWARE_NAME="${HARDWARE_NAME:-}"
KERNELBENCH_TIMINGS_DIR="${KERNELBENCH_TIMINGS_DIR:-}"
PRECISION="${PRECISION:-bf16}"
NUM_GPU_SLOTS="$(visible_gpu_slot_count)"
CODEX_SANDBOX_MODE="read-only"
BUDGET_POLL_SECONDS=30

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

require_command python
require_command flock
require_command "${KBHARNESS_CLI}"
if [[ "${SHARED_TOOL_STATE_PREPARED:-0}" != "1" ]]; then
  prepare_shared_tool_state
fi
export SHARED_TOOL_STATE_PREPARED=1

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
  export CODEX_HOME="${CODEX_SHARED_HOME}"
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    :
  elif ! codex login status >/dev/null 2>&1; then
    echo "Codex needs either repo-root .codex login state or OPENAI_API_KEY before launch." >&2
    echo "Preferred: CODEX_HOME=\"./.codex\" codex -c cli_auth_credentials_store=file login --device-auth" >&2
    echo "The harness copies ./.codex/auth.json into ${CODEX_SHARED_HOME} on launch." >&2
    echo "Alternative: export OPENAI_API_KEY=..." >&2
    exit 1
  fi
else
  require_command claude
  export CLAUDE_CONFIG_DIR="${CLAUDE_SHARED_CONFIG_DIR}"
  if [[ -n "${ANTHROPIC_API_KEY:-}" || -n "${ANTHROPIC_AUTH_TOKEN:-}" || -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
    :
  elif [[ -f "${CLAUDE_SHARED_CONFIG_DIR}/.credentials.json" ]]; then
    :
  else
    echo "Claude Code needs either repo-root .claude login state or exported API credentials before launch." >&2
    echo "Preferred: CLAUDE_CONFIG_DIR=\"./.claude\" claude login" >&2
    echo "The harness copies ./.claude/.credentials.json into ${CLAUDE_SHARED_CONFIG_DIR} on launch." >&2
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
  PREP_OUTPUT="${PREP_OUTPUT}" python - <<'PY'
import json
import os
payload = json.loads(os.environ["PREP_OUTPUT"])
print(payload["workspace"])
PY
})"

INITIAL_PROMPT_PATH="${WORKSPACE}/INITIAL_PROMPT.md"
EVENTS_PATH="${AGENT_ARTIFACT_DIR}/events.jsonl"
MCP_EVENTS_PATH="${AGENT_ARTIFACT_DIR}/mcp_ir_events.jsonl"
FINAL_MESSAGE_PATH="${AGENT_ARTIFACT_DIR}/final_message.txt"
TRACE_PATH="${AGENT_ARTIFACT_DIR}/trace_ir.json"
COMPLETION_PATH="${AGENT_ARTIFACT_DIR}/completion.json"
WORKSPACE_COMPLETION_PATH="${WORKSPACE}/completion.json"
BUDGET_EXHAUSTED_MARKER_PATH="${AGENT_ARTIFACT_DIR}/budget_exhausted_goal_status.json"
TOOL_CWD="${STATE_ROOT}/cwd/${TOOL}/${RUN_NAME}/level_${LEVEL}/problem_${PROBLEM_ID}"
rm -rf "${TOOL_CWD}"
mkdir -p "${TOOL_CWD}"
SCRATCH_FINAL_MESSAGE_PATH="${TOOL_CWD}/final_message.txt"
MCP_SOCKET_DIR="$(mktemp -d -p /tmp "kbh-mcp.${TOOL}.XXXXXX")"
MCP_SOCKET_PATH="${MCP_SOCKET_DIR}/server.sock"
MCP_SIDECAR_STDOUT_PATH="${AGENT_ARTIFACT_DIR}/mcp_sidecar.stdout.txt"
MCP_SIDECAR_STDERR_PATH="${AGENT_ARTIFACT_DIR}/mcp_sidecar.stderr.txt"
MCP_SIDECAR_PID=""
trap cleanup_mcp_sidecar EXIT

start_mcp_sidecar() {
  KBH_WORKSPACE="${WORKSPACE}" \
  KBH_CLIENT_TOOL="${TOOL}" \
  KBH_MCP_EVENTS_PATH="${MCP_EVENTS_PATH}" \
  python -m kernel_bench_experiment_agents.mcp_sidecar \
    --socket "${MCP_SOCKET_PATH}" \
    >"${MCP_SIDECAR_STDOUT_PATH}" \
    2>"${MCP_SIDECAR_STDERR_PATH}" &
  MCP_SIDECAR_PID=$!
}

wait_for_mcp_sidecar() {
  local attempts=200
  while (( attempts > 0 )); do
    if [[ -S "${MCP_SOCKET_PATH}" ]]; then
      return 0
    fi
    if ! kill -0 "${MCP_SIDECAR_PID}" 2>/dev/null; then
      echo "Launcher MCP sidecar exited before creating ${MCP_SOCKET_PATH}." >&2
      [[ -s "${MCP_SIDECAR_STDERR_PATH}" ]] && cat "${MCP_SIDECAR_STDERR_PATH}" >&2
      return 1
    fi
    attempts=$((attempts - 1))
    sleep 0.1
  done
  echo "Timed out waiting for launcher MCP sidecar socket at ${MCP_SOCKET_PATH}." >&2
  [[ -s "${MCP_SIDECAR_STDERR_PATH}" ]] && cat "${MCP_SIDECAR_STDERR_PATH}" >&2
  return 1
}

if [[ "${TOOL}" == "codex" ]]; then
  echo "Launching Codex from ${TOOL_CWD} with shared CODEX_HOME=${CODEX_HOME} and launcher-owned MCP sidecar access" >&2
else
  echo "Launching Claude Code from ${TOOL_CWD} with shared CLAUDE_CONFIG_DIR=${CLAUDE_CONFIG_DIR} and launcher-owned MCP sidecar access" >&2
fi

rm -f "${EVENTS_PATH}" "${MCP_EVENTS_PATH}" "${FINAL_MESSAGE_PATH}" "${TRACE_PATH}" "${COMPLETION_PATH}" "${WORKSPACE_COMPLETION_PATH}" "${BUDGET_EXHAUSTED_MARKER_PATH}" "${MCP_SIDECAR_STDOUT_PATH}" "${MCP_SIDECAR_STDERR_PATH}" "${SCRATCH_FINAL_MESSAGE_PATH}"
start_mcp_sidecar
wait_for_mcp_sidecar

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
    STATUS_PATH="${status_path}" python - <<'PY'
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
        STATUS_PATH="${BUDGET_EXHAUSTED_MARKER_PATH}" python - <<'PY'
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
    --sandbox "${CODEX_SANDBOX_MODE}"
    --cd "${TOOL_CWD}"
    --skip-git-repo-check
    --model "${MODEL}"
    --json
  )
  (
    env -u DATA_ROOT -u KBH_WORKSPACE -u KBH_CLIENT_TOOL -u KBH_MCP_EVENTS_PATH \
      KBH_MCP_SOCKET="${MCP_SOCKET_PATH}" \
      codex "${CODEX_ARGS[@]}" \
      --output-last-message "${SCRATCH_FINAL_MESSAGE_PATH}" \
      "$(cat "${INITIAL_PROMPT_PATH}")" | tee "${EVENTS_PATH}"
  ) &
else
  CLAUDE_ARGS=(
    -p
    --verbose
    --output-format stream-json
    --no-session-persistence
    --setting-sources user
    --model "${MODEL}"
  )
  (
    cd "${TOOL_CWD}" && env -u DATA_ROOT -u KBH_WORKSPACE -u KBH_CLIENT_TOOL -u KBH_MCP_EVENTS_PATH \
      KBH_MCP_SOCKET="${MCP_SOCKET_PATH}" \
      claude "${CLAUDE_ARGS[@]}" \
      "$(cat "${INITIAL_PROMPT_PATH}")" | tee "${EVENTS_PATH}"
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

if [[ -f "${SCRATCH_FINAL_MESSAGE_PATH}" ]]; then
  cp -f "${SCRATCH_FINAL_MESSAGE_PATH}" "${FINAL_MESSAGE_PATH}"
fi
cleanup_mcp_sidecar

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
  COMPLETION_PATH="${COMPLETION_PATH}" python - <<'PY'
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
