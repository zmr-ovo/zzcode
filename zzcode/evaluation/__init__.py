"""Reusable evaluation schemas, dataset loading, and prediction I/O."""

from .dataset import EvaluationDataset, assert_inference_payload_safe
from .errors import (
    DatasetValidationError,
    EvaluationError,
    PredictionValidationError,
    PrivateDataLeakageError,
    SchemaValidationError,
)
from .prediction import append_prediction, load_predictions, validate_predictions
from .schema import Prediction, PrivateTestSpec, SCHEMA_VERSION, TaskInstance

__all__ = [
    "DatasetValidationError",
    "EvaluationDataset",
    "EvaluationError",
    "Prediction",
    "PredictionValidationError",
    "PrivateDataLeakageError",
    "PrivateTestSpec",
    "SCHEMA_VERSION",
    "SchemaValidationError",
    "TaskInstance",
    "append_prediction",
    "assert_inference_payload_safe",
    "load_predictions",
    "validate_predictions",
]
