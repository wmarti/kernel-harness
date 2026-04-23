#!/usr/bin/env bash
#SBATCH --job-name=kernelbench-harness
#SBATCH --gres=gpu:1
#SBATCH --time=13:00:00
#SBATCH --output=slurm-out/%x-%j.out
#SBATCH --error=slurm-err/%x-%j.err
# Thin Slurm wrapper around run_agent_range.sh.
#
# Submit from the harness repo root after `./kb setup` has recorded the
# uv-managed harness Python in `./.kb-python`, or set `PYTHON=/path/to/python`
# for the batch environment. The script exports the repo-local
# `scripts/kbharness` wrapper itself.
#
# Example:
#   sbatch --export=ALL,TOOL=codex,RUN_NAME=kernelbench-codex,LEVEL=1,START_PROBLEM_ID=1,END_PROBLEM_ID=10,MODEL=gpt-5.4,TIME_BUDGET_MINUTES=180,PRECISION=bf16,KERNELBENCH_ROOT=/path/to/KernelBench,HARDWARE_NAME=H100 ./scripts/run_agent_problem.slurm.sh
#
# The higher-level `./kb submit ...` wrapper is the normal entrypoint. It uses
# `ybatch` automatically when that site-local command exists, otherwise `sbatch`.
set -euo pipefail

if [[ ! -f "./pyproject.toml" || ! -d "./src/kernel_bench_experiment_agents" ]]; then
  echo "Submit scripts/run_agent_problem.slurm.sh from the harness repo root." >&2
  exit 1
fi

REPO_ROOT="$(pwd)"
export PATH="${REPO_ROOT}/scripts:${PATH}"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

# shellcheck source=./scripts/kb_python.sh
source "${REPO_ROOT}/scripts/kb_python.sh"

DATA_ROOT="${DATA_ROOT:-.}"
mkdir -p "${DATA_ROOT}"
DATA_ROOT="$(cd "${DATA_ROOT}" && pwd)"
export DATA_ROOT

module load cuda || true

TOOL="${TOOL:-claude}"
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

RUN_NAME="${RUN_NAME:-$(default_run_name "${TOOL}")}"
LEVEL="${LEVEL:-1}"
PROBLEM_IDS="${PROBLEM_IDS:-}"
START_PROBLEM_ID="${START_PROBLEM_ID:-1}"
END_PROBLEM_ID="${END_PROBLEM_ID:-100}"
MAX_PARALLEL_SOLVERS="${MAX_PARALLEL_SOLVERS:-1}"
DATASET_SRC="${DATASET_SRC:-local}"
MODEL="${MODEL:-${DEFAULT_MODEL}}"
TIME_BUDGET_MINUTES="${TIME_BUDGET_MINUTES:-180}"
HARDWARE_NAME="${HARDWARE_NAME:-}"
KERNELBENCH_ROOT="${KERNELBENCH_ROOT:-}"
KERNELBENCH_TIMINGS_DIR="${KERNELBENCH_TIMINGS_DIR:-}"
PRECISION="${PRECISION:-bf16}"

if [[ -z "${HARDWARE_NAME}" ]]; then
  echo "HARDWARE_NAME must be set for scripts/run_agent_problem.slurm.sh." >&2
  exit 1
fi

DATA_ROOT="${DATA_ROOT}" \
TOOL="${TOOL}" \
RUN_NAME="${RUN_NAME}" \
LEVEL="${LEVEL}" \
PROBLEM_IDS="${PROBLEM_IDS}" \
START_PROBLEM_ID="${START_PROBLEM_ID}" \
END_PROBLEM_ID="${END_PROBLEM_ID}" \
MAX_PARALLEL_SOLVERS="${MAX_PARALLEL_SOLVERS}" \
DATASET_SRC="${DATASET_SRC}" \
MODEL="${MODEL}" \
TIME_BUDGET_MINUTES="${TIME_BUDGET_MINUTES}" \
HARDWARE_NAME="${HARDWARE_NAME}" \
KERNELBENCH_ROOT="${KERNELBENCH_ROOT}" \
KERNELBENCH_TIMINGS_DIR="${KERNELBENCH_TIMINGS_DIR}" \
PRECISION="${PRECISION}" \
./scripts/run_agent_range.sh
