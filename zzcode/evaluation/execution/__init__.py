"""Local execution building blocks used by the evaluation harness."""

from .local_harness import LocalGradingHarness, LocalGradingRun
from .log_parser import parse_junit, reconcile_expected_tests
from .models import (
    JUnitReport,
    GradingDecision,
    PatchApplyResult,
    SafetyResult,
    TestCaseResult,
    TestCaseStatus,
    TestGroupResult,
    TestRun,
)
from .patch_applier import PatchApplier
from .test_executor import TestExecutor
from .workspace import WorkspaceManager

__all__ = [
    "JUnitReport",
    "GradingDecision",
    "LocalGradingHarness",
    "LocalGradingRun",
    "PatchApplier",
    "PatchApplyResult",
    "SafetyResult",
    "TestCaseResult",
    "TestCaseStatus",
    "TestExecutor",
    "TestGroupResult",
    "TestRun",
    "WorkspaceManager",
    "parse_junit",
    "reconcile_expected_tests",
]
