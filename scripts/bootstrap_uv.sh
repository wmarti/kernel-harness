#!/usr/bin/env bash
# Backwards-compatible shim for the repo-local kb helper.
set -euo pipefail

if [[ ! -f "./pyproject.toml" || ! -d "./src/kernel_bench_experiment_agents" ]]; then
  echo "Run scripts/bootstrap_uv.sh from the harness repo root." >&2
  exit 1
fi

GPU_FLAG=()
if [[ "${INSTALL_KERNELBENCH_GPU_EXTRAS:-0}" == "1" ]]; then
  GPU_FLAG+=(--gpu-extras)
fi

PYTHON_FLAG=()
if [[ -n "${PYTHON_VERSION:-}" ]]; then
  PYTHON_FLAG+=(--python "${PYTHON_VERSION}")
fi

exec ./kb setup "${PYTHON_FLAG[@]}" "${GPU_FLAG[@]}" "$@"
