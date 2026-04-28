#!/usr/bin/env bash
# Delete one archived run plus its disposable state.
#
# Usage:
#   ./scripts/clear_run.sh <run_name>
# or:
#   RUN_NAME=<run_name> ./scripts/clear_run.sh
#
# Run this script from the harness repo root. DATA_ROOT controls which archive/state tree is cleared.
set -euo pipefail

if [[ ! -f "./pyproject.toml" || ! -d "./src/kernel_bench_experiment_agents" ]]; then
  echo "Run scripts/clear_run.sh from the harness repo root." >&2
  exit 1
fi

DATA_ROOT="${DATA_ROOT:-.}"
mkdir -p "${DATA_ROOT}"
DATA_ROOT="$(cd "${DATA_ROOT}" && pwd)"
STATE_ROOT="${DATA_ROOT}/state"
ARCHIVE_ROOT="${DATA_ROOT}/archive"

RUN_NAME="${RUN_NAME:-${1:-}}"

if [[ -z "${RUN_NAME}" ]]; then
  echo "Set RUN_NAME or pass it as the first argument." >&2
  exit 1
fi
if [[ ! "${RUN_NAME}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "RUN_NAME may contain only ASCII letters, digits, dot, underscore, and hyphen." >&2
  exit 1
fi

rm -rf \
  "${ARCHIVE_ROOT}/${RUN_NAME}" \
  "${STATE_ROOT}/build/${RUN_NAME}" \
  "${STATE_ROOT}/workspaces/${RUN_NAME}" \
  "${STATE_ROOT}/tool_state/${RUN_NAME}" \
  "${STATE_ROOT}/runtime/${RUN_NAME}"

rm -f \
  "${STATE_ROOT}/locks/solver/${RUN_NAME}"_level_*_problem_*.lock \
  "${STATE_ROOT}/locks/problem_state/${RUN_NAME}"_level_*_problem_*.lock \
  "${STATE_ROOT}/locks/live_gpu_wait/${RUN_NAME}"_level_*_problem_*.json

echo "Cleared run state for ${RUN_NAME}"
