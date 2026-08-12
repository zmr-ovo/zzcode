"""Structured results produced by local patch and test execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..schema import FailureRecord
from ..status import ResolvedStatus, StringEnum


class TestCaseStatus(StringEnum):
    __test__ = False
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    NOT_RUN = "NOT_RUN"


@dataclass(frozen=True)
class PatchApplyResult:
    checked: bool
    applied: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""
    failure: FailureRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "applied": self.applied,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "failure": self.failure.to_dict() if self.failure else None,
        }


@dataclass(frozen=True)
class SafetyResult:
    passed: bool
    touched_paths: tuple[str, ...]
    added_lines: int
    deleted_lines: int
    violations: tuple[str, ...] = ()
    failure: FailureRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "touched_paths": list(self.touched_paths),
            "added_lines": self.added_lines,
            "deleted_lines": self.deleted_lines,
            "violations": list(self.violations),
            "failure": self.failure.to_dict() if self.failure else None,
        }


@dataclass(frozen=True)
class TestCaseResult:
    __test__ = False
    nodeid: str
    status: TestCaseStatus
    duration_seconds: float = 0.0
    message: str | None = None


@dataclass(frozen=True)
class JUnitReport:
    cases: tuple[TestCaseResult, ...]
    collection_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class TestGroupResult:
    __test__ = False
    expected: tuple[str, ...]
    cases: tuple[TestCaseResult, ...]
    passed: int
    total: int
    failed: int
    errors: int
    skipped: int
    not_run: tuple[str, ...]
    collection_errors: tuple[str, ...]
    completed: bool

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected": list(self.expected),
            "cases": [
                {
                    "nodeid": case.nodeid,
                    "status": case.status.value,
                    "duration_seconds": case.duration_seconds,
                    "message": case.message,
                }
                for case in self.cases
            ],
            "passed": self.passed,
            "total": self.total,
            "failed": self.failed,
            "errors": self.errors,
            "skipped": self.skipped,
            "not_run": list(self.not_run),
            "collection_errors": list(self.collection_errors),
            "completed": self.completed,
            "rate": self.rate,
        }


@dataclass(frozen=True)
class TestRun:
    __test__ = False
    group_name: str
    command: tuple[str, ...]
    returncode: int | None
    timed_out: bool
    duration_seconds: float
    stdout: str
    stderr: str
    junit_path: Path
    result: TestGroupResult
    failure: FailureRecord | None = None
    image_digest: str | None = None
    container_id: str | None = None


@dataclass(frozen=True)
class GradingDecision:
    resolved_status: ResolvedStatus
    fail_to_pass_rate: float | None
    pass_to_pass_rate: float | None
    tests_completed: bool
    failure: FailureRecord | None = None


@dataclass(frozen=True)
class ResourceLimits:
    cpus: float = 1.0
    memory_mb: int = 1024
    pids_limit: int = 128
    tmpfs_mb: int = 256

    def __post_init__(self) -> None:
        if isinstance(self.cpus, bool) or not isinstance(self.cpus, (int, float)) or self.cpus <= 0:
            raise ValueError("cpus must be positive")
        for name in ("memory_mb", "pids_limit", "tmpfs_mb"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class MountSpec:
    source: Path
    target: str
    read_only: bool = True


@dataclass(frozen=True)
class ContainerHandle:
    container_id: str
    name: str
    image: str
    image_digest: str


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    container_id: str
