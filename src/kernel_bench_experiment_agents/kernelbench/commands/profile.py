"""Implement the Nsight Compute profiling command used by the workspace profile wrapper.

This module freezes the candidate, records durable profile metadata, runs NCU, and mirrors the latest results back into the workspace.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from kernel_bench_experiment_agents.workspace.archive import archive_problem_profiles_dir, next_archive_profile_index
from kernel_bench_experiment_agents.kernelbench.candidate.contract import CANDIDATE_FILENAME
from kernel_bench_experiment_agents.kernelbench.candidate.snapshot import read_validated_candidate_source, write_profile_candidate_snapshot
from kernel_bench_experiment_agents.runtime.common import emit_json
from kernel_bench_experiment_agents.agent_contract.goal_status import write_goal_status_files
from kernel_bench_experiment_agents.runtime.live_gpu_wait import (
    clear_live_gpu_wait_marker,
    create_live_gpu_wait_marker,
    settle_live_gpu_wait_marker,
)
from kernel_bench_experiment_agents.runtime.gpu_pool import isolated_gpu_environment, lease_gpu_slot, lease_problem_artifacts
from kernel_bench_experiment_agents.kernelbench.profiling.summary import summarize_ncu_raw_csv
from kernel_bench_experiment_agents.runtime.project import archive_problem_dir, now_iso, relative_path_within, write_json, write_text
from kernel_bench_experiment_agents.runtime.subprocess_tools import excerpt, run_subprocess_capture, serialize_exception
from kernel_bench_experiment_agents.workspace.paths import (
    latest_workspace_profile_paths,
    validate_workspace_assignment,
    workspace_candidate_path,
    workspace_path,
    workspace_profiles_dir,
    workspace_relpath,
)


def _workspace_profile_local_paths(workspace: Path, profile_name: str) -> dict[str, Path]:
    profile_base = workspace_profiles_dir(workspace) / profile_name
    return {
        "details_path": profile_base.with_suffix(".details.txt"),
        "summary_path": profile_base.with_suffix(".summary.txt"),
        "stdout_path": profile_base.with_suffix(".stdout.txt"),
        "stderr_path": profile_base.with_suffix(".stderr.txt"),
        "json_path": profile_base.with_suffix(".json"),
    }


def _write_workspace_profile_mirrors(
    *,
    workspace: Path,
    profile_name: str,
    payload: dict[str, Any],
    completed_stdout: str,
    completed_stderr: str,
    details_stdout: str,
    summary_text: str,
) -> dict[str, Path]:
    local_paths = _workspace_profile_local_paths(workspace, profile_name)
    latest_paths = latest_workspace_profile_paths(workspace)

    write_text(local_paths["details_path"], details_stdout)
    write_text(local_paths["summary_path"], summary_text)
    write_text(local_paths["stdout_path"], completed_stdout)
    write_text(local_paths["stderr_path"], completed_stderr)
    write_json(local_paths["json_path"], payload)

    write_text(latest_paths["details"], details_stdout)
    write_text(latest_paths["summary"], summary_text)
    write_text(latest_paths["stdout"], completed_stdout)
    write_text(latest_paths["stderr"], completed_stderr)
    write_json(latest_paths["json"], payload)

    return {**local_paths, **{f"latest_{key}": value for key, value in latest_paths.items()}}


def _workspace_candidate_reference(candidate_path: Path, workspace: Path | None) -> str:
    if workspace is not None:
        return workspace_relpath(candidate_path, workspace)
    return candidate_path.name


def command_profile_ncu(args: argparse.Namespace) -> None:
    """Profile one frozen candidate snapshot with Nsight Compute and persist the result."""
    candidate_path = Path(args.candidate).resolve()
    workspace: Path | None = None
    if args.workspace:
        workspace = workspace_path(args.workspace)
        validate_workspace_assignment(
            workspace,
            run_name=args.run_name,
            level=args.level,
            problem_id=args.problem_id,
        )
        expected_candidate_path = workspace_candidate_path(workspace)
        if candidate_path != expected_candidate_path:
            raise SystemExit(
                f"Only {CANDIDATE_FILENAME} may be profiled from the problem workspace."
            )

    candidate_src = read_validated_candidate_source(candidate_path)
    lease_name = f"profile:{args.run_name}:level_{args.level}:problem_{args.problem_id}"
    problem_archive_root = archive_problem_dir(args.run_name, args.level, args.problem_id)
    profiles_dir = archive_problem_profiles_dir(args.run_name, args.level, args.problem_id)

    profile_index: int | None = None
    profile_name: str | None = None
    report_prefix: Path | None = None
    report_path: Path | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    details_path: Path | None = None
    summary_path: Path | None = None
    profile_json_path: Path | None = None
    candidate_ref = _workspace_candidate_reference(candidate_path, workspace)
    archive_report_prefix: str | None = None
    archive_candidate_path: str | None = None
    reservation_wait_seconds: float | None = None
    snapshot_path: Path | None = None
    payload: dict[str, Any] | None = None
    emit_payload: dict[str, Any] | None = None
    completed = None
    details_completed = None
    raw_csv_completed = None
    summary_text: str | None = None
    gpu_id = None
    gpu_device_selector = None
    gpu_visible_devices = None
    gpu_logical_id = None
    gpu_selector_source = None
    gpu_wait_seconds = None
    live_gpu_wait_marker = None
    failure: Exception | None = None
    persist_failure: Exception | None = None

    with lease_problem_artifacts(
        run_name=args.run_name,
        level=args.level,
        problem_id=args.problem_id,
        lease_name=f"{lease_name}:reserve",
    ) as artifact_lease:
        reservation_wait_seconds = artifact_lease.wait_seconds
        profile_index = next_archive_profile_index(args.run_name, args.level, args.problem_id)
        profile_name = f"profile_{profile_index}"
        report_prefix = profiles_dir / profile_name
        report_path = Path(str(report_prefix) + ".ncu-rep")
        stdout_path = report_prefix.with_suffix(".stdout.txt")
        stderr_path = report_prefix.with_suffix(".stderr.txt")
        details_path = report_prefix.with_suffix(".details.txt")
        summary_path = report_prefix.with_suffix(".summary.txt")
        profile_json_path = report_prefix.with_suffix(".json")
        archive_report_prefix = relative_path_within(report_prefix, problem_archive_root)
        snapshot_path = write_profile_candidate_snapshot(
            profiles_dir=profiles_dir,
            profile_name=profile_name,
            candidate_src=candidate_src,
        )
        archive_candidate_path = relative_path_within(snapshot_path, problem_archive_root)
        payload = {
            "status": "started",
            "timestamp": now_iso(),
            "run_name": args.run_name,
            "level": args.level,
            "problem_id": args.problem_id,
            "profile_id": profile_index,
            "profile_name": profile_name,
            "sample_id": args.sample_id,
            "candidate_path": candidate_ref,
            "archive_candidate_path": archive_candidate_path,
            "artifact_reservation_wait_seconds": reservation_wait_seconds,
            "artifact_commit_wait_seconds": None,
            "error": None,
        }
        write_json(profile_json_path, payload)

    try:
        # Keep goal-status budget accounting honest while this profiler wrapper is
        # queued for a GPU lease by recording the in-flight wait immediately.
        live_gpu_wait_marker = create_live_gpu_wait_marker(
            run_name=args.run_name,
            level=args.level,
            problem_id=args.problem_id,
            operation="profile_ncu",
            requested_gpu=args.gpu_id,
            num_gpu_slots=args.num_gpu_slots,
        )
        with lease_gpu_slot(
            num_slots=args.num_gpu_slots,
            requested_slot=args.gpu_id,
            lease_name=lease_name,
        ) as lease:
            settle_live_gpu_wait_marker(live_gpu_wait_marker, wait_seconds=lease.wait_seconds)

            isolated_env = isolated_gpu_environment(device_selector=lease.device_selector)
            command = [
                "ncu",
                "--set",
                args.ncu_set,
                "--force-overwrite",
                "--target-processes",
                "all",
                "--export",
                str(report_prefix),
                sys.executable,
                "-m",
                "kernel_bench_experiment_agents.kernelbench.profiling.runner",
                "--candidate",
                str(snapshot_path),
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
                "--sample-label",
                profile_name,
                "--precision",
                args.precision,
            ]
            if args.kernelbench_root:
                command.extend(["--kernelbench-root", args.kernelbench_root])
            completed = run_subprocess_capture(command, env=isolated_env)
            gpu_id = lease.slot_id
            gpu_device_selector = lease.device_selector
            gpu_visible_devices = lease.isolated_visible_devices
            gpu_logical_id = lease.logical_gpu_id
            gpu_selector_source = lease.selector_source
            gpu_wait_seconds = lease.wait_seconds

        write_text(stdout_path, completed.stdout)
        write_text(stderr_path, completed.stderr)

        details_command = [
            "ncu",
            "--import",
            str(report_path),
            "--page",
            "details",
        ]
        details_completed = run_subprocess_capture(details_command)
        write_text(details_path, details_completed.stdout)

        raw_csv_command = [
            "ncu",
            "--import",
            str(report_path),
            "--page",
            "raw",
            "--csv",
        ]
        raw_csv_completed = run_subprocess_capture(raw_csv_command)
        summary_text = summarize_ncu_raw_csv(raw_csv_completed.stdout)
        write_text(summary_path, summary_text)

        if report_path.exists():
            report_path.unlink()

        profile_ok = (
            completed.returncode == 0
            and details_completed.returncode == 0
            and raw_csv_completed.returncode == 0
            and bool(details_completed.stdout.strip())
            and bool(raw_csv_completed.stdout.strip())
        )
        payload = {
            "status": "succeeded" if profile_ok else "failed",
            "timestamp": now_iso(),
            "run_name": args.run_name,
            "level": args.level,
            "problem_id": args.problem_id,
            "profile_id": profile_index,
            "profile_name": profile_name,
            "sample_id": args.sample_id,
            "candidate_path": candidate_ref,
            "archive_candidate_path": archive_candidate_path,
            "details_path": relative_path_within(details_path, problem_archive_root),
            "summary_path": relative_path_within(summary_path, problem_archive_root),
            "stdout_path": relative_path_within(stdout_path, problem_archive_root),
            "stderr_path": relative_path_within(stderr_path, problem_archive_root),
            "returncode": completed.returncode,
            "precision": args.precision,
            "command": [
                "ncu",
                "--set",
                args.ncu_set,
                "--force-overwrite",
                "--target-processes",
                "all",
                "--export",
                archive_report_prefix,
                "python",
                "-m",
                "kernel_bench_experiment_agents.kernelbench.profiling.runner",
                "--candidate",
                archive_candidate_path,
                "--level",
                str(args.level),
                "--problem-id",
                str(args.problem_id),
                "--dataset-src",
                args.dataset_src,
                "--gpu-id",
                str(gpu_logical_id),
                "--run-name",
                args.run_name,
                "--sample-label",
                profile_name,
                "--precision",
                args.precision,
            ],
            "details_command": ["ncu", "--import", f"{archive_report_prefix}.ncu-rep", "--page", "details"],
            "details_returncode": details_completed.returncode,
            "details_error_excerpt": excerpt(details_completed.stderr),
            "raw_summary_source": "generated from a transient ncu --page raw --csv export; the raw CSV is not archived",
            "raw_csv_returncode": raw_csv_completed.returncode,
            "raw_csv_error_excerpt": excerpt(raw_csv_completed.stderr),
            "gpu_id": gpu_id,
            "gpu_device_selector": gpu_device_selector,
            "gpu_visible_devices": gpu_visible_devices,
            "gpu_logical_id": gpu_logical_id,
            "gpu_selector_source": gpu_selector_source,
            "gpu_wait_seconds": gpu_wait_seconds,
            "artifact_reservation_wait_seconds": reservation_wait_seconds,
            "artifact_commit_wait_seconds": None,
            "error": None,
        }
        emit_payload = dict(payload)
    except Exception as exc:
        failure = exc
        if payload is not None:
            payload.update(
                {
                    "status": "failed",
                    "timestamp": now_iso(),
                    "gpu_id": gpu_id,
                    "gpu_device_selector": gpu_device_selector,
                    "gpu_visible_devices": gpu_visible_devices,
                    "gpu_logical_id": gpu_logical_id,
                    "gpu_selector_source": gpu_selector_source,
                    "gpu_wait_seconds": gpu_wait_seconds,
                    "artifact_reservation_wait_seconds": reservation_wait_seconds,
                    "artifact_commit_wait_seconds": None,
                    "error": serialize_exception(exc),
                }
            )
    finally:
        if payload is not None and profile_json_path is not None:
            try:
                with lease_problem_artifacts(
                    run_name=args.run_name,
                    level=args.level,
                    problem_id=args.problem_id,
                    lease_name=f"{lease_name}:commit",
                ) as artifact_lease:
                    payload["artifact_commit_wait_seconds"] = artifact_lease.wait_seconds
                    write_json(profile_json_path, payload)
                    clear_live_gpu_wait_marker(live_gpu_wait_marker)
                    live_gpu_wait_marker = None

                    if workspace is not None:
                        if (
                            emit_payload is not None
                            and completed is not None
                            and details_completed is not None
                            and summary_text is not None
                        ):
                            mirror_paths = _write_workspace_profile_mirrors(
                                workspace=workspace,
                                profile_name=profile_name,
                                payload=payload,
                                completed_stdout=completed.stdout,
                                completed_stderr=completed.stderr,
                                details_stdout=details_completed.stdout,
                                summary_text=summary_text,
                            )
                            emit_payload = {
                                "timestamp": payload["timestamp"],
                                "run_name": args.run_name,
                                "level": args.level,
                                "problem_id": args.problem_id,
                                "profile_id": profile_index,
                                "profile_name": profile_name,
                                "sample_id": args.sample_id,
                                "candidate_path": workspace_relpath(candidate_path, workspace),
                                "details_path": workspace_relpath(mirror_paths["latest_details"], workspace),
                                "summary_path": workspace_relpath(mirror_paths["latest_summary"], workspace),
                                "stdout_path": workspace_relpath(mirror_paths["latest_stdout"], workspace),
                                "stderr_path": workspace_relpath(mirror_paths["latest_stderr"], workspace),
                                "profile_details_path": workspace_relpath(mirror_paths["details_path"], workspace),
                                "profile_summary_path": workspace_relpath(mirror_paths["summary_path"], workspace),
                                "profile_stdout_path": workspace_relpath(mirror_paths["stdout_path"], workspace),
                                "profile_stderr_path": workspace_relpath(mirror_paths["stderr_path"], workspace),
                                "returncode": completed.returncode,
                                "precision": args.precision,
                                "details_returncode": details_completed.returncode,
                                "raw_csv_returncode": raw_csv_completed.returncode,
                                "gpu_id": gpu_id,
                                "gpu_wait_seconds": gpu_wait_seconds,
                            }
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

    if persist_failure is not None:
        raise SystemExit(
            f"Failed to persist profiling metadata for {profile_name}: {persist_failure}"
        ) from persist_failure

    if failure is not None:
        raise SystemExit(
            f"ncu profiling failed before completion for {profile_name}: {failure}"
        ) from failure

    if completed.returncode != 0:
        raise SystemExit(
            "ncu profiling failed "
            f"(return code {completed.returncode}); see {stderr_path}"
        )
    if details_completed.returncode != 0:
        raise SystemExit(
            "ncu details export failed "
            f"(return code {details_completed.returncode}); details stderr: {excerpt(details_completed.stderr)}"
        )
    if raw_csv_completed.returncode != 0:
        raise SystemExit(
            "ncu raw metric export failed "
            f"(return code {raw_csv_completed.returncode}); stderr: {excerpt(raw_csv_completed.stderr)}"
        )
    if not details_completed.stdout.strip():
        raise SystemExit(
            f"ncu details export produced no readable output; see {details_path}"
        )
    if not raw_csv_completed.stdout.strip():
        raise SystemExit(
            "ncu raw metric export produced no readable output for summary generation"
        )
    emit_json(emit_payload)
