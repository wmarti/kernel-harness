#!/usr/bin/env bash
# Run multiple problems by repeatedly invoking run_agent_problem.sh.
#
# Run this script from the harness repo root.
#
# Select problems with either:
#   PROBLEM_IDS=1,4,9
# or:
#   START_PROBLEM_ID=1 END_PROBLEM_ID=10
set -euo pipefail

if [[ ! -f "./pyproject.toml" || ! -d "./src/kernel_bench_experiment_agents" ]]; then
  echo "Run scripts/run_agent_range.sh from the harness repo root." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

DATA_ROOT="${DATA_ROOT:-.}"
mkdir -p "${DATA_ROOT}"
DATA_ROOT="$(cd "${DATA_ROOT}" && pwd)"
export DATA_ROOT
KERNELBENCH_ROOT="${KERNELBENCH_ROOT:-${REPO_ROOT}/third_party/KernelBench}"
export KERNELBENCH_ROOT

TOOL="${TOOL:-codex}"
case "${TOOL}" in
  codex|claude) ;;
  *)
    echo "Unsupported TOOL=${TOOL}. Expected codex or claude." >&2
    exit 1
    ;;
esac

RUN_NAME="${RUN_NAME:-kernelbench-${TOOL}-$(date -u +%Y%m%dT%H%M%SZ)}"
LEVEL="${LEVEL:-1}"
MAX_PARALLEL_SOLVERS="${MAX_PARALLEL_SOLVERS:-1}"
RUN_STARTED_AT="$(date '+%Y-%m-%dT%H:%M:%S%z')"
RUN_STARTED_EPOCH="$(date +%s)"

if [[ ! "${MAX_PARALLEL_SOLVERS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "MAX_PARALLEL_SOLVERS must be a positive integer." >&2
  exit 1
fi

if [[ -n "${PROBLEM_IDS:-}" ]]; then
  IFS=',' read -r -a PROBLEM_ID_LIST <<< "${PROBLEM_IDS}"
elif [[ -n "${START_PROBLEM_ID:-}" && -n "${END_PROBLEM_ID:-}" ]]; then
  PROBLEM_ID_LIST=()
  for (( pid=START_PROBLEM_ID; pid<=END_PROBLEM_ID; pid++ )); do
    PROBLEM_ID_LIST+=("${pid}")
  done
else
  echo "Set either PROBLEM_IDS=1,2,3 or START_PROBLEM_ID and END_PROBLEM_ID." >&2
  exit 1
fi

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

report_elapsed_time() {
  local exit_code=$?
  local finished_at elapsed hours minutes seconds

  finished_at="$(date '+%Y-%m-%dT%H:%M:%S%z')"
  elapsed=$(( $(date +%s) - RUN_STARTED_EPOCH ))
  hours=$(( elapsed / 3600 ))
  minutes=$(( (elapsed % 3600) / 60 ))
  seconds=$(( elapsed % 60 ))

  echo "Range run ${RUN_NAME} finished at ${finished_at} with exit code ${exit_code} after ${hours}h ${minutes}m ${seconds}s" >&2
}

trap report_elapsed_time EXIT

echo "Range run ${RUN_NAME} started at ${RUN_STARTED_AT}" >&2

export DATA_ROOT TOOL RUN_NAME LEVEL
export DATASET_SRC="${DATASET_SRC:-local}"
export MODEL="${MODEL:-}"
export TIME_BUDGET_MINUTES="${TIME_BUDGET_MINUTES:-180}"
export HARDWARE_NAME="${HARDWARE_NAME:-}"
export KERNELBENCH_ROOT="${KERNELBENCH_ROOT:-${REPO_ROOT}/third_party/KernelBench}"
export KERNELBENCH_TIMINGS_DIR="${KERNELBENCH_TIMINGS_DIR:-}"
export PRECISION="${PRECISION:-bf16}"

action_run_one() {
  local pid="$1"
  PROBLEM_ID="${pid}" ./scripts/run_agent_problem.sh
}

declare -A JOB_TO_PROBLEM=()
declare -a FAILED_PROBLEMS=()
active_jobs=0

reap_one_job() {
  local finished_pid=""
  local status=0
  local problem="unknown"

  set +e
  wait -n -p finished_pid
  status=$?
  set -e

  if [[ -n "${finished_pid}" && -n "${JOB_TO_PROBLEM[${finished_pid}]:-}" ]]; then
    problem="${JOB_TO_PROBLEM[${finished_pid}]}"
    unset "JOB_TO_PROBLEM[$finished_pid]"
  fi

  active_jobs=$((active_jobs - 1))
  if (( status != 0 )); then
    FAILED_PROBLEMS+=("problem ${problem} (exit ${status})")
  fi
}

for raw_pid in "${PROBLEM_ID_LIST[@]}"; do
  pid="$(trim "${raw_pid}")"
  [[ -n "${pid}" ]] || continue

  while (( active_jobs >= MAX_PARALLEL_SOLVERS )); do
    reap_one_job
  done

  action_run_one "${pid}" &
  job_pid=$!
  JOB_TO_PROBLEM["${job_pid}"]="${pid}"
  active_jobs=$((active_jobs + 1))
done

while (( active_jobs > 0 )); do
  reap_one_job
done

if (( ${#FAILED_PROBLEMS[@]} > 0 )); then
  echo "Range run ${RUN_NAME} completed with failing problems:" >&2
  for failure in "${FAILED_PROBLEMS[@]}"; do
    echo "  - ${failure}" >&2
  done
  exit 1
fi
