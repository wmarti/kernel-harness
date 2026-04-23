# KernelBench harness

This repository runs autonomous coding agents on KernelBench optimization problems, one problem at a time, and records the durable results under `archive/`.

At a high level:

- the harness prepares a fresh per-problem workspace
- the model does **not** use direct local file or shell tools for problem work
- local problem interaction goes through a shared MCP server that exposes the harness tool surface
- hosted web search stays tool-native and domain-restricted
- the durable record lives under `archive/`
- disposable live state lives under `state/`

For the detailed system contract, archive layout, workspace layout, MCP/config split, and runtime boundary notes, read `ARCHITECTURE.md`.

## Recommended setup: vendored KernelBench + uv bootstrap

The preferred setup is:

- vendor KernelBench under `third_party/KernelBench/` as a git submodule
- create a local `uv` environment at `./.venv`
- install **both** KernelBench and this harness into that same environment

From the harness repo root:

Example:

```bash
./scripts/bootstrap_uv.sh
export PATH="$(pwd)/.venv/bin:$PATH"
```

This harness assumes:

- the official KernelBench checkout exists either at `./third_party/KernelBench/` or wherever `KERNELBENCH_ROOT` points
- the KernelBench timing files already exist for your hardware
- `KERNELBENCH_TIMINGS_DIR` is optional; set it only when your timing results live outside the default KernelBench timing tree

Notes:

- `scripts/bootstrap_uv.sh` runs `git submodule update --init --recursive third_party/KernelBench`, creates `./.venv`, and installs both editable packages into it with `uv`.
- When `uv` is missing, the bootstrap script prints the official install command plus the installation docs URL and exits.
- Set `INSTALL_KERNELBENCH_GPU_EXTRAS=1` when you want the bootstrap script to install `KernelBench[gpu]` as well.
- The launcher scripts automatically prepend `./.venv/bin` to `PATH` when that local environment exists.

## Authenticate the agent tools

Run these commands from the harness repo root.

The harness generates `state/config/` itself on launch. Authenticate once into repo-root tool dirs, and the harness will copy just the auth files into `state/config/` each time it recreates shared tool state.

### Codex

Preferred path: sign in once into repo-root `./.codex/`, using file-backed credentials so the harness can copy `auth.json` into `state/config/codex/` on launch.

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

Preferred path: sign in once into repo-root `./.claude/`. The harness copies `./.claude/.credentials.json` into `state/config/claude/` on launch.

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
RUN_NAME=kernelbench-codex-h100-v3 \
LEVEL=1 \
PROBLEM_ID=1 \
MODEL=gpt-5.4 \
TIME_BUDGET_MINUTES=180 \
PRECISION=bf16 \
HARDWARE_NAME=H100 \
./scripts/run_agent_problem.sh
```

### Run one problem with Claude

```bash
TOOL=claude \
RUN_NAME=kernelbench-claude-h100-v3 \
LEVEL=1 \
PROBLEM_ID=1 \
MODEL=opus-4.6 \
TIME_BUDGET_MINUTES=180 \
PRECISION=bf16 \
HARDWARE_NAME=H100 \
./scripts/run_agent_problem.sh
```

If you are **not** using the vendored submodule, add:

```bash
KERNELBENCH_ROOT=/path/to/KernelBench
```

### Run a contiguous range

```bash
TOOL=codex \
RUN_NAME=kernelbench-codex-h100-v3 \
LEVEL=1 \
START_PROBLEM_ID=1 \
END_PROBLEM_ID=10 \
MODEL=gpt-5.4 \
TIME_BUDGET_MINUTES=180 \
PRECISION=bf16 \
HARDWARE_NAME=H100 \
./scripts/run_agent_range.sh
```

### Run an explicit problem list

```bash
TOOL=claude \
RUN_NAME=kernelbench-claude-h100-v3 \
LEVEL=1 \
PROBLEM_IDS=1,4,9 \
MODEL=opus-4.6 \
TIME_BUDGET_MINUTES=180 \
PRECISION=bf16 \
HARDWARE_NAME=H100 \
./scripts/run_agent_range.sh
```

### Submit the Slurm wrapper

Submit from the harness repo root. The script itself carries the default `#SBATCH` / `#YBATCH` header block for the common H100 path, so the usual launch is still:

```bash
ybatch --export=TOOL=codex,RUN_NAME=kernelbench-codex-h100-v3,LEVEL=1,START_PROBLEM_ID=1,END_PROBLEM_ID=10,MODEL=gpt-5.4,TIME_BUDGET_MINUTES=180,PRECISION=bf16,HARDWARE_NAME=H100 ./scripts/run_agent_problem.slurm.sh
```

Override those scheduler defaults in the script header or on the submit command when your cluster needs something different. Use `sbatch` instead of `ybatch` on clusters that expose plain Slurm submission.

### Summarize one archived run

```bash
kbharness summarize-run --run-name kernelbench-codex-h100-v3
```

This scans only `archive/<run_name>/` and writes `archive/<run_name>/run_summary.json`.

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
- `KERNELBENCH_ROOT=/path/to/KernelBench` when you are not using `./third_party/KernelBench`
- `HARDWARE_NAME=H100`
- `KERNELBENCH_TIMINGS_DIR=/path/to/results/timing/<hardware>` when you need a non-default timings location
- inherited `CUDA_VISIBLE_DEVICES` when you want to pin visible GPUs from the scheduler or shell

## Where to look after a run

The only durable copy-out root is:

```text
archive/<run_name>/
```

Live workspaces, locks, shared tool config, per-problem scratch directories, and build products live under `state/` and are disposable once no run is active.

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
- shared Codex / Claude config layout under `state/config/`
- the MCP-only local tool surface
- how profiling, attempts, traces, and summaries are recorded
