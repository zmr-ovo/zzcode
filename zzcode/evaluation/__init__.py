"""Reusable evaluation schemas, dataset loading, and prediction I/O."""

from .dataset import EvaluationDataset, assert_inference_payload_safe
from .errors import (
    ArtifactError,
    DatasetValidationError,
    EvaluationError,
    PredictionValidationError,
    PrivateDataLeakageError,
    RunAlreadyExistsError,
    SchemaValidationError,
)
from .failures import classify_failure, make_failure
from .prediction import append_prediction, load_predictions, validate_predictions
from .reporting import ArtifactStore, RunPaths, generate_run_id
from .schema import (
    AgentRunResult,
    EvaluationResult,
    FailureRecord,
    Prediction,
    PrivateTestSpec,
    RunManifest,
    SCHEMA_VERSION,
    TaskInstance,
)
from .status import (
    AgentRunStatus,
    EvaluationStage,
    FailureCategory,
    FailureType,
    ResolvedStatus,
    RunStatus,
)

__all__ = [
    "AgentRunResult",
    "AgentRunStatus",
    "ArtifactError",
    "ArtifactStore",
    "DatasetValidationError",
    "EvaluationDataset",
    "EvaluationError",
    "EvaluationResult",
    "EvaluationStage",
    "FailureCategory",
    "FailureRecord",
    "FailureType",
    "Prediction",
    "PredictionValidationError",
    "PrivateDataLeakageError",
    "PrivateTestSpec",
    "ResolvedStatus",
    "RunAlreadyExistsError",
    "RunManifest",
    "RunPaths",
    "RunStatus",
    "SCHEMA_VERSION",
    "SchemaValidationError",
    "TaskInstance",
    "append_prediction",
    "assert_inference_payload_safe",
    "classify_failure",
    "generate_run_id",
    "load_predictions",
    "make_failure",
    "validate_predictions",
]
