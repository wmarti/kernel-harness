"""Implement the measured candidate-evaluation command used by the workspace run wrapper.

This module validates the candidate, records archived attempt metadata, leases a GPU slot, and updates goal status after each run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from kernel_bench_experiment_agents.workspace.archive import sample_manifest_path
from kernel_bench_experiment_agents.kernelbench.candidate.contract import CANDIDATE_FILENAME
from kernel_bench_experiment_agents.kernelbench.candidate.snapshot import read_validated_candidate_source, write_run_candidate_snapshot
from kernel_bench_experiment_agents.kernelbench.candidate.validation import CandidateValidationError
from kernel_bench_experiment_agents.runtime.common import as_float, emit_json
from kernel_bench_experiment_agents.agent_contract.goal_status import write_goal_status_files
from kernel_bench_experiment_agents.runtime.live_gpu_wait import (
    clear_live_gpu_wait_marker,
    create_live_gpu_wait_marker,
    settle_live_gpu_wait_marker,
)
from kernel_bench_experiment_agents.runtime.gpu_pool import isolated_gpu_environment, lease_gpu_slot, lease_problem_artifacts
from kernel_bench_experiment_agents.runtime.project import (
    archive_problem_dir,
    build_problem_dir,
    next_sample_id,
    now_iso,
    official_kernel_path,
    relative_path_within,
    write_json,
    write_text,
)
from kernel_bench_experiment_agents.runtime.subprocess_tools import excerpt, load_json_object, run_subprocess_capture, serialize_exception
from kernel_bench_experiment_agents.workspace.paths import (
    load_workspace_metadata,
    validate_workspace_assignment,
    workspace_candidate_path,
    workspace_path,
    workspace_relpath,
    write_workspace_sample_copy,
    workspace_samples_dir,
)


def _workspace_candidate_reference(candidate_path: Path, workspace: Path | None) -> str:
    if workspace is not None:
        return workspace_relpath(candidate_path, workspace)
    return candidate_path.name


def _workspace_sample_artifact(workspace: Path | None, sample_id: int, suffix: str) -> Path | None:
    if workspace is None:
        return None
    return workspace_samples_dir(workspace) / f"sample_{sample_id}{suffix}"


def _solver_artifact_reference(
    *,
    workspace: Path | None,
    sample_id: int,
    archive_path: Path,
    problem_archive_root: Path,
    suffix: str,
) -> str:
    workspace_path_ref = _workspace_sample_artifact(workspace, sample_id, suffix)
    if workspace_path_ref is not None:
        return workspace_relpath(workspace_path_ref, workspace)
    return relative_path_within(archive_path, problem_archive_root)


def _solver_visible_payload(payload: dict[str, Any], workspace: Path | None) -> dict[str, Any]:
    if workspace is None:
        return payload
    sample_id = payload.get("sample_id")
    if not isinstance(sample_id, int):
        return payload
    kernel_path = _workspace_sample_artifact(workspace, sample_id, ".py")
    stdout_path = _workspace_sample_artifact(workspace, sample_id, ".stdout.txt")
    stderr_path = _workspace_sample_artifact(workspace, sample_id, ".stderr.txt")
    if kernel_path is None or stdout_path is None or stderr_path is None:
        return payload
    solver_payload = dict(payload)
    solver_payload["archive_kernel_path"] = workspace_relpath(kernel_path, workspace)
    solver_payload["stdout_path"] = workspace_relpath(stdout_path, workspace)
    solver_payload["stderr_path"] = workspace_relpath(stderr_path, workspace)
    return solver_payload


def _result_warnings(
    result: dict[str, Any],
    workspace: Path | None,
    *,
    stdout_text: str = "",
) -> list[str]:
    warnings: list[str] = []
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    if metadata.get("excessive_speedup"):
        warnings.append(
            "KernelBench flagged this run as suspicious because the measured speedup is excessively large. This run does not count toward progress. Discard it as possible reward hacking and continue iterating until you have a non-suspicious result."
        )
    for line in stdout_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[WARNING]"):
            warnings.append(stripped)
    if workspace is None:
        return warnings
    metadata = load_workspace_metadata(workspace)
    baseline_runtime_ms = metadata.get("baseline_runtime_ms") if isinstance(metadata, dict) else None
    baseline_runtime_ms = baseline_runtime_ms if isinstance(baseline_runtime_ms, dict) else {}
    eager_baseline = as_float(baseline_runtime_ms.get("eager"))
    ref_runtime = as_float(result.get("ref_runtime"))
    if eager_baseline is None or ref_runtime is None or eager_baseline <= 0:
        return warnings
    relative_delta = abs(ref_runtime - eager_baseline) / eager_baseline
    if relative_delta > 0.15:
        warnings.append(
            f"KernelBench reported ref_runtime={ref_runtime} ms but the archived eager baseline is {eager_baseline} ms; relative delta {relative_delta:.1%}. Review this problem manually before trusting the baseline comparison."
        )
    return warnings



def command_run_candidate(args: argparse.Namespace) -> None:
    """Evaluate one frozen candidate snapshot and persist the measured attempt payload."""
    candidate_path = Path(args.candidate).resolve()
    workspace = workspace_path(args.workspace) if args.workspace else None
    problem_archive_root = archive_problem_dir(args.run_name, args.level, args.problem_id)
    lease_name = f"artifacts:{args.run_name}:level_{args.level}:problem_{args.problem_id}"
    sample_id: int | None = None
    payload: dict[str, Any] | None = None
    sample_json_path: Path | None = None
    live_gpu_wait_marker = None
    failure: Exception | None = None
    persist_failure: Exception | None = None

    try:
        with lease_problem_artifacts(
            run_name=args.run_name,
            level=args.level,
            problem_id=args.problem_id,
            lease_name=lease_name,
        ) as artifact_lease:
            sample_id = next_sample_id(args.run_name, args.level, args.problem_id)
            kernel_path = official_kernel_path(
                args.run_name,
                args.level,
                args.problem_id,
                sample_id,
            )
            sample_json_path = sample_manifest_path(
                args.run_name,
                args.level,
                args.problem_id,
                sample_id,
            )
            stdout_path = sample_json_path.with_suffix(".stdout.txt")
            stderr_path = sample_json_path.with_suffix(".stderr.txt")
            if workspace is not None:
                validate_workspace_assignment(
                    workspace,
                    run_name=args.run_name,
                    level=args.level,
                    problem_id=args.problem_id,
                )
                expected_candidate_path = workspace_candidate_path(workspace)
                if candidate_path != expected_candidate_path:
                    raise CandidateValidationError(
                        f"Only {CANDIDATE_FILENAME} may be evaluated from the problem workspace."
                    )

            candidate_ref = _workspace_candidate_reference(candidate_path, workspace)
            payload = {
                "status": "started",
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "run_name": args.run_name,
                "level": args.level,
                "problem_id": args.problem_id,
                "sample_id": sample_id,
                "candidate_path": candidate_ref,
                "archive_kernel_path": relative_path_within(kernel_path, problem_archive_root),
                "stdout_path": relative_path_within(stdout_path, problem_archive_root),
                "stderr_path": relative_path_within(stderr_path, problem_archive_root),
                "backend": args.backend,
                "precision": args.precision,
                "artifact_reservation_wait_seconds": artifact_lease.wait_seconds,
                "artifact_commit_wait_seconds": None,
                "gpu_id": None,
                "gpu_device_selector": None,
                "gpu_visible_devices": None,
                "gpu_logical_id": None,
                "gpu_selector_source": None,
                "gpu_wait_seconds": None,
                "result": {},
                "warnings": [],
                "error": None,
            }

            candidate_src = read_validated_candidate_source(candidate_path)
            kernel_path = write_run_candidate_snapshot(
                run_name=args.run_name,
                level=args.level,
                problem_id=args.problem_id,
                sample_id=sample_id,
                candidate_src=candidate_src,
            )
            if workspace is not None:
                write_workspace_sample_copy(workspace, sample_id, candidate_src)
            write_json(sample_json_path, payload)

        # The launcher polls goal status while this wrapper may still be queued for a
        # GPU lease, so record the live wait immediately instead of only after the
        # command eventually persists gpu_wait_seconds at the end of the run.
        live_gpu_wait_marker = create_live_gpu_wait_marker(
            run_name=args.run_name,
            level=args.level,
            problem_id=args.problem_id,
            operation="run_candidate",
            requested_gpu=args.gpu_id,
            num_gpu_slots=args.num_gpu_slots,
        )
        with lease_gpu_slot(
            num_slots=args.num_gpu_slots,
            requested_slot=args.gpu_id,
            lease_name=f"run:{args.run_name}:level_{args.level}:problem_{args.problem_id}",
        ) as lease:
            settle_live_gpu_wait_marker(live_gpu_wait_marker, wait_seconds=lease.wait_seconds)

            runner_output_path = build_problem_dir(
                args.run_name,
                args.level,
                args.problem_id,
                f"sample_{sample_id}",
            ) / "evaluation_result.json"
            command = [
                sys.executable,
                "-m",
                "kernel_bench_experiment_agents.kernelbench.runners.evaluation",
                "--candidate",
                str(kernel_path),
                "--output-path",
                str(runner_output_path),
                "--level",
                str(args.level),
                "--problem-id",
                str(args.problem_id),
                "--dataset-src",
                args.dataset_src,
                "--gpu-id",
                str(lease.logical_gpu_id),
                "--run-name",
                args.run_name,
                "--sample-id",
                str(sample_id),
                "--backend",
                args.backend,
                "--precision",
                args.precision,
                "--num-correct-trials",
                str(args.num_correct_trials),
                "--num-perf-trials",
                str(args.num_perf_trials),
            ]
            if args.kernelbench_root:
                command.extend(["--kernelbench-root", args.kernelbench_root])
            if args.timing_method is not None:
                command.extend(["--timing-method", args.timing_method])

            completed = run_subprocess_capture(
                command,
                env=isolated_gpu_environment(device_selector=lease.device_selector),
            )
            write_text(stdout_path, completed.stdout)
            write_text(stderr_path, completed.stderr)
            if workspace is not None:
                write_text(
                    _workspace_sample_artifact(workspace, sample_id, ".stdout.txt"),
                    completed.stdout,
                )
                write_text(
                    _workspace_sample_artifact(workspace, sample_id, ".stderr.txt"),
                    completed.stderr,
                )
            payload["gpu_id"] = lease.slot_id
            payload["gpu_device_selector"] = lease.device_selector
            payload["gpu_visible_devices"] = lease.isolated_visible_devices
            payload["gpu_logical_id"] = lease.logical_gpu_id
            payload["gpu_selector_source"] = lease.selector_source
            payload["gpu_wait_seconds"] = lease.wait_seconds

        if completed.returncode != 0:
            stderr_ref = _solver_artifact_reference(
                workspace=workspace,
                sample_id=sample_id,
                archive_path=stderr_path,
                problem_archive_root=problem_archive_root,
                suffix=".stderr.txt",
            )
            raise RuntimeError(
                "Candidate evaluation subprocess failed "
                f"(return code {completed.returncode}); see {stderr_ref}.\n"
                f"stderr excerpt:\n{excerpt(completed.stderr or completed.stdout)}"
            )
        if not runner_output_path.exists():
            raise RuntimeError(
                f"Candidate evaluation subprocess produced no result payload at {runner_output_path}."
            )
        result = load_json_object(runner_output_path)
        payload["status"] = "succeeded"
        payload["updated_at"] = now_iso()
        payload["result"] = result
        payload["warnings"] = _result_warnings(result, workspace, stdout_text=completed.stdout)
    except Exception as exc:
        failure = exc
        if payload is None or sample_id is None or sample_json_path is None:
            raise
        payload["status"] = "failed"
        payload["updated_at"] = now_iso()
        payload["error"] = serialize_exception(exc)
    finally:
        if payload is not None and sample_json_path is not None:
            try:
                with lease_problem_artifacts(
                    run_name=args.run_name,
                    level=args.level,
                    problem_id=args.problem_id,
                    lease_name=lease_name,
                ) as artifact_lease:
                    payload["artifact_commit_wait_seconds"] = artifact_lease.wait_seconds
                    payload["updated_at"] = now_iso()
                    write_json(sample_json_path, payload)
                    clear_live_gpu_wait_marker(live_gpu_wait_marker)
                    live_gpu_wait_marker = None
                    if workspace is not None:
                        workspace_json_path = _workspace_sample_artifact(workspace, sample_id, ".json")
                        if workspace_json_path is not None:
                            write_json(workspace_json_path, _solver_visible_payload(payload, workspace))
                        write_goal_status_files(
                            run_name=args.run_name,
                            level=args.level,
                            problem_id=args.problem_id,
                            workspace=workspace,
                        )
            except Exception as exc:
                persist_failure = exc
                clear_live_gpu_wait_marker(live_gpu_wait_marker)
                live_gpu_wait_marker = None
        else:
            clear_live_gpu_wait_marker(live_gpu_wait_marker)
            live_gpu_wait_marker = None

    emit_json(_solver_visible_payload(payload, workspace))
    if failure is not None:
        if persist_failure is not None:
            print(
                f"warning: artifact persistence also failed for sample {sample_id}: {persist_failure}",
                file=sys.stderr,
            )
        raise SystemExit(
            f"Candidate evaluation failed for sample {sample_id}: {failure}"
        ) from failure
    if persist_failure is not None:
        raise SystemExit(
            f"Artifact persistence failed for sample {sample_id}: {persist_failure}"
        ) from persist_failure
