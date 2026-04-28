"""Describe the solver-editable candidate file and its starter template."""

from __future__ import annotations

from textwrap import dedent


CANDIDATE_FILENAME = "candidate_model_new.py"


def candidate_template() -> str:
    return dedent(
        '''
        import torch
        import torch.nn as nn
        from torch.utils.cpp_extension import load_inline


        CPP_SOURCE = r"""
        #include <torch/extension.h>

        PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
            // Register extension entrypoints here.
        }
        """

        CUDA_SOURCE = r"""
        #include <torch/extension.h>
        #include <cuda.h>
        #include <cuda_runtime.h>

        __global__ void placeholder_kernel() {
        }
        """


        class ModelNew(nn.Module):
            def __init__(self, *init_inputs):
                super().__init__()
                self._module = load_inline(
                    name="candidate_extension",
                    cpp_sources=CPP_SOURCE,
                    cuda_sources=CUDA_SOURCE,
                    functions=None,
                    extra_cflags=["-O3", "-std=c++17"],
                    extra_cuda_cflags=["-O3", "-std=c++17"],
                    with_cuda=True,
                    verbose=False,
                )

            def forward(self, *inputs):
                raise NotImplementedError("Call your compiled CUDA extension entrypoint here.")
        '''
    ).strip() + "\n"
