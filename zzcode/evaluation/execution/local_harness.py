"""Phase 3 local grading flow driven by manually supplied patches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..errors import ArtifactError, DatasetValidationError
from ..failures import make_failure
from ..grading.grader import grade_test_results
from ..grading.safety import SafetyPolicy, inspect_patch
from ..schema import EvaluationResult, PrivateTestSpec, TaskInstance
from ..status import EvaluationStage, FailureType, ResolvedStatus
from ..serialization import write_json_atomic, write_text_atomic
from .models import GradingDecision, PatchApplyResult, SafetyResult, TestRun
from .patch_applier import PatchApplier
from .test_executor import TestExecutor
from .workspace import WorkspaceManager


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LocalGradingRun:
    instance_id: str
    workspace: Path | None
    validation_kind: str
    safety: SafetyResult
    patch_apply: PatchApplyResult | None
    fail_to_pass: TestRun | None
    pass_to_pass: TestRun | None
    decision: GradingDecision
    evaluation_result: EvaluationResult | None


class LocalGradingHarness:
    def __init__(
        self,
        workspace_manager: WorkspaceManager,
        *,
        python_executable: Path | str | None = None,
        test_executor: TestExecutor | None = None,
        safety_policy: SafetyPolicy | None = None,
    ) -> None:
        if python_executable is not None and test_executor is not None:
            raise ValueError("provide python_executable or test_executor, not both")
        self.workspace_manager = workspace_manager
        self.patch_applier = PatchApplier()
        self.test_executor = test_executor or TestExecutor(python_executable)
        self.safety_policy = safety_policy or SafetyPolicy()

    def run(
        self,
        task: TaskInstance,
        private_spec: PrivateTestSpec,
        model_patch: str | None,
        artifact_dir: Path,
        *,
        timeout_seconds: float = 120,
    ) -> LocalGradingRun:
        if task.instance_id != private_spec.instance_id:
            raise DatasetValidationError(
                f"public/private instance mismatch: {task.instance_id} != {private_spec.instance_id}"
            )
        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        validation_kind = "null" if model_patch is None else "patch"
        empty_safety = SafetyResult(True, (), 0, 0)
        if model_patch is not None:
            write_text_atomic(artifact_dir / "patch.diff", model_patch, overwrite=False)
            if not model_patch.strip():
                patch_apply = self.patch_applier.apply(Path.cwd(), model_patch)
                decision = GradingDecision(
                    ResolvedStatus.AGENT_ERROR,
                    None,
                    None,
                    False,
                    patch_apply.failure,
                )
                write_json_atomic(
                    artifact_dir / "patch_apply.json",
                    patch_apply.to_dict(),
                    overwrite=False,
                )
                return LocalGradingRun(
                    task.instance_id,
                    None,
                    validation_kind,
                    empty_safety,
                    patch_apply,
                    None,
                    None,
                    decision,
                    self._evaluation_result(task, model_patch, False, decision),
                )
        try:
            workspace = self.workspace_manager.create_grading(task)
        except ArtifactError as exc:
            failure = make_failure(
                FailureType.INFRASTRUCTURE_ERROR,
                EvaluationStage.WORKSPACE,
                str(exc),
                retryable=True,
            )
            decision = GradingDecision(ResolvedStatus.INFRA_ERROR, None, None, False, failure)
            return LocalGradingRun(
                task.instance_id,
                None,
                validation_kind,
                empty_safety,
                None,
                None,
                None,
                decision,
                self._evaluation_result(task, model_patch, False, decision),
            )

        patch_apply = None
        safety = empty_safety
        if model_patch is not None:
            safety = inspect_patch(model_patch, self.safety_policy)
            write_json_atomic(artifact_dir / "safety.json", safety.to_dict(), overwrite=False)
            if not safety.passed:
                decision = grade_test_results(None, None, safety)
                return LocalGradingRun(
                    task.instance_id,
                    workspace,
                    validation_kind,
                    safety,
                    None,
                    None,
                    None,
                    decision,
                    self._evaluation_result(task, model_patch, False, decision),
                )
            patch_apply = self.patch_applier.apply(workspace, model_patch)
            write_json_atomic(
                artifact_dir / "patch_apply.json",
                patch_apply.to_dict(),
                overwrite=False,
            )
            if not patch_apply.applied:
                decision = GradingDecision(
                    ResolvedStatus.NO,
                    None,
                    None,
                    False,
                    patch_apply.failure,
                )
                return LocalGradingRun(
                    task.instance_id,
                    workspace,
                    validation_kind,
                    safety,
                    patch_apply,
                    None,
                    None,
                    decision,
                    self._evaluation_result(task, model_patch, False, decision),
                )

        try:
            self.test_executor.inject_test_patch(workspace, private_spec)
        except DatasetValidationError as exc:
            failure = make_failure(
                FailureType.INVALID_DATASET,
                EvaluationStage.DATASET,
                str(exc),
            )
            decision = GradingDecision(ResolvedStatus.DATASET_ERROR, None, None, False, failure)
            return LocalGradingRun(
                task.instance_id,
                workspace,
                validation_kind,
                safety,
                patch_apply,
                None,
                None,
                decision,
                self._evaluation_result(task, model_patch, bool(patch_apply and patch_apply.applied), decision),
            )
        f2p = self.test_executor.run_fail_to_pass(
            workspace,
            private_spec.fail_to_pass,
            timeout_seconds,
            artifact_dir,
        )
        p2p = self.test_executor.run_pass_to_pass(
            workspace,
            private_spec.pass_to_pass,
            timeout_seconds,
            artifact_dir,
        )
        execution_failure = f2p.failure or p2p.failure
        if execution_failure is not None:
            status = (
                ResolvedStatus.INFRA_ERROR
                if execution_failure.category.value == "INFRA_ERROR"
                else ResolvedStatus.NO
            )
            decision = GradingDecision(
                status,
                f2p.result.rate,
                p2p.result.rate,
                False,
                execution_failure,
            )
        else:
            decision = grade_test_results(f2p.result, p2p.result, safety)
        evaluation_result = None
        if model_patch is not None:
            evaluation_result = self._evaluation_result(
                task,
                model_patch,
                bool(patch_apply and patch_apply.applied),
                decision,
                test_runs=(f2p, p2p),
            )
        return LocalGradingRun(
            task.instance_id,
            workspace,
            validation_kind,
            safety,
            patch_apply,
            f2p,
            p2p,
            decision,
            evaluation_result,
        )

    @staticmethod
    def _evaluation_result(
        task: TaskInstance,
        model_patch: str | None,
        patch_applied: bool,
        decision: GradingDecision,
        *,
        test_runs: tuple[TestRun, TestRun] | None = None,
    ) -> EvaluationResult | None:
        if model_patch is None:
            return None
        tests_completed = decision.tests_completed and patch_applied
        f2p_rate = decision.fail_to_pass_rate if tests_completed else None
        p2p_rate = decision.pass_to_pass_rate if tests_completed else None
        metrics = {}
        if test_runs is not None:
            digests = {run.image_digest for run in test_runs if run.image_digest is not None}
            if len(digests) == 1:
                metrics["image_digest"] = next(iter(digests))
            metrics["test_duration_seconds"] = sum(
                run.duration_seconds for run in test_runs
            )
        return EvaluationResult(
            instance_id=task.instance_id,
            resolved_status=decision.resolved_status,
            agent_completed=True,
            patch_generated=bool(model_patch and model_patch.strip()),
            patch_applied=patch_applied,
            tests_completed=tests_completed,
            fail_to_pass_rate=f2p_rate,
            pass_to_pass_rate=p2p_rate,
            failure=decision.failure,
            completed_at=_now(),
            metrics=metrics,
        )
