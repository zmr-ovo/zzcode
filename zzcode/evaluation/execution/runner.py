"""Phase 6 end-to-end vertical-slice evaluation pipeline."""

from __future__ import annotations

import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from ..dataset import EvaluationDataset
from ..failures import make_failure
from ..inference.adapter import AgentAdapter
from ..inference.models import AgentRunConfig, InferenceOutcome
from ..inference.runner import InferenceRunner
from ..reporting.artifacts import ArtifactStore, RunPaths, generate_run_id
from ..schema import AgentRunResult, EvaluationResult, RunManifest, TaskInstance
from ..serialization import write_json_atomic
from ..status import (
    AgentRunStatus,
    EvaluationStage,
    FailureCategory,
    FailureType,
    ResolvedStatus,
    RunStatus,
)
from .local_harness import LocalGradingHarness, LocalGradingRun
from .models import ResourceLimits
from .test_executor import TestExecutor
from .workspace import WorkspaceManager


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit(repository: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"cannot resolve Agent commit: {result.stderr.strip()}")
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if status.returncode != 0:
        raise ValueError(f"cannot inspect Agent worktree: {status.stderr.strip()}")
    if status.stdout:
        raise ValueError(
            "formal evaluation requires a clean Agent repository; commit or stash changes first"
        )
    return result.stdout.strip()


def _limits_payload(limits: ResourceLimits) -> dict[str, int | float]:
    return {
        "cpus": float(limits.cpus),
        "memory_mb": limits.memory_mb,
        "pids_limit": limits.pids_limit,
        "tmpfs_mb": limits.tmpfs_mb,
    }


def _test_group_payload(run) -> dict | None:
    if run is None:
        return None
    return {
        "returncode": run.returncode,
        "timed_out": run.timed_out,
        "duration_seconds": run.duration_seconds,
        "image_digest": run.image_digest,
        "result": run.result.to_dict(),
        "failure": run.failure.to_dict() if run.failure else None,
    }


def _validation_payload(kind: str, run: LocalGradingRun) -> dict:
    return {
        "instance_id": run.instance_id,
        "kind": kind,
        "resolved_status": run.decision.resolved_status.value,
        "tests_completed": run.decision.tests_completed,
        "fail_to_pass_rate": run.decision.fail_to_pass_rate,
        "pass_to_pass_rate": run.decision.pass_to_pass_rate,
        "patch_applied": bool(run.patch_apply and run.patch_apply.applied),
        "safety": run.safety.to_dict(),
        "fail_to_pass": _test_group_payload(run.fail_to_pass),
        "pass_to_pass": _test_group_payload(run.pass_to_pass),
        "failure": run.decision.failure.to_dict() if run.decision.failure else None,
    }


def _null_is_valid(run: LocalGradingRun) -> bool:
    return (
        run.decision.tests_completed
        and run.decision.resolved_status in {ResolvedStatus.NO, ResolvedStatus.PARTIAL}
        and run.decision.pass_to_pass_rate == 1.0
        and run.decision.fail_to_pass_rate is not None
        and run.decision.fail_to_pass_rate < 1.0
    )


def _gold_is_valid(run: LocalGradingRun) -> bool:
    return run.decision.resolved_status == ResolvedStatus.FULL


class VerticalSliceRunner:
    """Run Null, Gold x N, real Agent inference, Docker grading, and reports."""

    def __init__(
        self,
        *,
        dataset: EvaluationDataset,
        repositories: Mapping[str, Path],
        workspace_root: Path,
        artifact_root: Path,
        test_executor: TestExecutor,
        adapter: AgentAdapter,
        environment_image: str,
        image_digest: str | None,
        resource_limits: ResourceLimits,
        gold_repetitions: int = 3,
        test_timeout_seconds: float = 120.0,
    ) -> None:
        if gold_repetitions < 1:
            raise ValueError("gold_repetitions must be positive")
        if test_timeout_seconds <= 0:
            raise ValueError("test_timeout_seconds must be positive")
        self.dataset = dataset
        self.repositories = {name: Path(path).resolve() for name, path in repositories.items()}
        self.workspace_root = Path(workspace_root).resolve()
        self.artifact_store = ArtifactStore(artifact_root)
        self.test_executor = test_executor
        self.adapter = adapter
        self.environment_image = environment_image
        self.image_digest = image_digest
        self.resource_limits = resource_limits
        self.gold_repetitions = gold_repetitions
        self.test_timeout_seconds = test_timeout_seconds

    def run(
        self,
        agent_config: AgentRunConfig,
        *,
        run_id: str | None = None,
        agent_commit: str | None = None,
    ) -> RunPaths:
        tasks = self.dataset.tasks()
        if not tasks:
            raise ValueError("selected split has no tasks")
        environment_ids = {task.environment_id for task in tasks}
        if len(environment_ids) != 1:
            raise ValueError("all tasks in one run must use the same environment_id")
        repository = next(iter(self.repositories.values()))
        manifest = RunManifest(
            run_id=run_id or generate_run_id(),
            dataset_name=self.dataset.public_root.name,
            dataset_digest=self.dataset.digest(),
            split=self.dataset.split,
            agent_commit=agent_commit or _git_commit(repository),
            provider=agent_config.provider,
            model_name_or_path=agent_config.model,
            environment_id=next(iter(environment_ids)),
            started_at=_now(),
            task_count=len(tasks),
            model_parameters={
                "temperature": float(agent_config.temperature),
                "top_p": float(agent_config.top_p),
                "max_steps": agent_config.max_steps,
                "max_new_tokens": agent_config.max_new_tokens,
                "timeout_seconds": float(agent_config.timeout_seconds),
                "provider_timeout_seconds": float(agent_config.provider_timeout_seconds),
                "base_url": agent_config.base_url,
                "host": agent_config.host,
            },
            resource_limits=_limits_payload(self.resource_limits),
            environment={
                "image": self.environment_image,
                "network": "none",
                "gold_repetitions": self.gold_repetitions,
                "test_timeout_seconds": self.test_timeout_seconds,
            },
            image_digest=self.image_digest,
        )
        paths = self.artifact_store.start_run(manifest)
        self.artifact_store.update_manifest(paths, manifest.with_status(RunStatus.RUNNING))
        try:
            task_rows = [self._run_task(paths, task, agent_config) for task in tasks]
            results = self._results_payload(manifest.run_id, task_rows)
            self.artifact_store.write_results(paths, results)
            self.artifact_store.update_manifest(
                paths,
                manifest.with_status(RunStatus.COMPLETED, _now()),
            )
            return paths
        except BaseException as exc:
            failure_type = (
                FailureType.HARNESS_INTERRUPTED
                if isinstance(exc, (KeyboardInterrupt, SystemExit))
                else FailureType.INFRASTRUCTURE_ERROR
            )
            failure = make_failure(
                failure_type,
                EvaluationStage.HARNESS,
                f"vertical-slice run stopped: {type(exc).__name__}: {exc}",
                retryable=not isinstance(exc, (KeyboardInterrupt, SystemExit)),
            )
            self.artifact_store.write_run_failure(paths, failure, recorded_at=_now())
            terminal = (
                RunStatus.INTERRUPTED
                if isinstance(exc, (KeyboardInterrupt, SystemExit))
                else RunStatus.FAILED
            )
            self.artifact_store.update_manifest(paths, manifest.with_status(terminal, _now()))
            raise

    def _workspace_manager(self, run_paths: RunPaths, stage: str) -> WorkspaceManager:
        root = self.workspace_root / run_paths.root.name / stage
        return WorkspaceManager(root, self.repositories)

    def _grading_harness(self, run_paths: RunPaths, stage: str) -> LocalGradingHarness:
        return LocalGradingHarness(
            self._workspace_manager(run_paths, stage),
            test_executor=self.test_executor,
        )

    def _run_task(
        self,
        paths: RunPaths,
        task: TaskInstance,
        agent_config: AgentRunConfig,
    ) -> dict:
        instance_dir = paths.instance_dir(task.instance_id)
        instance_dir.mkdir(parents=True, exist_ok=True)
        private = self.dataset.private_spec(task.instance_id)

        null_dir = instance_dir / "validation" / "null"
        null = self._grading_harness(paths, "validation-null").run(
            task,
            private,
            None,
            null_dir,
            timeout_seconds=self.test_timeout_seconds,
        )
        null_payload = _validation_payload("null", null)
        write_json_atomic(null_dir / "validation.json", null_payload, overwrite=False)

        gold_patch = private.gold_patch_path.read_text(encoding="utf-8")
        gold_runs: list[LocalGradingRun] = []
        gold_payloads: list[dict] = []
        for repetition in range(1, self.gold_repetitions + 1):
            kind = f"gold-{repetition}"
            artifact_dir = instance_dir / "validation" / kind
            run = self._grading_harness(paths, f"validation-{kind}").run(
                task,
                private,
                gold_patch,
                artifact_dir,
                timeout_seconds=self.test_timeout_seconds,
            )
            payload = _validation_payload(kind, run)
            write_json_atomic(artifact_dir / "validation.json", payload, overwrite=False)
            gold_runs.append(run)
            gold_payloads.append(payload)

        null_passed = _null_is_valid(null)
        gold_passed = all(_gold_is_valid(run) for run in gold_runs)
        gate_passed = null_passed and gold_passed
        gate_payload = {
            "instance_id": task.instance_id,
            "passed": gate_passed,
            "null_passed": null_passed,
            "gold_passed": gold_passed,
            "gold_repetitions": self.gold_repetitions,
            "null": null_payload,
            "gold": gold_payloads,
        }
        write_json_atomic(instance_dir / "validation" / "gate.json", gate_payload, overwrite=False)

        if gate_passed:
            try:
                outcome = InferenceRunner(
                    self._workspace_manager(paths, "inference"),
                    self.adapter,
                ).run(task, agent_config, instance_dir / "agent")
            except Exception as exc:
                # A broken Adapter must become a per-task infrastructure result
                # instead of dropping the remaining batch and its artifacts.
                now = _now()
                failure = make_failure(
                    FailureType.INFRASTRUCTURE_ERROR,
                    EvaluationStage.AGENT,
                    f"Agent Adapter raised {type(exc).__name__}",
                    retryable=True,
                )
                agent_result = AgentRunResult(
                    instance_id=task.instance_id,
                    status=AgentRunStatus.FAILED,
                    started_at=now,
                    completed_at=now,
                    failure=failure,
                    metadata={"provider": agent_config.provider, "model": agent_config.model},
                )
                outcome = InferenceOutcome(
                    task,
                    self.workspace_root
                    / paths.root.name
                    / "inference"
                    / task.instance_id
                    / "inference",
                    agent_result,
                    None,
                    None,
                )
            result = self._persist_and_grade_agent(paths, task, private, outcome)
        else:
            outcome, result = self._persist_dataset_gate_failure(paths, task, gate_payload)

        return {
            "instance_id": task.instance_id,
            "dataset_gate_passed": gate_passed,
            "validation": {
                "null_status": null.decision.resolved_status.value,
                "gold_statuses": [run.decision.resolved_status.value for run in gold_runs],
            },
            "agent_status": outcome.agent_result.status.value,
            "resolved_status": result.resolved_status.value,
            "fail_to_pass_rate": result.fail_to_pass_rate,
            "pass_to_pass_rate": result.pass_to_pass_rate,
            "failure_category": result.failure.category.value if result.failure else None,
        }

    def _persist_and_grade_agent(
        self,
        paths: RunPaths,
        task: TaskInstance,
        private,
        outcome: InferenceOutcome,
    ) -> EvaluationResult:
        self.artifact_store.write_agent_result(paths, outcome.agent_result)
        if outcome.prediction is not None and outcome.patch is not None:
            self.artifact_store.write_patch(paths, task.instance_id, outcome.patch)
            self.artifact_store.append_prediction(paths, outcome.prediction)
            grading = self._grading_harness(paths, "agent-grading").run(
                task,
                private,
                outcome.patch,
                paths.instance_dir(task.instance_id) / "grading",
                timeout_seconds=self.test_timeout_seconds,
            )
            if grading.evaluation_result is None:
                raise RuntimeError("Agent patch grading produced no EvaluationResult")
            result = grading.evaluation_result
        else:
            result = self._agent_failure_result(task, outcome.agent_result)
        self.artifact_store.write_instance_result(paths, result)
        return result

    def _persist_dataset_gate_failure(
        self,
        paths: RunPaths,
        task: TaskInstance,
        gate_payload: dict,
    ) -> tuple[InferenceOutcome, EvaluationResult]:
        now = _now()
        failure = make_failure(
            FailureType.INVALID_DATASET,
            EvaluationStage.DATASET,
            "Repo Task failed Null/Gold validity gates; Agent inference was not started",
            details={
                "null_passed": gate_payload["null_passed"],
                "gold_passed": gate_payload["gold_passed"],
            },
        )
        agent_result = AgentRunResult(
            instance_id=task.instance_id,
            status=AgentRunStatus.FAILED,
            started_at=now,
            completed_at=now,
            failure=failure,
            metadata={"skipped": True, "reason": "dataset_gate_failed"},
        )
        outcome = InferenceOutcome(
            task,
            self.workspace_root / paths.root.name / "inference" / task.instance_id / "inference",
            agent_result,
            None,
            None,
        )
        self.artifact_store.write_agent_result(paths, agent_result)
        result = EvaluationResult(
            instance_id=task.instance_id,
            resolved_status=ResolvedStatus.DATASET_ERROR,
            agent_completed=False,
            patch_generated=False,
            patch_applied=False,
            tests_completed=False,
            failure=failure,
            completed_at=now,
            metrics={"dataset_gate_passed": False},
        )
        self.artifact_store.write_instance_result(paths, result)
        return outcome, result

    @staticmethod
    def _agent_failure_result(task: TaskInstance, agent_result: AgentRunResult) -> EvaluationResult:
        failure = agent_result.failure
        if failure is None:
            raise RuntimeError("Agent produced neither a patch nor a failure record")
        if failure.category == FailureCategory.DATASET_ERROR:
            resolved = ResolvedStatus.DATASET_ERROR
        elif failure.category == FailureCategory.INFRA_ERROR:
            resolved = ResolvedStatus.INFRA_ERROR
        else:
            resolved = ResolvedStatus.AGENT_ERROR
        return EvaluationResult(
            instance_id=task.instance_id,
            resolved_status=resolved,
            agent_completed=agent_result.status == AgentRunStatus.COMPLETED,
            patch_generated=agent_result.patch_generated,
            patch_applied=False,
            tests_completed=False,
            failure=failure,
            completed_at=_now(),
            metrics={
                "agent_duration_seconds": agent_result.duration_seconds,
                "tool_steps": agent_result.tool_steps,
                "token_usage": agent_result.token_usage,
            },
        )

    @staticmethod
    def _results_payload(run_id: str, rows: list[dict]) -> dict:
        statuses = Counter(row["resolved_status"] for row in rows)
        resolved = statuses[ResolvedStatus.FULL.value]
        total = len(rows)
        return {
            "run_id": run_id,
            "completed_at": _now(),
            "summary": {
                "task_count": total,
                "dataset_gate_passed": sum(row["dataset_gate_passed"] for row in rows),
                "resolved": resolved,
                "resolution_rate": resolved / total if total else 0.0,
                "status_counts": dict(sorted(statuses.items())),
            },
            "tasks": rows,
        }
