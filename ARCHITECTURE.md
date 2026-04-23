# Architecture

This document describes the current system contract for the KernelBench harness.

## Scope

The harness runs one autonomous coding agent on one KernelBench optimization problem at a time, records the run, and stores the durable result under `archive/`.

The harness is responsible for:

- preparing a fresh workspace for one problem
- rendering the solver-facing contract into that workspace
- launching Codex or Claude with a narrow local surface
- exposing local problem interaction only through a shared MCP server plus hosted web search
- recording attempts, traces, status, completion, and profiler outputs
- aggregating archived results through `summarize-run`

The harness is **not** responsible for installing KernelBench itself. KernelBench setup belongs to the official KernelBench repository and the active environment you choose to run this harness in.

## Documentation audiences

There are two documentation surfaces.

Root docs are for maintainers and users:

- `README.md`
- `ARCHITECTURE.md`
- `AGENTS.md`
- `MEMORY.md`

Generated workspace docs are for the solver agent:

- `AGENTS.md`
- `SPEC.md`
- `HARDWARE.md`
- `GOAL_STATUS.md`

The same filename does not imply the same audience.

## High-level flow

For one `(run_name, level, problem_id)` tuple, the harness does this:

1. resolves the problem and baseline information from the KernelBench checkout
2. prepares a fresh self-contained workspace
3. renders solver-facing docs and compatibility wrapper scripts into that workspace
4. prepares the shared tool-private config under `state/config/`
5. starts a launcher-owned MCP sidecar for the problem
6. launches Codex or Claude from an empty per-problem scratch cwd under `state/cwd/`
7. gives the model only hosted web search plus the `kernelbench` MCP proxy for local problem work
8. records attempts, traces, completion state, and optional profiles
9. writes the durable record under `archive/<run_name>/level_<level>/problem_<problem_id>/`

The solver does not decide measured outcomes. The harness decides, from recorded attempts, whether the best correct solution beat eager PyTorch, `torch.compile`, both, or neither.

## Durable vs disposable state

### Durable: `archive/`

`archive/` is the only directory you should copy out for post-run analysis.

Per problem, the durable record is:

```text
archive/<run_name>/level_<level>/problem_<problem_id>/
  archive_manifest.json
  contract/
  agent/
  attempts/
  profiles/
```

### Disposable: `state/`

`state/` contains live mutable runtime data only:

- `state/workspaces/`
- `state/build/`
- `state/locks/`
- `state/config/codex/`
- `state/config/claude/`
- `state/cwd/codex/`
- `state/cwd/claude/`

It is safe to delete `state/` only when no run is active.

`DATA_ROOT` controls where `archive/` and `state/` are written. It does **not** decide where repository source files live.

## Shared tool-private config

The workspace is solver-visible. Tool-private config and auth live outside it.

- Codex uses `CODEX_HOME=state/config/codex/`
- Claude uses `CLAUDE_CONFIG_DIR=state/config/claude/`

Those shared tool dirs are where the harness writes:

- generated Codex `config.toml`
- generated Claude `settings.json`
- generated Claude `.claude.json` for MCP proxy registration
- Claude keeps bash sandboxing disabled on this cluster-oriented setup; the active client-side guardrail is the Claude permissions allow/deny list plus MCP-only workspace access
- generated helper-agent definitions for both tools
- tool-managed local state such as auth/session/history files

The harness seeds shared auth from repo-root tool dirs when they exist:

- `./.codex/auth.json` -> `state/config/codex/auth.json`
- `./.claude/.credentials.json` -> `state/config/claude/.credentials.json`

That keeps `state/config/` disposable while leaving repo-root login state under user control.

This split is deliberate:

- the workspace should contain only problem files the solver is meant to read or edit
- tool auth/config should not sit inside the solver-visible workspace
- traces are **not** recovered from shared tool history files; each problem captures its own streamed `agent/events.jsonl` directly from the launcher and its own `agent/mcp_ir_events.jsonl` from the launcher-owned MCP backend

## Codex vs Claude local-surface split

The two tool runtimes expose their native controls differently.

### Codex

Codex keeps its shared user/runtime config under `CODEX_HOME`. The harness generates:

- `state/config/codex/config.toml`
- `state/config/codex/agents/*.toml`

Codex launches from an empty per-problem cwd under `state/cwd/codex/...`, with the real workspace reachable only through the `kernelbench` MCP proxy. The launcher starts the real MCP backend outside the runtime and the runtime connects to it through a Unix socket.

### Claude

Claude keeps its shared user/runtime config under `CLAUDE_CONFIG_DIR`. The harness generates:

- `state/config/claude/settings.json`
- `state/config/claude/.claude.json`
- `state/config/claude/agents/*.md`

Claude also launches from an empty per-problem cwd under `state/cwd/claude/...`, with the real workspace reachable only through the `kernelbench` MCP proxy. The shared `state/config/claude/.claude.json` forwards only the per-problem socket path (`KBH_MCP_SOCKET`) into that proxy. The launcher-owned sidecar keeps the real MCP backend outside the runtime process tree and gives it the problem context (`DATA_ROOT`, `KBH_WORKSPACE`, `KBH_CLIENT_TOOL`, `KBH_MCP_EVENTS_PATH`).

The practical result is the same for both tools:

- no tool auth/config files in the workspace
- no direct local problem reads/writes through the client’s normal file tools
- the real MCP backend sits outside the runtime process tree
- shared web-search policy and helper-agent definitions

Implementation note: the official Python MCP SDK still owns transport, protocol, and initialization for the real backend. The harness-specific MCP layer under `src/kernel_bench_experiment_agents/mcp/` covers context loading, filesystem policy, resources, tool handlers, and the synthetic trace sidecar, while a small launcher-owned socket relay and runtime-side stdio proxy bridge the runtime to that backend.

## Archive contents

### `archive_manifest.json`

Machine-readable description of the problem archive. It explains what is canonical, what is mirrored into the live workspace, and what should be copied out.

### `contract/`

The exact problem contract shown to the solver, plus frozen structured metadata.

Important files:

- `AGENTS.md` — solver instructions and boundaries
- `SPEC.md` — problem goal and success criteria
- `HARDWARE.md` — human-readable hardware facts and guidance
- `INITIAL_PROMPT.md` — the exact initial launch prompt
- `problem_reference.py` — local copy of the reference PyTorch problem code
- `candidate_model_new.py` — initial solver scaffold shown at workspace creation
- `candidate_final.py` — final captured candidate when completion exists
- `problem.json` — machine-readable problem metadata, embedded eager/compile baseline runtimes, budget, and run identifiers
- `hardware.json` — machine-readable hardware facts
- `workspace_contract.json` — machine-readable solver contract
- `provenance.json` — archive-only outward provenance for the source KernelBench checkout and timing files
- `helper_agents/` — archived copies of the generated Codex and Claude helper-agent definitions

### `agent/`

The agent-side run record.

Important files:

- `events.jsonl` — raw streamed output from the agent CLI for this problem only
- `mcp_ir_events.jsonl` — synthetic MCP tool events for this problem only
- `trace_ir.json` — normalized trace representation used by the harness
- `completion.json` — terminal state plus measured outcome and trace-derived metadata
- `goal_status.json` — archived final goal-status snapshot for where the solver ended
- `final_message.txt` — the last assistant-visible model message, when one is available

### `attempts/`

Measured evaluation results for submitted candidates.

Important files:

- `sample_<id>.json` — one measured attempt record with correctness/runtime/build outcome and archive-relative file references
- `sample_<id>.stdout.txt` — evaluation subprocess stdout for that attempt
- `sample_<id>.stderr.txt` — evaluation subprocess stderr for that attempt
- `kernels/level_<level>_problem_<problem_id>_sample_<id>_kernel.py` — archived candidate source measured for that attempt

### `profiles/`

Nsight Compute profiling artifacts.

Important files:

- `profile_<id>.json` — one profile manifest with attempt linkage and archive-relative file references
- `profile_<id>.summary.txt` — short solver-facing summary of the selected metrics
- `profile_<id>.details.txt` — fuller textual profiler output
- `profile_<id>.stdout.txt` — profiler command stdout
- `profile_<id>.stderr.txt` — profiler command stderr

The harness keeps text-first profiler outputs. It does not archive `.ncu-rep` files.

## Workspace surface

Each solver workspace is fresh, self-contained, and intentionally small.

Solver-visible files:

- `AGENTS.md`
- `SPEC.md`
- `HARDWARE.md`
- `GOAL_STATUS.md`
- `goal_status.json`
- `hardware.json`
- `workspace_contract.json`
- `problem.json`
- `problem_reference.py`
- `candidate_model_new.py`
- `samples/`
- `profiles/`
- `bin/*.sh`

Workspace-local `samples/` and `profiles/` are mirrors for solver convenience. They are not the durable source of truth.

Notably absent from the workspace:

- Codex `auth.json`
- Codex `config.toml`
- Claude `settings.json`
- Claude `.claude.json`
- Claude credential files
- tool-private helper-agent config

Those live under `state/config/` instead.

## MCP tool surface

The solver is supposed to work through the `kernelbench` MCP server, not through ad hoc shell or local file workflows.

The solver-visible MCP surface is:

- `workspace_overview`
- `list_workspace_dir`
- `read_workspace_file`
- `write_candidate`
- `run_candidate`
- `profile_ncu`
- `goal_status`
- `best_result`
- `complete_problem`

Semantics:

- `run_candidate` is the only supported measured-evaluation path
- `profile_ncu` is the only supported profiling path
- `goal_status` refreshes live status explicitly
- `complete_problem` is the only valid solver termination path
- `read_workspace_file` and `list_workspace_dir` are the only supported local problem-read paths
- `write_candidate` is the only supported local edit path

The compatibility `bin/*.sh` wrappers still exist in the workspace for humans and archived trace accounting, but the model is not supposed to call them directly.

## Completion model

Solver-written completion is intentionally narrow:

- the only solver-visible exit path is `complete_problem(summary=...)`
- the solver does not choose between `done`, `budget_exhausted`, or other launcher-owned outcomes
- the harness records solver completion as `done` and infers the measured outcome from archived attempts

Launcher-owned terminal states are:

- `budget_exhausted`
- `failed_to_generate`

The harness computes the measured outcome from archived attempts:

- `beats_both`
- `beats_eager_only`
- `beats_compile_only`
- `beats_neither`

## Canonical JSON vs rendered Markdown

Where both JSON and Markdown exist, the JSON form is the canonical structured source and the Markdown form is a rendered solver-facing view.

Examples:

- `workspace_contract.json` renders workspace `AGENTS.md` and `INITIAL_PROMPT.md`
- `hardware.json` renders `HARDWARE.md`
- `goal_status.json` renders `GOAL_STATUS.md`

This is intentional, not independent duplicated documentation.
