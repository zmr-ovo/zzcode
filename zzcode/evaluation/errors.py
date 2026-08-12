"""Errors raised by the evaluation data and prediction layers."""


class EvaluationError(Exception):
    """Base class for evaluation-specific failures."""


class SchemaValidationError(EvaluationError, ValueError):
    """A structured evaluation object does not satisfy its schema."""


class DatasetValidationError(EvaluationError, ValueError):
    """An evaluation dataset is missing, inconsistent, or unsafe."""


class PrivateDataLeakageError(DatasetValidationError):
    """Private grading data was found in an Agent-visible payload."""


class PredictionValidationError(EvaluationError, ValueError):
    """A prediction or predictions file is invalid."""


class ArtifactError(EvaluationError):
    """An evaluation run artifact cannot be safely persisted or loaded."""


class RunAlreadyExistsError(ArtifactError, FileExistsError):
    """A run id already exists and must not be overwritten."""
