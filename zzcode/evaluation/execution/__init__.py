"""Local execution building blocks used by the evaluation harness."""

from .local_harness import LocalGradingHarness, LocalGradingRun
from .docker_runner import DockerRunner
from .docker_test_executor import DockerTestExecutor
from .log_parser import parse_junit, reconcile_expected_tests
from .models import (
    JUnitReport,
    CommandResult,
    ContainerHandle,
    GradingDecision,
    PatchApplyResult,
    MountSpec,
    ResourceLimits,
    SafetyResult,
    TestCaseResult,
    TestCaseStatus,
    TestGroupResult,
    TestRun,
)
from .patch_applier import PatchApplier
from .runner import VerticalSliceRunner
from .test_executor import TestExecutor
from .workspace import WorkspaceManager

__all__ = [
    "JUnitReport",
    "CommandResult",
    "ContainerHandle",
    "DockerRunner",
    "DockerTestExecutor",
    "GradingDecision",
    "LocalGradingHarness",
    "LocalGradingRun",
    "PatchApplier",
    "PatchApplyResult",
    "MountSpec",
    "ResourceLimits",
    "SafetyResult",
    "TestCaseResult",
    "TestCaseStatus",
    "TestExecutor",
    "TestGroupResult",
    "TestRun",
    "WorkspaceManager",
    "VerticalSliceRunner",
    "parse_junit",
    "reconcile_expected_tests",
]
