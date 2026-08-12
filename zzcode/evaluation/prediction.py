"""Read, write, and validate SWE-bench-compatible prediction JSONL."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

from .errors import ArtifactError, PredictionValidationError, SchemaValidationError
from .schema import Prediction, TaskInstance
from .serialization import append_jsonl


def append_prediction(path: Path, prediction: Prediction) -> None:
    """Append one prediction while preventing duplicate instance ids."""

    path = Path(path)
    if not isinstance(prediction, Prediction):
        raise PredictionValidationError("prediction must be a Prediction instance")
    try:
        append_jsonl(path, prediction.to_dict(), unique_key="instance_id")
    except ArtifactError as exc:
        raise PredictionValidationError(str(exc)) from exc


def load_predictions(path: Path) -> dict[str, Prediction]:
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise PredictionValidationError(f"predictions file does not exist: {path}") from exc
    predictions: dict[str, Prediction] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PredictionValidationError(
                f"invalid prediction JSON at {path}:{line_number}: {exc}"
            ) from exc
        try:
            prediction = Prediction.from_dict(row)
        except SchemaValidationError as exc:
            raise PredictionValidationError(
                f"invalid prediction at {path}:{line_number}: {exc}"
            ) from exc
        if prediction.instance_id in predictions:
            raise PredictionValidationError(
                f"duplicate prediction instance_id at {path}:{line_number}: {prediction.instance_id}"
            )
        predictions[prediction.instance_id] = prediction
    if not predictions:
        raise PredictionValidationError(f"predictions file contains no predictions: {path}")
    return predictions


def validate_predictions(
    tasks: Iterable[TaskInstance],
    predictions: Mapping[str, Prediction],
) -> None:
    """Require exactly one valid prediction for every task in a selected split."""

    task_ids = [task.instance_id for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise PredictionValidationError("tasks contain duplicate instance ids")
    prediction_ids = set(predictions)
    for key, prediction in predictions.items():
        if not isinstance(prediction, Prediction):
            raise PredictionValidationError(f"prediction for {key} is not a Prediction instance")
        if key != prediction.instance_id:
            raise PredictionValidationError(
                f"prediction mapping key {key!r} does not match {prediction.instance_id!r}"
            )
    unknown = sorted(prediction_ids - set(task_ids))
    missing = sorted(set(task_ids) - prediction_ids)
    if unknown:
        raise PredictionValidationError(f"predictions contain unknown instances: {', '.join(unknown)}")
    if missing:
        raise PredictionValidationError(f"predictions are missing instances: {', '.join(missing)}")
