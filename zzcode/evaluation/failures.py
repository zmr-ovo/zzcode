"""Construct and validate the Agent/Data/Infrastructure failure taxonomy."""

from __future__ import annotations

from typing import Any

from .errors import SchemaValidationError
from .schema import FailureRecord
from .status import EvaluationStage, FailureCategory, FailureType, FAILURE_CATEGORY_BY_TYPE


def classify_failure(failure_type: FailureType | str) -> FailureCategory:
    try:
        failure_type = FailureType(failure_type)
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(f"unknown failure_type: {failure_type}") from exc
    return FAILURE_CATEGORY_BY_TYPE[failure_type]


def make_failure(
    failure_type: FailureType | str,
    stage: EvaluationStage | str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> FailureRecord:
    failure_type = FailureType(failure_type)
    return FailureRecord(
        category=classify_failure(failure_type),
        failure_type=failure_type,
        stage=stage,
        message=message,
        retryable=retryable,
        details=details or {},
    )


def validate_failure_category(failure: FailureRecord) -> None:
    expected = classify_failure(failure.failure_type)
    if failure.category != expected:
        raise SchemaValidationError(
            f"{failure.failure_type.value} must use category {expected.value}, "
            f"not {failure.category.value}"
        )
