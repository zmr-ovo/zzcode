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
from pathlib import Path
from typing import Any, Mapping

from .errors import SchemaValidationError


SCHEMA_VERSION = 1
_INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


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


def _tuple_of_strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise SchemaValidationError(f"{field_name} must be a non-empty list of test ids")
    items = tuple(_required_string(item, field_name) for item in value)
    if len(items) != len(set(items)):
        raise SchemaValidationError(f"{field_name} contains duplicate test ids")
    return items


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
