"""Stable status and failure codes used in evaluation artifacts."""

from enum import Enum


class StringEnum(str, Enum):
    """Python 3.10-compatible string enum."""

    def __str__(self) -> str:
        return self.value


class RunStatus(StringEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class AgentRunStatus(StringEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class ResolvedStatus(StringEnum):
    NOT_GRADED = "NOT_GRADED"
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NO = "NO"
    AGENT_ERROR = "AGENT_ERROR"
    INFRA_ERROR = "INFRA_ERROR"
    DATASET_ERROR = "DATASET_ERROR"


class FailureCategory(StringEnum):
    DATASET_ERROR = "DATASET_ERROR"
    AGENT_ERROR = "AGENT_ERROR"
    INFRA_ERROR = "INFRA_ERROR"


class EvaluationStage(StringEnum):
    DATASET = "DATASET"
    WORKSPACE = "WORKSPACE"
    AGENT = "AGENT"
    PATCH_COLLECTION = "PATCH_COLLECTION"
    PATCH_SAFETY = "PATCH_SAFETY"
    PATCH_APPLY = "PATCH_APPLY"
    TEST_EXECUTION = "TEST_EXECUTION"
    GRADING = "GRADING"
    HARNESS = "HARNESS"


class FailureType(StringEnum):
    INVALID_DATASET = "INVALID_DATASET"
    DATASET_LEAKAGE = "DATASET_LEAKAGE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    AGENT_INTERRUPTED = "AGENT_INTERRUPTED"
    TOOL_FAILURE = "TOOL_FAILURE"
    EMPTY_PATCH = "EMPTY_PATCH"
    INVALID_PATCH = "INVALID_PATCH"
    PATCH_APPLY_FAILURE = "PATCH_APPLY_FAILURE"
    SAFETY_VIOLATION = "SAFETY_VIOLATION"
    TEST_TIMEOUT = "TEST_TIMEOUT"
    TEST_ERROR = "TEST_ERROR"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"
    HARNESS_INTERRUPTED = "HARNESS_INTERRUPTED"


FAILURE_CATEGORY_BY_TYPE = {
    FailureType.INVALID_DATASET: FailureCategory.DATASET_ERROR,
    FailureType.DATASET_LEAKAGE: FailureCategory.DATASET_ERROR,
    FailureType.PROVIDER_UNAVAILABLE: FailureCategory.INFRA_ERROR,
    FailureType.INFRASTRUCTURE_ERROR: FailureCategory.INFRA_ERROR,
    FailureType.HARNESS_INTERRUPTED: FailureCategory.INFRA_ERROR,
    FailureType.AGENT_TIMEOUT: FailureCategory.AGENT_ERROR,
    FailureType.AGENT_INTERRUPTED: FailureCategory.AGENT_ERROR,
    FailureType.TOOL_FAILURE: FailureCategory.AGENT_ERROR,
    FailureType.EMPTY_PATCH: FailureCategory.AGENT_ERROR,
    FailureType.INVALID_PATCH: FailureCategory.AGENT_ERROR,
    FailureType.PATCH_APPLY_FAILURE: FailureCategory.AGENT_ERROR,
    FailureType.SAFETY_VIOLATION: FailureCategory.AGENT_ERROR,
    FailureType.TEST_TIMEOUT: FailureCategory.AGENT_ERROR,
    FailureType.TEST_ERROR: FailureCategory.AGENT_ERROR,
}


def enum_value(enum_type, value, field_name):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field_name} must be one of: {choices}") from exc
