"""Versioned data models shared by the evaluation harness.

This module intentionally contains no Git, model, Docker, or test execution
logic.  Phase 1 uses plain dataclasses so artifacts stay easy to inspect and
remain compatible with the SWE-bench prediction shape.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .errors import SchemaValidationError
from .status import (
    AgentRunStatus,
    EvaluationStage,
    FailureCategory,
    FailureType,
    FAILURE_CATEGORY_BY_TYPE,
    ResolvedStatus,
    RunStatus,
    enum_value,
)


SCHEMA_VERSION = 1
_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_SECRET_KEY_MARKERS = ("apikey", "authorization", "credential", "password", "privatekey", "secret")


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _instance_id(value: object) -> str:
    value = _required_string(value, "instance_id")
    if not _INSTANCE_ID_RE.fullmatch(value):
        raise SchemaValidationError(
            "instance_id must contain only letters, digits, '.', '_' or '-' "
            "and must not contain path separators"
        )
    return value


def _json_mapping(value: object, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{field_name} must be a JSON object")
    copied = copy.deepcopy(dict(value))
    if not all(isinstance(key, str) for key in copied):
        raise SchemaValidationError(f"{field_name} keys must be strings")
    try:
        json.dumps(copied, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(f"{field_name} must contain only JSON values") from exc
    return copied


def _assert_no_secret_fields(value: object, location: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = "".join(character for character in key.lower() if character.isalnum())
            if any(marker in normalized for marker in _SECRET_KEY_MARKERS):
                raise SchemaValidationError(f"secret-bearing field {key!r} is not allowed in {location}")
            _assert_no_secret_fields(child, f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_secret_fields(child, f"{location}[{index}]")


def _tuple_of_strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise SchemaValidationError(f"{field_name} must be a non-empty list of test ids")
    items = tuple(_required_string(item, field_name) for item in value)
    if len(items) != len(set(items)):
        raise SchemaValidationError(f"{field_name} contains duplicate test ids")
    return items


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field_name)


def _timestamp(value: object, field_name: str) -> str:
    value = _required_string(value, field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchemaValidationError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SchemaValidationError(f"{field_name} must include a timezone")
    return value


def _parsed_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _optional_timestamp(value: object, field_name: str) -> str | None:
    return None if value is None else _timestamp(value, field_name)


def _non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchemaValidationError(f"{field_name} must be a non-negative integer")
    return value


def _non_negative_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise SchemaValidationError(f"{field_name} must be a non-negative number")
    return float(value)


def _optional_rate(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaValidationError(f"{field_name} must be a number from 0 to 1")
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise SchemaValidationError(f"{field_name} must be a number from 0 to 1")
    return value


def _enum(enum_type, value: object, field_name: str):
    try:
        return enum_value(enum_type, value, field_name)
    except ValueError as exc:
        raise SchemaValidationError(str(exc)) from exc


@dataclass(frozen=True)
class TaskInstance:
    """Agent-visible definition of one repository task."""

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    environment_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "instance_id", _instance_id(self.instance_id))
        object.__setattr__(self, "repo", _required_string(self.repo, "repo"))
        commit = _required_string(self.base_commit, "base_commit")
        if not _COMMIT_RE.fullmatch(commit):
            raise SchemaValidationError("base_commit must be a 7-64 character hexadecimal commit id")
        object.__setattr__(self, "base_commit", commit.lower())
        object.__setattr__(
            self,
            "problem_statement",
            _required_string(self.problem_statement, "problem_statement"),
        )
        object.__setattr__(
            self,
            "environment_id",
            _required_string(self.environment_id, "environment_id"),
        )
        object.__setattr__(self, "metadata", _json_mapping(self.metadata, "metadata"))
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaValidationError(f"unsupported task schema_version: {self.schema_version}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "problem_statement": self.problem_statement,
            "environment_id": self.environment_id,
            "metadata": copy.deepcopy(self.metadata),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "TaskInstance":
        if not isinstance(row, Mapping):
            raise SchemaValidationError("task must be a JSON object")
        allowed = {
            "schema_version",
            "instance_id",
            "repo",
            "base_commit",
            "problem_statement",
            "environment_id",
            "metadata",
        }
        unknown = sorted(set(row) - allowed)
        if unknown:
            raise SchemaValidationError(f"task contains unknown fields: {', '.join(unknown)}")
        required = allowed - {"schema_version", "metadata"}
        missing = sorted(key for key in required if key not in row)
        if missing:
            raise SchemaValidationError(f"task is missing required fields: {', '.join(missing)}")
        return cls(
            schema_version=row.get("schema_version", SCHEMA_VERSION),
            instance_id=row["instance_id"],
            repo=row["repo"],
            base_commit=row["base_commit"],
            problem_statement=row["problem_statement"],
            environment_id=row["environment_id"],
            metadata=row.get("metadata", {}),
        )


@dataclass(frozen=True)
class PrivateTestSpec:
    """Harness-only grading inputs for one task."""

    instance_id: str
    gold_patch_path: Path
    test_patch_path: Path
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "instance_id", _instance_id(self.instance_id))
        object.__setattr__(self, "gold_patch_path", Path(self.gold_patch_path))
        object.__setattr__(self, "test_patch_path", Path(self.test_patch_path))
        object.__setattr__(
            self,
            "fail_to_pass",
            _tuple_of_strings(self.fail_to_pass, "FAIL_TO_PASS"),
        )
        object.__setattr__(
            self,
            "pass_to_pass",
            _tuple_of_strings(self.pass_to_pass, "PASS_TO_PASS"),
        )
        overlap = sorted(set(self.fail_to_pass) & set(self.pass_to_pass))
        if overlap:
            raise SchemaValidationError(
                f"tests cannot appear in both FAIL_TO_PASS and PASS_TO_PASS: {', '.join(overlap)}"
            )
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaValidationError(f"unsupported private schema_version: {self.schema_version}")


@dataclass(frozen=True)
class Prediction:
    """SWE-bench-compatible representation of an Agent-produced patch."""

    instance_id: str
    model_name_or_path: str
    model_patch: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "instance_id", _instance_id(self.instance_id))
        object.__setattr__(
            self,
            "model_name_or_path",
            _required_string(self.model_name_or_path, "model_name_or_path"),
        )
        if not isinstance(self.model_patch, str) or not self.model_patch.strip():
            raise SchemaValidationError("model_patch must be a non-empty string")

    def to_dict(self) -> dict[str, str]:
        return {
            "instance_id": self.instance_id,
            "model_name_or_path": self.model_name_or_path,
            "model_patch": self.model_patch,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "Prediction":
        if not isinstance(row, Mapping):
            raise SchemaValidationError("prediction must be a JSON object")
        required = {"instance_id", "model_name_or_path", "model_patch"}
        missing = sorted(required - set(row))
        unknown = sorted(set(row) - required)
        if missing:
            raise SchemaValidationError(f"prediction is missing required fields: {', '.join(missing)}")
        if unknown:
            raise SchemaValidationError(f"prediction contains unknown fields: {', '.join(unknown)}")
        return cls(
            instance_id=row["instance_id"],
            model_name_or_path=row["model_name_or_path"],
            model_patch=row["model_patch"],
        )


@dataclass(frozen=True)
class FailureRecord:
    """A serializable failure with an explicit owner and execution stage."""

    category: FailureCategory
    failure_type: FailureType
    stage: EvaluationStage
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", _enum(FailureCategory, self.category, "category"))
        object.__setattr__(
            self,
            "failure_type",
            _enum(FailureType, self.failure_type, "failure_type"),
        )
        object.__setattr__(self, "stage", _enum(EvaluationStage, self.stage, "stage"))
        object.__setattr__(self, "message", _required_string(self.message, "message"))
        if not isinstance(self.retryable, bool):
            raise SchemaValidationError("retryable must be a boolean")
        object.__setattr__(self, "details", _json_mapping(self.details, "details"))
        _assert_no_secret_fields(self.details, "failure details")
        expected_category = FAILURE_CATEGORY_BY_TYPE[self.failure_type]
        if self.category != expected_category:
            raise SchemaValidationError(
                f"{self.failure_type.value} must use category {expected_category.value}, "
                f"not {self.category.value}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "failure_type": self.failure_type.value,
            "stage": self.stage.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": copy.deepcopy(self.details),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "FailureRecord":
        if not isinstance(row, Mapping):
            raise SchemaValidationError("failure must be a JSON object")
        allowed = {"category", "failure_type", "stage", "message", "retryable", "details"}
        missing = sorted({"category", "failure_type", "stage", "message"} - set(row))
        unknown = sorted(set(row) - allowed)
        if missing:
            raise SchemaValidationError(f"failure is missing required fields: {', '.join(missing)}")
        if unknown:
            raise SchemaValidationError(f"failure contains unknown fields: {', '.join(unknown)}")
        return cls(
            category=row["category"],
            failure_type=row["failure_type"],
            stage=row["stage"],
            message=row["message"],
            retryable=row.get("retryable", False),
            details=row.get("details", {}),
        )


@dataclass(frozen=True)
class RunManifest:
    """Immutable configuration and provenance for one evaluation run."""

    run_id: str
    dataset_name: str
    dataset_digest: str
    split: str
    agent_commit: str
    provider: str
    model_name_or_path: str
    environment_id: str
    started_at: str
    task_count: int
    model_parameters: dict[str, Any] = field(default_factory=dict)
    resource_limits: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    image_digest: str | None = None
    status: RunStatus = RunStatus.CREATED
    completed_at: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _required_string(self.run_id, "run_id"))
        if "/" in self.run_id or "\\" in self.run_id or self.run_id in {".", ".."}:
            raise SchemaValidationError("run_id must not contain path separators")
        object.__setattr__(self, "dataset_name", _required_string(self.dataset_name, "dataset_name"))
        digest = _required_string(self.dataset_digest, "dataset_digest")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise SchemaValidationError("dataset_digest must use sha256:<64 lowercase hex characters>")
        object.__setattr__(self, "dataset_digest", digest)
        object.__setattr__(self, "split", _required_string(self.split, "split"))
        agent_commit = _required_string(self.agent_commit, "agent_commit")
        if not _COMMIT_RE.fullmatch(agent_commit):
            raise SchemaValidationError("agent_commit must be a 7-64 character hexadecimal commit id")
        object.__setattr__(self, "agent_commit", agent_commit.lower())
        object.__setattr__(self, "provider", _required_string(self.provider, "provider"))
        object.__setattr__(
            self,
            "model_name_or_path",
            _required_string(self.model_name_or_path, "model_name_or_path"),
        )
        object.__setattr__(
            self,
            "environment_id",
            _required_string(self.environment_id, "environment_id"),
        )
        object.__setattr__(self, "started_at", _timestamp(self.started_at, "started_at"))
        object.__setattr__(self, "completed_at", _optional_timestamp(self.completed_at, "completed_at"))
        object.__setattr__(self, "task_count", _non_negative_int(self.task_count, "task_count"))
        object.__setattr__(
            self,
            "model_parameters",
            _json_mapping(self.model_parameters, "model_parameters"),
        )
        object.__setattr__(
            self,
            "resource_limits",
            _json_mapping(self.resource_limits, "resource_limits"),
        )
        object.__setattr__(self, "environment", _json_mapping(self.environment, "environment"))
        _assert_no_secret_fields(self.model_parameters, "model_parameters")
        _assert_no_secret_fields(self.resource_limits, "resource_limits")
        _assert_no_secret_fields(self.environment, "environment")
        object.__setattr__(self, "image_digest", _optional_string(self.image_digest, "image_digest"))
        object.__setattr__(self, "status", _enum(RunStatus, self.status, "status"))
        if self.completed_at and _parsed_timestamp(self.completed_at) < _parsed_timestamp(self.started_at):
            raise SchemaValidationError("completed_at cannot be earlier than started_at")
        if self.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.INTERRUPTED}:
            if self.completed_at is None:
                raise SchemaValidationError("terminal run status requires completed_at")
        elif self.completed_at is not None:
            raise SchemaValidationError("completed_at is only valid for a terminal run status")
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaValidationError(f"unsupported run manifest schema_version: {self.schema_version}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "dataset_name": self.dataset_name,
            "dataset_digest": self.dataset_digest,
            "split": self.split,
            "agent_commit": self.agent_commit,
            "provider": self.provider,
            "model_name_or_path": self.model_name_or_path,
            "environment_id": self.environment_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "task_count": self.task_count,
            "model_parameters": copy.deepcopy(self.model_parameters),
            "resource_limits": copy.deepcopy(self.resource_limits),
            "environment": copy.deepcopy(self.environment),
            "image_digest": self.image_digest,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "RunManifest":
        if not isinstance(row, Mapping):
            raise SchemaValidationError("run manifest must be a JSON object")
        allowed = {
            "schema_version",
            "run_id",
            "dataset_name",
            "dataset_digest",
            "split",
            "agent_commit",
            "provider",
            "model_name_or_path",
            "environment_id",
            "started_at",
            "completed_at",
            "task_count",
            "model_parameters",
            "resource_limits",
            "environment",
            "image_digest",
            "status",
        }
        required = {
            "run_id",
            "dataset_name",
            "dataset_digest",
            "split",
            "agent_commit",
            "provider",
            "model_name_or_path",
            "environment_id",
            "started_at",
            "task_count",
        }
        missing = sorted(required - set(row))
        unknown = sorted(set(row) - allowed)
        if missing:
            raise SchemaValidationError(f"run manifest is missing required fields: {', '.join(missing)}")
        if unknown:
            raise SchemaValidationError(f"run manifest contains unknown fields: {', '.join(unknown)}")
        return cls(**dict(row))

    def with_status(self, status: RunStatus, completed_at: str | None = None) -> "RunManifest":
        row = self.to_dict()
        row["status"] = _enum(RunStatus, status, "status").value
        row["completed_at"] = completed_at
        return RunManifest.from_dict(row)


@dataclass(frozen=True)
class AgentRunResult:
    """Persisted outcome of the Agent stage before grading."""

    instance_id: str
    status: AgentRunStatus
    started_at: str
    completed_at: str | None = None
    duration_seconds: float = 0.0
    final_answer: str | None = None
    patch_generated: bool = False
    token_usage: dict[str, Any] = field(default_factory=dict)
    tool_steps: int = 0
    failure: FailureRecord | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "instance_id", _instance_id(self.instance_id))
        object.__setattr__(self, "status", _enum(AgentRunStatus, self.status, "status"))
        object.__setattr__(self, "started_at", _timestamp(self.started_at, "started_at"))
        object.__setattr__(self, "completed_at", _optional_timestamp(self.completed_at, "completed_at"))
        object.__setattr__(
            self,
            "duration_seconds",
            _non_negative_number(self.duration_seconds, "duration_seconds"),
        )
        object.__setattr__(self, "final_answer", _optional_string(self.final_answer, "final_answer"))
        if not isinstance(self.patch_generated, bool):
            raise SchemaValidationError("patch_generated must be a boolean")
        object.__setattr__(self, "token_usage", _json_mapping(self.token_usage, "token_usage"))
        object.__setattr__(self, "tool_steps", _non_negative_int(self.tool_steps, "tool_steps"))
        if self.failure is not None and not isinstance(self.failure, FailureRecord):
            if not isinstance(self.failure, Mapping):
                raise SchemaValidationError("failure must be a FailureRecord or JSON object")
            object.__setattr__(self, "failure", FailureRecord.from_dict(self.failure))
        object.__setattr__(self, "metadata", _json_mapping(self.metadata, "metadata"))
        _assert_no_secret_fields(self.metadata, "Agent result metadata")
        if self.completed_at and _parsed_timestamp(self.completed_at) < _parsed_timestamp(self.started_at):
            raise SchemaValidationError("completed_at cannot be earlier than started_at")
        if self.status == AgentRunStatus.RUNNING:
            if self.completed_at is not None or self.failure is not None:
                raise SchemaValidationError("running Agent result cannot be completed or failed")
        else:
            if self.completed_at is None:
                raise SchemaValidationError("terminal Agent result requires completed_at")
            if self.status in {AgentRunStatus.FAILED, AgentRunStatus.INTERRUPTED} and self.failure is None:
                raise SchemaValidationError("failed or interrupted Agent result requires failure")
            if self.status == AgentRunStatus.COMPLETED and self.failure is not None:
                raise SchemaValidationError("completed Agent result cannot contain failure")
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaValidationError(f"unsupported Agent result schema_version: {self.schema_version}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "final_answer": self.final_answer,
            "patch_generated": self.patch_generated,
            "token_usage": copy.deepcopy(self.token_usage),
            "tool_steps": self.tool_steps,
            "failure": self.failure.to_dict() if self.failure else None,
            "metadata": copy.deepcopy(self.metadata),
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "AgentRunResult":
        if not isinstance(row, Mapping):
            raise SchemaValidationError("Agent result must be a JSON object")
        allowed = {
            "schema_version",
            "instance_id",
            "status",
            "started_at",
            "completed_at",
            "duration_seconds",
            "final_answer",
            "patch_generated",
            "token_usage",
            "tool_steps",
            "failure",
            "metadata",
        }
        required = {"instance_id", "status", "started_at"}
        missing = sorted(required - set(row))
        unknown = sorted(set(row) - allowed)
        if missing:
            raise SchemaValidationError(f"Agent result is missing required fields: {', '.join(missing)}")
        if unknown:
            raise SchemaValidationError(f"Agent result contains unknown fields: {', '.join(unknown)}")
        return cls(**dict(row))


@dataclass(frozen=True)
class EvaluationResult:
    """Single-task result. Grading fields remain nullable until Phase 3."""

    instance_id: str
    resolved_status: ResolvedStatus
    agent_completed: bool
    patch_generated: bool
    patch_applied: bool
    tests_completed: bool
    fail_to_pass_rate: float | None = None
    pass_to_pass_rate: float | None = None
    failure: FailureRecord | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    completed_at: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "instance_id", _instance_id(self.instance_id))
        object.__setattr__(
            self,
            "resolved_status",
            _enum(ResolvedStatus, self.resolved_status, "resolved_status"),
        )
        for field_name in ("agent_completed", "patch_generated", "patch_applied", "tests_completed"):
            if not isinstance(getattr(self, field_name), bool):
                raise SchemaValidationError(f"{field_name} must be a boolean")
        object.__setattr__(
            self,
            "fail_to_pass_rate",
            _optional_rate(self.fail_to_pass_rate, "fail_to_pass_rate"),
        )
        object.__setattr__(
            self,
            "pass_to_pass_rate",
            _optional_rate(self.pass_to_pass_rate, "pass_to_pass_rate"),
        )
        if self.failure is not None and not isinstance(self.failure, FailureRecord):
            if not isinstance(self.failure, Mapping):
                raise SchemaValidationError("failure must be a FailureRecord or JSON object")
            object.__setattr__(self, "failure", FailureRecord.from_dict(self.failure))
        object.__setattr__(self, "metrics", _json_mapping(self.metrics, "metrics"))
        object.__setattr__(self, "completed_at", _optional_timestamp(self.completed_at, "completed_at"))
        if self.patch_applied and not self.patch_generated:
            raise SchemaValidationError("patch_applied requires patch_generated")
        if self.tests_completed and not self.patch_applied:
            raise SchemaValidationError("tests_completed requires patch_applied")
        if self.tests_completed:
            if self.fail_to_pass_rate is None or self.pass_to_pass_rate is None:
                raise SchemaValidationError("completed tests require F2P and P2P rates")
        elif self.fail_to_pass_rate is not None or self.pass_to_pass_rate is not None:
            raise SchemaValidationError("F2P and P2P rates require tests_completed")
        if self.resolved_status in {
            ResolvedStatus.AGENT_ERROR,
            ResolvedStatus.INFRA_ERROR,
            ResolvedStatus.DATASET_ERROR,
        } and self.failure is None:
            raise SchemaValidationError("error resolved_status requires failure")
        if self.resolved_status in {
            ResolvedStatus.AGENT_ERROR,
            ResolvedStatus.INFRA_ERROR,
            ResolvedStatus.DATASET_ERROR,
        } and self.completed_at is None:
            raise SchemaValidationError("error resolved_status requires completed_at")
        if self.resolved_status == ResolvedStatus.NOT_GRADED and self.failure is not None:
            raise SchemaValidationError("NOT_GRADED result cannot contain failure")
        expected_failure_category = {
            ResolvedStatus.AGENT_ERROR: FailureCategory.AGENT_ERROR,
            ResolvedStatus.INFRA_ERROR: FailureCategory.INFRA_ERROR,
            ResolvedStatus.DATASET_ERROR: FailureCategory.DATASET_ERROR,
        }.get(self.resolved_status)
        if expected_failure_category and self.failure.category != expected_failure_category:
            raise SchemaValidationError(
                f"{self.resolved_status.value} requires {expected_failure_category.value} failure"
            )
        if self.resolved_status == ResolvedStatus.FULL:
            if not self.tests_completed or self.fail_to_pass_rate != 1.0 or self.pass_to_pass_rate != 1.0:
                raise SchemaValidationError("FULL requires completed tests with F2P=1 and P2P=1")
            if self.failure is not None:
                raise SchemaValidationError("FULL result cannot contain failure")
        if self.resolved_status in {ResolvedStatus.FULL, ResolvedStatus.PARTIAL, ResolvedStatus.NO}:
            if self.completed_at is None:
                raise SchemaValidationError("graded result requires completed_at")
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaValidationError(f"unsupported evaluation result schema_version: {self.schema_version}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "resolved_status": self.resolved_status.value,
            "agent_completed": self.agent_completed,
            "patch_generated": self.patch_generated,
            "patch_applied": self.patch_applied,
            "tests_completed": self.tests_completed,
            "fail_to_pass_rate": self.fail_to_pass_rate,
            "pass_to_pass_rate": self.pass_to_pass_rate,
            "failure": self.failure.to_dict() if self.failure else None,
            "metrics": copy.deepcopy(self.metrics),
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "EvaluationResult":
        if not isinstance(row, Mapping):
            raise SchemaValidationError("evaluation result must be a JSON object")
        allowed = {
            "schema_version",
            "instance_id",
            "resolved_status",
            "agent_completed",
            "patch_generated",
            "patch_applied",
            "tests_completed",
            "fail_to_pass_rate",
            "pass_to_pass_rate",
            "failure",
            "metrics",
            "completed_at",
        }
        required = {
            "instance_id",
            "resolved_status",
            "agent_completed",
            "patch_generated",
            "patch_applied",
            "tests_completed",
        }
        missing = sorted(required - set(row))
        unknown = sorted(set(row) - allowed)
        if missing:
            raise SchemaValidationError(
                f"evaluation result is missing required fields: {', '.join(missing)}"
            )
        if unknown:
            raise SchemaValidationError(
                f"evaluation result contains unknown fields: {', '.join(unknown)}"
            )
        return cls(**dict(row))
