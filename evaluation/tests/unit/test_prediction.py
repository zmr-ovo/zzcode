import json

import pytest

from zzcode.evaluation import (
    Prediction,
    PredictionValidationError,
    TaskInstance,
    append_prediction,
    load_predictions,
    validate_predictions,
)


def _task(instance_id="ZZCODE-BUG-001"):
    return TaskInstance(
        instance_id=instance_id,
        repo="local/zzcode",
        base_commit="abcdef1",
        problem_statement="Fix it.",
        environment_id="zzcode-py313-v1",
    )


def test_prediction_jsonl_round_trip_preserves_unicode_and_patch(tmp_path):
    path = tmp_path / "predictions.jsonl"
    prediction = Prediction(
        "ZZCODE-BUG-001",
        "provider/模型",
        "diff --git a/a.py b/a.py\n+说明 = '修复'\n",
    )

    append_prediction(path, prediction)

    assert load_predictions(path) == {prediction.instance_id: prediction}
    assert "模型" in path.read_text(encoding="utf-8")


def test_append_prediction_rejects_duplicate_instance(tmp_path):
    path = tmp_path / "predictions.jsonl"
    prediction = Prediction("ZZCODE-BUG-001", "provider/model", "diff --git a/a b/a\n")
    append_prediction(path, prediction)

    with pytest.raises(PredictionValidationError, match="duplicate"):
        append_prediction(path, prediction)


@pytest.mark.parametrize(
    "row",
    [
        {"instance_id": "ZZCODE-BUG-001", "model_name_or_path": "model"},
        {
            "instance_id": "ZZCODE-BUG-001",
            "model_name_or_path": "model",
            "model_patch": "",
        },
        {
            "instance_id": "ZZCODE-BUG-001",
            "model_name_or_path": "model",
            "model_patch": "patch",
            "private_tests": ["hidden"],
        },
    ],
)
def test_load_predictions_rejects_missing_extra_or_empty_fields(tmp_path, row):
    path = tmp_path / "predictions.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(PredictionValidationError):
        load_predictions(path)


def test_validate_predictions_rejects_unknown_and_missing_instances():
    tasks = [_task()]

    with pytest.raises(PredictionValidationError, match="unknown"):
        validate_predictions(
            tasks,
            {"ZZCODE-BUG-999": Prediction("ZZCODE-BUG-999", "model", "patch")},
        )

    with pytest.raises(PredictionValidationError, match="missing"):
        validate_predictions(tasks, {})
