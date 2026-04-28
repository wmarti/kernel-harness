#!/usr/bin/env bash
# Smoke-test the static candidate contract validator.
set -euo pipefail

if [[ ! -f "./pyproject.toml" || ! -d "./src/kernel_bench_experiment_agents" ]]; then
  echo "Run scripts/test_candidate_validation.sh from the harness repo root." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -f "${REPO_ROOT}/.kb-python" ]]; then
  # shellcheck source=./kb_python.sh
  source "${SCRIPT_DIR}/kb_python.sh"
  PYTHON_BIN="$(resolve_repo_python "${REPO_ROOT}" "./kb setup")"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

"${PYTHON_BIN}" - <<'PY'
from __future__ import annotations

from kernel_bench_experiment_agents.kernelbench.candidate.validation import (
    CandidateValidationError,
    validate_candidate_source,
)
from kernel_bench_experiment_agents.kernelbench.candidate.contract import candidate_template


BASE = r'''
import torch
import torch.nn as nn
from torch.utils.cpp_extension import load_inline

CPP_SOURCE = r"""
#include <torch/extension.h>
PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
}
"""

CUDA_SOURCE = r"""
#include <cuda_runtime.h>
__global__ void k() {
}
"""

class ModelNew(nn.Module):
    def __init__(self):
        super().__init__()
        self.mod = load_inline(
            name="validator_smoke",
            cpp_sources=CPP_SOURCE,
            cuda_sources=CUDA_SOURCE,
            functions=None,
            with_cuda=True,
        )

    def forward(self, *inputs):
        return inputs[0]
'''


def expect_ok(source: str, label: str) -> None:
    validate_candidate_source(source)
    print(f"ok: {label}")


def expect_reject(source: str, label: str) -> None:
    try:
        validate_candidate_source(source)
    except CandidateValidationError as exc:
        print(f"reject: {label}: {exc}")
        return
    raise AssertionError(f"expected rejection for {label}")


expect_ok(BASE, "valid free-form candidate")
expect_ok(candidate_template(), "generated starter stub")
expect_reject(BASE.replace("class ModelNew", "class OtherModel"), "missing ModelNew")

for expression in (
    "torch.matmul(inputs[0], inputs[1])",
    "torch.mm(inputs[0], inputs[1])",
    "torch.bmm(inputs[0], inputs[1])",
    "torch.einsum('ij,jk->ik', inputs[0], inputs[1])",
    "inputs[0] @ inputs[1]",
):
    expect_reject(BASE.replace("return inputs[0]", f"return {expression}"), expression)

expect_reject("import triton\n" + BASE, "Triton import")
expect_reject(BASE.replace("__global__ void k()", "extern \"C\" void cublasSgemm()"), "cuBLAS marker")
expect_reject(BASE.replace("__global__ void k()", "extern \"C\" void cudnnConvolutionForward()"), "cuDNN marker")
expect_reject(BASE.replace("__global__ void k()", "extern \"C\" void cutlass_kernel()"), "CUTLASS marker")
expect_reject(
    BASE.replace("with_cuda=True,", "with_cuda=True,\n            build_directory='/tmp/outside',"),
    "build_directory escape",
)
PY
