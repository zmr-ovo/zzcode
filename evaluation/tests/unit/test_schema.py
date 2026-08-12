import pytest

from zzcode.evaluation import Prediction, SchemaValidationError, TaskInstance


def test_task_instance_round_trips_without_sharing_metadata():
    metadata = {"resource_limits": {"timeout_seconds": 900}}
    task = TaskInstance(
        instance_id="ZZCODE-BUG-001",
        repo="local/zzcode",
        base_commit="ABCDEF1234567",
        problem_statement="修复外部编辑后的旧摘要。",
        environment_id="zzcode-py313-v1",
        metadata=metadata,
    )

    metadata["resource_limits"]["timeout_seconds"] = 1
    restored = TaskInstance.from_dict(task.to_dict())

    assert restored == task
    assert restored.base_commit == "abcdef1234567"
    assert restored.metadata["resource_limits"]["timeout_seconds"] == 900


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("instance_id", "../escape"),
        ("base_commit", "not-a-commit"),
        ("problem_statement", "  "),
        ("environment_id", ""),
    ],
)
def test_task_instance_rejects_invalid_required_fields(field, value):
    data = {
        "instance_id": "ZZCODE-BUG-001",
        "repo": "local/zzcode",
        "base_commit": "abcdef1",
        "problem_statement": "Fix it.",
        "environment_id": "zzcode-py313-v1",
    }
    data[field] = value

    with pytest.raises(SchemaValidationError):
        TaskInstance.from_dict(data)


def test_task_instance_rejects_unknown_schema_fields():
    with pytest.raises(SchemaValidationError, match="unknown fields"):
        TaskInstance.from_dict(
            {
                "instance_id": "ZZCODE-BUG-001",
                "repo": "local/zzcode",
                "base_commit": "abcdef1",
                "problem_statement": "Fix it.",
                "environment_id": "zzcode-py313-v1",
                "gold_patch": "secret",
            }
        )


def test_prediction_uses_exact_swebench_compatible_fields():
    prediction = Prediction(
        instance_id="ZZCODE-BUG-001",
        model_name_or_path="openai/gpt-coding",
        model_patch="diff --git a/a.py b/a.py\n",
    )

    assert prediction.to_dict() == {
        "instance_id": "ZZCODE-BUG-001",
        "model_name_or_path": "openai/gpt-coding",
        "model_patch": "diff --git a/a.py b/a.py\n",
    }


def test_prediction_rejects_empty_patch():
    with pytest.raises(SchemaValidationError, match="model_patch"):
        Prediction("ZZCODE-BUG-001", "provider/model", "  ")
