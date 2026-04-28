# KernelBench agent harness

A reproducible agent harness for running Codex or Claude on **one KernelBench problem at a time** in a Landrun-bounded workspace.

It does four things:

- prepares a fresh per-problem workspace and solver contract
- exposes workspace files directly while routing privileged harness actions through a small command broker
- archives every attempt, profile, trace, and completion record under `archive/`
- scores the final result against eager and `torch.compile` baselines

The disposable live state is under `state/`. The durable record is under `archive/`.

If you only read one more file after this README, read `ARCHITECTURE.md`.


## What the model is actually told

The solver policy is not hidden in one giant prompt. It is split across a few generated files and resources that the model re-reads during the run:

- `AGENTS.md` — the durable top-level solver contract
- `INITIAL_PROMPT.md` — the opening run-specific instructions
- `GOAL_STATUS.md` — the live progress/status file the solver should re-read after measured actions
- helper-agent specs for `runner` and `profiler` — narrow delegated roles when the client runtime supports sub-agents

The intended behavior is:

- the main agent acts as the **planner-manager**
- `runner` handles measured evaluation by default
- `profiler` handles Nsight Compute work by default
- direct `run_candidate` / `profile_ncu` from the main agent are fallback paths when helper spawning is unavailable

## Actual solver surface

For **both** Codex and Claude, the solver starts in the generated problem workspace under Landrun. The workspace is read-only except for `candidate_model_new.py`. Hosted web access stays separate and tool-native.

The exact live model trace is saved as `archive/.../agent/events.jsonl`. `trace_ir.json` is the normalized merged view used for counts and summaries.

- local read surface: generated workspace files plus `samples/` and `profiles/`
- local write surface: `candidate_model_new.py` only
- command tools: `run_candidate`, `profile_ncu`, `goal_status`, `best_result`, `complete_problem`
- `goal_status` returns the live JSON status snapshot (remaining budget, attempt counts, latest discarded-attempt reason when present, best sample, baseline progress)
- `best_result` returns the current best measured correct attempt, including `sample_id` and archive-relative artifact paths
- native web stays separate from MCP and is limited to `docs.nvidia.com`

The client-specific enforcement differs slightly:

- **Codex** runs with shell disabled and a writable per-problem `CODEX_HOME` under `state/tool_state/`.
- **Claude** gets native file tools but no Bash, with a per-problem `CLAUDE_CONFIG_DIR` under `state/tool_state/`.


## Set up the environment

Run setup from the harness repo root. It creates a uv-managed Python 3.10 environment, initializes the vendored checkouts, builds the repo-local Landrun binary, installs KernelBench and this harness, and records the selected interpreter in `.kb-python`.

```bash
./kb setup
```

Before local or batch runs on cluster nodes, load CUDA if your cluster requires it.

```bash
module load cuda
```

This harness assumes:

- the vendored KernelBench checkout is initialized by `./kb setup`, or `KERNELBENCH_ROOT` points to an explicit external checkout
- the KernelBench timing files already exist for your hardware
- `KERNELBENCH_TIMINGS_DIR` is optional; set it only when your timing results live outside the default KernelBench timing tree

## Authenticate the agent tools

Run these commands from the harness repo root.

The harness generates per-problem tool state under `state/tool_state/` on launch. Authenticate once into repo-root tool dirs, and the harness will mirror only those repo-root auth files into the per-problem tool state each time it prepares a run. It intentionally does not read `~/.codex` or `~/.claude`.

### Codex

Preferred path: sign in once into repo-root `./.codex/`, using file-backed credentials so the harness can copy `auth.json` into per-problem `state/tool_state/.../codex/` on launch.

```bash
mkdir -p .codex
CODEX_HOME="./.codex" codex -c cli_auth_credentials_store=file login --device-auth
CODEX_HOME="./.codex" codex login status
```

Alternative: export an API key instead.

```bash
export OPENAI_API_KEY=...
```

### Claude Code

Preferred path: sign in once into repo-root `./.claude/`. The harness copies only `./.claude/.credentials.json` into per-problem `state/tool_state/.../claude/` on launch. If a fresh `claude login` works in your normal shell but the harness does not, refresh the repo-root `./.claude/.credentials.json` file; the harness intentionally ignores `~/.claude`.

```bash
mkdir -p .claude
CLAUDE_CONFIG_DIR="./.claude" claude login
```

Alternatives: export API credentials or an OAuth token.

```bash
export ANTHROPIC_API_KEY=...
# or
export ANTHROPIC_AUTH_TOKEN=...
# or
export CLAUDE_CODE_OAUTH_TOKEN=...
```

## Most common runs

Run these scripts from the harness repo root.

### Run one problem

```bash
TOOL=codex \
RUN_NAME=kernelbench-codex-h100-v4 \
LEVEL=1 \
PROBLEM_ID=1 \
MODEL=gpt-5.4 \
TIME_BUDGET_MINUTES=180 \
PRECISION=bf16 \
KERNELBENCH_ROOT=/path/to/KernelBench \
HARDWARE_NAME=H100 \
./scripts/run_agent_problem.sh
```

### Run one problem with Claude

```bash
TOOL=claude \
RUN_NAME=kernelbench-claude-h100-v4 \
LEVEL=1 \
PROBLEM_ID=1 \
MODEL=claude-opus-4-7 \
TIME_BUDGET_MINUTES=180 \
PRECISION=bf16 \
KERNELBENCH_ROOT=/path/to/KernelBench \
HARDWARE_NAME=H100 \
./scripts/run_agent_problem.sh
```

### Run a contiguous range

```bash
TOOL=codex \
RUN_NAME=kernelbench-codex-h100-v4 \
LEVEL=1 \
START_PROBLEM_ID=1 \
END_PROBLEM_ID=10 \
MODEL=gpt-5.4 \
TIME_BUDGET_MINUTES=180 \
PRECISION=bf16 \
KERNELBENCH_ROOT=/path/to/KernelBench \
HARDWARE_NAME=H100 \
./scripts/run_agent_range.sh
```

### Run an explicit problem list

```bash
TOOL=claude \
RUN_NAME=kernelbench-claude-h100-v4 \
LEVEL=1 \
PROBLEM_IDS=1,4,9 \
MODEL=claude-opus-4-7 \
TIME_BUDGET_MINUTES=180 \
PRECISION=bf16 \
KERNELBENCH_ROOT=/path/to/KernelBench \
HARDWARE_NAME=H100 \
./scripts/run_agent_range.sh
```

### Submit the Slurm wrapper

Submit from the harness repo root. The script itself carries the default `#SBATCH` / `#YBATCH` header block for the common H100 path, so the usual launch is still:

```bash
ybatch --export=TOOL=codex,RUN_NAME=kernelbench-codex-h100-v4,LEVEL=1,START_PROBLEM_ID=1,END_PROBLEM_ID=10,MODEL=gpt-5.4,TIME_BUDGET_MINUTES=180,PRECISION=bf16,KERNELBENCH_ROOT=/path/to/KernelBench,HARDWARE_NAME=H100 ./scripts/run_agent_problem.slurm.sh
```

Override those scheduler defaults in the script header or on the submit command when your cluster needs something different. Use `sbatch` instead of `ybatch` on clusters that expose plain Slurm submission.

### Summarize one archived run

```bash
kbharness summarize-run --run-name kernelbench-codex-h100-v4
```

This scans only `archive/<run_name>/` and writes `archive/<run_name>/run_summary.json`. Summary beat-rates and best-runtime fields exclude suspicious or otherwise non-counting attempts.

## User knobs you will actually use

These are the main variables worth changing:

- `DATA_ROOT=/path/for/archive-and-state` if you want artifacts somewhere other than `./`
- `TOOL=codex|claude`
- `MODEL=...`
- `RUN_NAME=...`
- `LEVEL=...`
- `PROBLEM_ID=...`
- `START_PROBLEM_ID=...` / `END_PROBLEM_ID=...`
- `PROBLEM_IDS=1,4,9`
- `TIME_BUDGET_MINUTES=...`
- `PRECISION=bf16`
- `KERNELBENCH_ROOT=/path/to/KernelBench`
- `HARDWARE_NAME=H100`
- `KERNELBENCH_TIMINGS_DIR=/path/to/results/timing/<hardware>` when you need a non-default timings location
- inherited `CUDA_VISIBLE_DEVICES` when you want to pin visible GPUs from the scheduler or shell

## Where to look after a run

The only durable copy-out root is:

```text
archive/<run_name>/
```

Live workspaces, locks, per-problem tool config, runtime scratch directories, and build products live under `state/` and are disposable once no run is active.

## CLI surface

Installing this repo exposes the harness CLI:

```bash
kbharness --help
```

The launcher scripts are the normal entrypoints. The CLI exists mainly so those scripts, workspace wrappers, and the MCP server can call the harness internals in a stable way.

## Need more detail?

Read `ARCHITECTURE.md` for:

- archive contents and file meanings
- workspace contents and solver boundaries
- per-problem Codex / Claude config layout under `state/tool_state/`
- the Landrun direct-workspace and command-broker surface
- how profiling, attempts, traces, and summaries are recorded
