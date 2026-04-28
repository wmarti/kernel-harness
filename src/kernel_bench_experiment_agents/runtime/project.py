"""Provide the small path and file helpers that the harness uses everywhere.

The harness code lives in the installed package, but all generated artifacts are rooted under DATA_ROOT
(`archive/` and `state/`). Callers should treat DATA_ROOT as an artifact root only, never as a way to
find repository source files.
"""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_root() -> Path:
    candidate = os.environ.get("DATA_ROOT") or "."
    return ensure_dir(Path(candidate).expanduser().resolve())


def archive_dir() -> Path:
    return ensure_dir(data_root() / "archive")


def state_dir() -> Path:
    return ensure_dir(data_root() / "state")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def landrun_bin(explicit: str | None = None) -> Path:
    candidate = explicit or os.environ.get("LANDRUN") or os.environ.get("LANDRUN_BIN")
    if candidate:
        path = Path(candidate).expanduser().resolve()
    else:
        path = repo_root() / "third_party" / "bin" / "landrun"
    if not path.exists():
        raise RuntimeError(f"Landrun binary does not exist: {path}. Run ./kb setup first.")
    if not os.access(path, os.X_OK):
        raise RuntimeError(f"Landrun binary is not executable: {path}")
    return path


def build_dir() -> Path:
    return ensure_dir(state_dir() / "build")


def locks_dir() -> Path:
    return ensure_dir(state_dir() / "locks")


def gpu_lock_dir() -> Path:
    return ensure_dir(locks_dir() / "gpu")


def artifact_lock_dir() -> Path:
    return ensure_dir(locks_dir() / "problem_state")


def solver_lock_dir() -> Path:
    return ensure_dir(locks_dir() / "solver")


def _lock_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _validate_component(value: str, *, label: str) -> str:
    if not value:
        raise RuntimeError(f"{label} must not be empty")
    if Path(value).name != value:
        raise RuntimeError(
            f"{label} must be a single path component without separators: {value!r}"
        )
    if value in {".", ".."}:
        raise RuntimeError(f"{label} is not allowed: {value!r}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise RuntimeError(
            f"{label} may contain only ASCII letters, digits, dot, underscore, and hyphen"
        )
    return value


def validate_run_name(run_name: str) -> str:
    return _validate_component(run_name, label="run_name")


def validate_sample_key(sample_key: str) -> str:
    return _validate_component(sample_key, label="sample_key")


def artifact_lock_path(run_name: str, level: int, problem_id: int) -> Path:
    run_name = validate_run_name(run_name)
    return artifact_lock_dir() / (
        f"{_lock_slug(run_name)}_level_{level}_problem_{problem_id}.lock"
    )


def solver_lock_path(run_name: str, level: int, problem_id: int) -> Path:
    run_name = validate_run_name(run_name)
    return solver_lock_dir() / (
        f"{_lock_slug(run_name)}_level_{level}_problem_{problem_id}.lock"
    )


def workspace_dir(run_name: str, level: int, problem_id: int) -> Path:
    run_name = validate_run_name(run_name)
    return ensure_dir(
        state_dir() / "workspaces" / run_name / f"level_{level}" / f"problem_{problem_id}"
    )


def archive_problem_dir(run_name: str, level: int, problem_id: int) -> Path:
    run_name = validate_run_name(run_name)
    return ensure_dir(
        archive_dir() / run_name / f"level_{level}" / f"problem_{problem_id}"
    )


def archive_contract_dir(run_name: str, level: int, problem_id: int) -> Path:
    return ensure_dir(archive_problem_dir(run_name, level, problem_id) / "contract")


def archive_agent_dir(run_name: str, level: int, problem_id: int) -> Path:
    return ensure_dir(archive_problem_dir(run_name, level, problem_id) / "agent")


def archive_attempts_dir(run_name: str, level: int, problem_id: int) -> Path:
    return ensure_dir(archive_problem_dir(run_name, level, problem_id) / "attempts")


def archive_attempt_kernel_dir(run_name: str, level: int, problem_id: int) -> Path:
    return ensure_dir(archive_attempts_dir(run_name, level, problem_id) / "kernels")


def archive_profiles_dir(run_name: str, level: int, problem_id: int) -> Path:
    return ensure_dir(archive_problem_dir(run_name, level, problem_id) / "profiles")


def build_problem_root(run_name: str, level: int, problem_id: int) -> Path:
    run_name = validate_run_name(run_name)
    return ensure_dir(build_dir() / run_name / f"level_{level}" / f"problem_{problem_id}")


def build_problem_dir(
    run_name: str,
    level: int,
    problem_id: int,
    sample_key: str,
) -> Path:
    sample_key = validate_sample_key(sample_key)
    return ensure_dir(build_problem_root(run_name, level, problem_id) / sample_key)


def kernelbench_root(explicit: str | None = None) -> Path:
    candidate = explicit or os.environ.get("KERNELBENCH_ROOT")
    if not candidate:
        raise RuntimeError(
            "KERNELBENCH_ROOT is not set. Point it at the official KernelBench checkout."
        )

    path = Path(candidate).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"KERNELBENCH_ROOT does not exist: {path}")
    return path


def next_sample_id(run_name: str, level: int, problem_id: int) -> int:
    kernel_pattern = re.compile(
        rf"^level_{level}_problem_{problem_id}_sample_(\d+)_kernel\.py$"
    )
    artifact_pattern = re.compile(r"^sample_(\d+)\.json$")
    max_sample = -1
    for child in archive_attempt_kernel_dir(run_name, level, problem_id).iterdir():
        match = kernel_pattern.match(child.name)
        if match:
            max_sample = max(max_sample, int(match.group(1)))
    for child in archive_attempts_dir(run_name, level, problem_id).iterdir():
        match = artifact_pattern.match(child.name)
        if match:
            max_sample = max(max_sample, int(match.group(1)))
    return max_sample + 1


def official_kernel_path(run_name: str, level: int, problem_id: int, sample_id: int) -> Path:
    return archive_attempt_kernel_dir(run_name, level, problem_id) / (
        f"level_{level}_problem_{problem_id}_sample_{sample_id}_kernel.py"
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def append_jsonl(path: Path, payload: Any) -> None:
    # callers must hold the per-problem state lease before appending here
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    ensure_dir(path.parent)
    path.write_text(content, encoding="utf-8")


def make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP)


def relative_path_within(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))
