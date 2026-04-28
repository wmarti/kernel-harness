# Architecture

This document describes the current system contract for the KernelBench harness.

## Scope

The harness runs one autonomous coding agent on one KernelBench optimization problem at a time, records the run, and stores the durable result under `archive/`.

The harness is responsible for:

- preparing a fresh workspace for one problem
- rendering the solver-facing contract into that workspace
- launching Codex or Claude with a narrow local surface
- exposing local workspace files directly under Landrun while brokered command tools handle privileged harness actions
- recording attempts, traces, status, completion, and profiler outputs
- aggregating archived results through `summarize-run`

The harness initializes the vendored KernelBench checkout during `./kb setup` and installs it into the configured Python environment. The same setup step builds `third_party/bin/landrun` for the direct-runtime sandbox. Operators may still override KernelBench with an explicit external `KERNELBENCH_ROOT`.

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

## Solver policy surfaces

The solver is nudged through several small surfaces rather than one giant prompt:

- generated workspace `AGENTS.md` — durable top-level contract
- generated `INITIAL_PROMPT.md` — opening run-specific instructions
- generated `GOAL_STATUS.md` — live status file re-read after measured actions
- helper-agent specs for `runner` and `profiler` — narrow delegated roles when the client runtime supports helper spawning

The intended behavior is:

- the main agent acts as the planner-manager
- `runner` handles measured evaluation by default
- `profiler` handles Nsight Compute work by default
- direct `run_candidate` / `profile_ncu` from the main agent are fallback paths when helper spawning is unavailable
- hosted web remains separate from command tools and should be used only for NVIDIA docs when the next optimization branch depends on hardware-specific behavior

## High-level flow

For one `(run_name, level, problem_id)` tuple, the harness does this:

1. resolves the problem and baseline information from the KernelBench checkout
2. prepares a fresh self-contained workspace
3. renders solver-facing docs and compatibility wrapper scripts into that workspace
4. prepares per-problem tool-private config under `state/tool_state/`
5. starts the launcher-owned command broker on a Unix socket under `state/`
6. launches Codex or Claude from the prepared workspace under Landrun
7. gives the model native workspace reads, write access only to `candidate_model_new.py`, hosted web search, and the `kernelbench_commands` MCP server for brokered harness actions
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
- `state/tool_state/`
- `state/runtime/`
- `state/s/`

It is safe to delete `state/` only when no run is active.

`DATA_ROOT` controls where `archive/` and `state/` are written. It does **not** decide where repository source files live.

## Per-problem tool-private config

The workspace is solver-visible. Tool-private config and auth live outside it.

- Codex authenticates from per-problem `state/tool_state/<run>/level_<level>/problem_<problem_id>/codex/` and launches with that `CODEX_HOME`
- Claude uses per-problem `CLAUDE_CONFIG_DIR=state/tool_state/<run>/level_<level>/problem_<problem_id>/claude/`

Those tool dirs are where the harness writes:

- generated Codex `config.toml`
- generated Claude `settings.json`
- generated Claude `.claude.json` for MCP server registration
- Claude keeps bash sandboxing disabled on this cluster-oriented setup; the active client-side guardrail is the Claude permissions allow/deny list plus Landrun filesystem policy
- generated helper-agent definitions for both tools
- tool-managed local state such as auth/session/history files

The harness mirrors auth only from repo-root tool dirs:

- `./.codex/auth.json` -> per-problem Codex home
- `./.claude/.credentials.json` -> per-problem Claude config dir

That keeps `state/tool_state/` disposable while leaving repo-root login state under user control. The harness intentionally does not read `~/.codex` or `~/.claude`.

This split is deliberate:

- the workspace should contain only problem files the solver is meant to read or edit
- tool auth/config should not sit inside the solver-visible workspace
- traces are **not** recovered from tool history files; each problem captures its own streamed `agent/events.jsonl` directly from the launcher and its own `agent/mcp_ir_events.jsonl` from the command broker
- `agent/events.jsonl` is the exact client stream you watch live; `agent/trace_ir.json` is the normalized merged view used for counts, audit, and summaries

## Codex vs Claude local-surface split

The two tool runtimes expose their native controls differently.

### Codex

Codex keeps its user/runtime config under per-problem `CODEX_HOME`. The harness generates:

- `state/tool_state/.../codex/config.toml`
- `state/tool_state/.../codex/agents/*.toml`

Codex launches from the prepared workspace under Landrun. The workspace mount is read-only except for `candidate_model_new.py`; shell execution is disabled; the generated config registers only the `kernelbench_commands` MCP server and forwards only `KBH_COMMAND_SOCKET`.

### Claude

Claude keeps its user/runtime config under per-problem `CLAUDE_CONFIG_DIR`. The harness generates:

- `state/tool_state/.../claude/settings.json`
- `state/tool_state/.../claude/.claude.json`
- `state/tool_state/.../claude/agents/*.md`

Claude also launches from the prepared workspace under Landrun. The generated `.claude.json` registers only the `kernelbench_commands` MCP server and forwards only `KBH_COMMAND_SOCKET`.

The practical result is the same for both tools **with respect to the actual problem environment**:

- no tool auth/config files in the workspace
- the real workspace is mounted read-only except for `candidate_model_new.py`
- hosted web access stays native to each client and is restricted separately from MCP
- shared web-search policy and helper-agent definitions
- `goal_status` is the live structured status query (remaining budget, attempt counts, best sample, baseline progress)
- `best_result` is the narrow structured query for the current best measured correct attempt

The enforcement mechanism differs:

- **Codex** launches with shell disabled, parent project-doc discovery disabled, and Landrun enforcing the workspace write boundary.
- **Claude** uses explicit permission allow/deny lists for its built-in tools, including no Bash, while Landrun enforces the workspace write boundary.

Implementation note: the official Python MCP SDK owns transport, protocol, and initialization for `kernelbench_commands`. The launcher renders a self-contained command MCP server into per-run tool state from `src/kernel_bench_experiment_agents/runtime/policy.py`; it forwards requests to the launcher-owned broker without mounting the harness source tree.

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
- `candidate_model_new.py` — initial free-form solver stub shown at workspace creation
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
- `completion.json` — terminal state plus measured outcome, trace-derived metadata, and `kernelbench_hacked_kernel_attempt_warnings`
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

Solver-visible problem files and history mirrors:

- read-only workspace files: `AGENTS.md`, `INITIAL_PROMPT.md`, `SPEC.md`, `HARDWARE.md`, `GOAL_STATUS.md`, `problem_reference.py`
- writable workspace file: `candidate_model_new.py`
- history mirrors: `samples/`, `profiles/`
- compatibility wrappers: `bin/*.sh`, which call the broker and require `KBH_COMMAND_SOCKET`

Workspace-local `samples/` and `profiles/` are mirrors for solver convenience. They are not the durable source of truth.

Notably absent from the workspace:

- Codex `auth.json`
- Codex `config.toml`
- Claude `settings.json`
- Claude `.claude.json`
- Claude credential files
- tool-private helper-agent config

Those live under `state/tool_state/` instead.

## Command tool surface

The solver reads workspace files directly and uses the `kernelbench_commands` MCP server for privileged harness actions.

The solver-visible command surface is:

- `run_candidate`
- `profile_ncu`
- `goal_status`
- `best_result`
- `complete_problem`

Semantics:

- the generated workspace is mounted read-only except for `candidate_model_new.py`
- `run_candidate` validates the current candidate before execution; validation failures return the exact rejected construct and do not count
- `run_candidate` is the only supported measured-evaluation path
- `profile_ncu` is the only supported profiling path
- `goal_status` is **not** just a cached file read: it refreshes `GOAL_STATUS.md` under the artifact lock and returns the live structured snapshot, including remaining budget, baseline status, current best-run summary, and the latest discarded-attempt reason when present
- `best_result` returns the best measured correct attempt manifest so far, including at least the `sample_id`, the measured result payload, and archive-relative artifact paths such as the archived kernel snapshot
- `complete_problem` is the only valid solver termination path

In other words, the solver surface is intentionally split into:

- **native web**: tool-specific hosted search/fetch, restricted to `docs.nvidia.com`
- **native workspace files**: problem contract, candidate file, and bounded history mirrors
- **command MCP tools**: measured run, profiling, live status refresh, best-result query, and completion

The compatibility `bin/*.sh` wrappers still exist in the workspace for humans and archived trace accounting, but they only work through the broker socket exported by the launcher.

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

Suspicious or otherwise non-counting attempts still remain in `attempts/` for audit, but they do not count toward progress, best-result selection, or summary beat-rate fields.

## Canonical JSON vs rendered Markdown

Where both JSON and Markdown exist, the JSON form is the canonical structured source and the Markdown form is a rendered solver-facing view.

Examples:

- `workspace_contract.json` renders workspace `AGENTS.md` and `INITIAL_PROMPT.md`
- `hardware.json` renders `HARDWARE.md`
- `goal_status.json` renders `GOAL_STATUS.md`

This is intentional, not independent duplicated documentation.
