#!/usr/bin/env bash
# Create or refresh the local uv environment and install both KernelBench and this harness into it.
set -euo pipefail

if [[ ! -f "./pyproject.toml" || ! -d "./src/kernel_bench_experiment_agents" ]]; then
  echo "Run scripts/bootstrap_uv.sh from the harness repo root." >&2
  exit 1
fi

PYTHON_VERSION="${PYTHON_VERSION:-3.10}"
VENV_DIR="${VENV_DIR:-.venv}"
KERNELBENCH_DIR="third_party/KernelBench"
INSTALL_GPU_EXTRAS="${INSTALL_KERNELBENCH_GPU_EXTRAS:-0}"

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi

  echo "uv is required but was not found on PATH." >&2
  echo "Install it first, then rerun this bootstrap." >&2
  echo "Docs: https://docs.astral.sh/uv/getting-started/installation/" >&2
  echo "macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  echo "macOS/Linux without curl: wget -qO- https://astral.sh/uv/install.sh | sh" >&2
  exit 1
}

ensure_uv

git submodule update --init --recursive "${KERNELBENCH_DIR}"

uv venv --python "${PYTHON_VERSION}" "${VENV_DIR}"
export PATH="$(cd "${VENV_DIR}/bin" && pwd):${PATH}"

if [[ "${INSTALL_GPU_EXTRAS}" == "1" ]]; then
  uv pip install -e "./${KERNELBENCH_DIR}[gpu]"
else
  uv pip install -e "./${KERNELBENCH_DIR}"
fi
uv pip install -e .

echo
echo "Bootstrap complete."
echo "Environment: $(cd "${VENV_DIR}" && pwd)"
echo "KernelBench root: $(cd "${KERNELBENCH_DIR}" && pwd)"
echo
echo "Next commands:"
echo "  export PATH=\"$(cd "${VENV_DIR}/bin" && pwd):\$PATH\""
echo "  ./scripts/run_agent_problem.sh ..."
