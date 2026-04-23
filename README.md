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

## Setup

The clean repo-root entrypoint is:

```bash
./kb setup
```

Optional setup knobs:

- `./kb setup --python 3.10`
- `./kb setup --venv-dir /path/to/venv`

This harness assumes:

- the official KernelBench checkout exists either at `./third_party/KernelBench/` or wherever `KERNELBENCH_ROOT` points
- the KernelBench timing files already exist for your hardware
- `KERNELBENCH_TIMINGS_DIR` is optional; set it only when your timing results live outside the default KernelBench timing tree

Notes:

- `./kb setup` always syncs and initializes the vendored `third_party/KernelBench` submodule first, so older clones pick up `.gitmodules` URL changes automatically.
- `./kb setup` always uses `uv` and provisions a Python 3.10 environment under `./.venv` by default.
- When `./.venv` already exists, `./kb setup` removes and recreates it non-interactively before reinstalling packages.
- For safety, `--venv-dir` only auto-replaces the repo-managed `./.venv` or an existing directory that already looks like a virtualenv; it refuses to delete arbitrary existing directories.
- `./kb setup` defaults `uv` to `UV_LINK_MODE=copy`, which avoids noisy hardlink fallback warnings on NFS or cross-filesystem setups.
- `./kb run` and `./kb range` require `--hardware-name` unless `HARDWARE_NAME` is already set in the environment.
- `./kb submit` requires `--partition` and `--hardware-name`, plus one of `--problem-id`, `--problem-ids`, or `--start-problem-id/--end-problem-id`.
- When the active submit command is `ybatch`, `./kb submit` also requires `--ybatch-resource` or `KB_YBATCH_RESOURCE`.
- When `uv` is missing, `./kb setup` prints the official install command plus the installation docs URL and exits.
- KernelBench upstream currently publishes `requires-python = "==3.10.*"` in its `pyproject.toml`, so the supported setup path today is still Python 3.10.x.
- Pass `--gpu-extras` when you want `KernelBench[gpu]`. For compatibility, `INSTALL_KERNELBENCH_GPU_EXTRAS=1` is still honored too.
- `./kb setup` records the selected interpreter in `./.kb-python`, and the launchers reuse that exact Python on later runs instead of guessing from a stale `./.venv`.
- When `RUN_NAME` is unset, the launchers generate a unique default like `kernelbench-codex-20260423T081530Z-12345` so reruns do not reuse the same archive tree by accident.

For compatibility, `./scripts/bootstrap_uv.sh` still works and now forwards to `./kb setup`.

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

Run these commands from the harness repo root.

### Run one problem

```bash
./kb run \
  --tool codex \
  --run-name kernelbench-codex-h100-v3 \
  --level 1 \
  --problem-id 1 \
  --model gpt-5.4 \
  --time-budget-minutes 180 \
  --precision bf16 \
  --hardware-name H100
```

### Run one problem with Claude

```bash
./kb run \
  --tool claude \
  --run-name kernelbench-claude-h100-v3 \
  --level 1 \
  --problem-id 1 \
  --model opus-4.6 \
  --time-budget-minutes 180 \
  --precision bf16 \
  --hardware-name H100
```

If you are **not** using the vendored submodule, add:

```bash
--kernelbench-root /path/to/KernelBench
```

### Run a contiguous range

```bash
./kb range \
  --tool codex \
  --run-name kernelbench-codex-h100-v3 \
  --level 1 \
  --start-problem-id 1 \
  --end-problem-id 10 \
  --model gpt-5.4 \
  --time-budget-minutes 180 \
  --precision bf16 \
  --hardware-name H100
```

### Run an explicit problem list

```bash
./kb range \
  --tool claude \
  --run-name kernelbench-claude-h100-v3 \
  --level 1 \
  --problem-ids 1,4,9 \
  --model opus-4.6 \
  --time-budget-minutes 180 \
  --precision bf16 \
  --hardware-name H100
```

### Submit one problem to Slurm

```bash
./kb submit \
  --partition h100 \
  --hardware-name H100 \
  --ybatch-resource h100_1 \
  --tool codex \
  --problem-id 1
```

`./kb submit` uses `ybatch` automatically when that site-local command exists; otherwise it uses `sbatch`.
Use `--dry-run` first when you want to inspect the exact submit command and any generated `ybatch` wrapper without queueing a job.

### Submit a range to Slurm

```bash
./kb submit \
  --partition a100 \
  --hardware-name A100 \
  --ybatch-resource a100_1 \
  --tool codex \
  --start-problem-id 1 \
  --end-problem-id 10 \
  --time 13:00:00
```

Keep the scheduler choice explicit. `./kb submit` does not try to autodetect free hardware or choose GPU fallbacks for you.
On clusters with a site-local `ybatch`, the resource name is still site-specific, so set `--ybatch-resource` or `KB_YBATCH_RESOURCE` yourself.

### Summarize one archived run

```bash
./kb summarize-run --run-name kernelbench-codex-h100-v3
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

## Lower-level entrypoints

`./kb` is the clean user-facing wrapper. These still exist underneath it:

```bash
./scripts/run_agent_problem.sh
./scripts/run_agent_range.sh
./scripts/run_agent_problem.slurm.sh
./scripts/kbharness --help
```

The shell launchers and workspace wrappers call the repo-local `scripts/kbharness` wrapper, which runs `python -m kernel_bench_experiment_agents.cli` against the repo source tree. An installed `kbharness` console script is still fine, but it is no longer required just to use this repo.

## Need more detail?

Read `ARCHITECTURE.md` for:

- archive contents and file meanings
- workspace contents and solver boundaries
- shared Codex / Claude config layout under `state/config/`
- the MCP-only local tool surface
- how profiling, attempts, traces, and summaries are recorded
