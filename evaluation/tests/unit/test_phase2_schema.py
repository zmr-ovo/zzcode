from dataclasses import FrozenInstanceError

import pytest

from zzcode.evaluation import (
    AgentRunResult,
    AgentRunStatus,
    EvaluationResult,
    EvaluationStage,
    FailureCategory,
    FailureRecord,
    FailureType,
    ResolvedStatus,
    RunManifest,
    RunStatus,
    SchemaValidationError,
    classify_failure,
    make_failure,
)


STARTED = "2026-08-12T08:00:00+00:00"
COMPLETED = "2026-08-12T08:01:00+00:00"


def _manifest(**overrides):
    values = {
        "run_id": "20260812T080000000000Z-1234abcd",
        "dataset_name": "zzcode-bench-v1",
        "dataset_digest": "sha256:" + "a" * 64,
        "split": "dev",
        "agent_commit": "abcdef1234567",
        "provider": "openai-compatible",
        "model_name_or_path": "provider/model",
        "environment_id": "zzcode-py313-v1",
        "started_at": STARTED,
        "task_count": 2,
        "model_parameters": {"temperature": 0.0},
        "resource_limits": {"timeout_seconds": 900},
        "environment": {"os": "linux", "architecture": "arm64"},
    }
    values.update(overrides)
    return RunManifest(**values)


def test_run_manifest_round_trip_and_status_transition():
    manifest = _manifest()
    running = manifest.with_status(RunStatus.RUNNING)
    completed = running.with_status(RunStatus.COMPLETED, COMPLETED)

    assert RunManifest.from_dict(completed.to_dict()) == completed
    assert completed.status == RunStatus.COMPLETED
    assert completed.completed_at == COMPLETED


def test_run_manifest_rejects_invalid_digest_and_terminal_without_timestamp():
    with pytest.raises(SchemaValidationError, match="dataset_digest"):
        _manifest(dataset_digest="sha256:bad")

    with pytest.raises(SchemaValidationError, match="completed_at"):
        _manifest(status=RunStatus.FAILED)


@pytest.mark.parametrize("secret_key", ["api_key", "Authorization", "client_secret", "password"])
def test_run_manifest_rejects_secret_bearing_configuration(secret_key):
    with pytest.raises(SchemaValidationError, match="secret-bearing"):
        _manifest(model_parameters={secret_key: "must-not-be-persisted"})


def test_failure_category_is_derived_and_mismatch_is_rejected():
    assert classify_failure(FailureType.AGENT_TIMEOUT) == FailureCategory.AGENT_ERROR
    assert classify_failure(FailureType.INFRASTRUCTURE_ERROR) == FailureCategory.INFRA_ERROR
    assert classify_failure(FailureType.INVALID_DATASET) == FailureCategory.DATASET_ERROR

    with pytest.raises(SchemaValidationError, match="must use category"):
        FailureRecord(
            category=FailureCategory.AGENT_ERROR,
            failure_type=FailureType.INFRASTRUCTURE_ERROR,
            stage=EvaluationStage.HARNESS,
            message="Docker daemon unavailable",
        )


def test_failure_details_reject_secret_bearing_fields():
    with pytest.raises(SchemaValidationError, match="secret-bearing"):
        make_failure(
            FailureType.INFRASTRUCTURE_ERROR,
            EvaluationStage.HARNESS,
            "provider initialization failed",
            details={"api_key": "must-not-be-persisted"},
        )


def test_agent_failure_is_serializable_and_keeps_error_owner():
    failure = make_failure(
        FailureType.AGENT_TIMEOUT,
        EvaluationStage.AGENT,
        "Agent exceeded 900 seconds",
        retryable=True,
        details={"timeout_seconds": 900},
    )
    result = AgentRunResult(
        instance_id="ZZCODE-BUG-001",
        status=AgentRunStatus.FAILED,
        started_at=STARTED,
        completed_at=COMPLETED,
        duration_seconds=900,
        failure=failure,
    )

    restored = AgentRunResult.from_dict(result.to_dict())

    assert restored == result
    assert restored.failure.category == FailureCategory.AGENT_ERROR


def test_agent_result_rejects_inconsistent_lifecycle():
    with pytest.raises(SchemaValidationError, match="requires failure"):
        AgentRunResult(
            instance_id="ZZCODE-BUG-001",
            status=AgentRunStatus.FAILED,
            started_at=STARTED,
            completed_at=COMPLETED,
        )

    with pytest.raises(SchemaValidationError, match="cannot be earlier"):
        AgentRunResult(
            instance_id="ZZCODE-BUG-001",
            status=AgentRunStatus.COMPLETED,
            started_at=COMPLETED,
            completed_at=STARTED,
        )


def test_evaluation_result_allows_not_graded_phase2_state():
    result = EvaluationResult(
        instance_id="ZZCODE-BUG-001",
        resolved_status=ResolvedStatus.NOT_GRADED,
        agent_completed=True,
        patch_generated=True,
        patch_applied=False,
        tests_completed=False,
        metrics={"duration_seconds": 1.25},
    )

    assert EvaluationResult.from_dict(result.to_dict()) == result


def test_evaluation_result_rejects_invalid_funnel_and_wrong_error_category():
    with pytest.raises(SchemaValidationError, match="patch_applied requires"):
        EvaluationResult(
            instance_id="ZZCODE-BUG-001",
            resolved_status=ResolvedStatus.NOT_GRADED,
            agent_completed=True,
            patch_generated=False,
            patch_applied=True,
            tests_completed=False,
        )

    infra_failure = make_failure(
        FailureType.INFRASTRUCTURE_ERROR,
        EvaluationStage.HARNESS,
        "Docker failed",
    )
    with pytest.raises(SchemaValidationError, match="requires AGENT_ERROR"):
        EvaluationResult(
            instance_id="ZZCODE-BUG-001",
            resolved_status=ResolvedStatus.AGENT_ERROR,
            agent_completed=False,
            patch_generated=False,
            patch_applied=False,
            tests_completed=False,
            failure=infra_failure,
            completed_at=COMPLETED,
        )


def test_schema_models_are_frozen():
    manifest = _manifest()
    with pytest.raises(FrozenInstanceError):
        manifest.run_id = "changed"
