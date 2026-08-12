"""Inference orchestration kept independent from private grading inputs."""

from __future__ import annotations

from pathlib import Path

from ..errors import ArtifactError
from ..execution.workspace import WorkspaceManager
from ..failures import make_failure
from ..schema import AgentRunResult, TaskInstance
from ..status import AgentRunStatus, EvaluationStage, FailureType
from .adapter import AgentAdapter
from .models import AgentRunConfig, InferenceOutcome


class InferenceRunner:
    def __init__(self, workspace_manager: WorkspaceManager, adapter: AgentAdapter) -> None:
        self.workspace_manager = workspace_manager
        self.adapter = adapter

    def run(
        self,
        task: TaskInstance,
        config: AgentRunConfig,
        artifact_dir: Path,
    ) -> InferenceOutcome:
        try:
            workspace = self.workspace_manager.create_inference(task)
        except ArtifactError as exc:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).isoformat()
            failure = make_failure(
                FailureType.INFRASTRUCTURE_ERROR,
                EvaluationStage.WORKSPACE,
                str(exc),
                retryable=True,
            )
            result = AgentRunResult(
                instance_id=task.instance_id,
                status=AgentRunStatus.FAILED,
                started_at=now,
                completed_at=now,
                failure=failure,
                metadata={"provider": config.provider, "model": config.model},
            )
            # No real workspace exists, but the outcome contract retains a
            # deterministic intended path for diagnostics.
            workspace = (
                self.workspace_manager.root / task.instance_id / "inference"
            ).resolve()
            return InferenceOutcome(task, workspace, result, None, None)
        return self.adapter.run(task, workspace, config, artifact_dir)
