"""Convert structured safety and test results into deterministic grades."""

from __future__ import annotations

from ..execution.models import GradingDecision, SafetyResult, TestGroupResult
from ..failures import make_failure
from ..status import EvaluationStage, FailureType, ResolvedStatus


def grade_test_results(
    fail_to_pass: TestGroupResult | None,
    pass_to_pass: TestGroupResult | None,
    safety: SafetyResult,
) -> GradingDecision:
    if not safety.passed:
        return GradingDecision(
            ResolvedStatus.NO,
            None,
            None,
            False,
            safety.failure,
        )
    if fail_to_pass is None or pass_to_pass is None:
        return GradingDecision(
            ResolvedStatus.NO,
            None,
            None,
            False,
            make_failure(
                FailureType.TEST_ERROR,
                EvaluationStage.TEST_EXECUTION,
                "F2P or P2P result is missing",
            ),
        )
    f2p_rate = fail_to_pass.rate
    p2p_rate = pass_to_pass.rate
    completed = fail_to_pass.completed and pass_to_pass.completed
    if not completed:
        return GradingDecision(
            ResolvedStatus.NO,
            f2p_rate,
            p2p_rate,
            False,
            make_failure(
                FailureType.TEST_ERROR,
                EvaluationStage.TEST_EXECUTION,
                "F2P or P2P tests did not complete",
                details={
                    "f2p_not_run": list(fail_to_pass.not_run),
                    "p2p_not_run": list(pass_to_pass.not_run),
                    "f2p_collection_errors": list(fail_to_pass.collection_errors),
                    "p2p_collection_errors": list(pass_to_pass.collection_errors),
                },
            ),
        )
    if (
        fail_to_pass.errors
        or fail_to_pass.skipped
        or pass_to_pass.errors
        or pass_to_pass.skipped
    ):
        return GradingDecision(
            ResolvedStatus.NO,
            f2p_rate,
            p2p_rate,
            False,
            make_failure(
                FailureType.TEST_ERROR,
                EvaluationStage.TEST_EXECUTION,
                "F2P or P2P contains errored or skipped expected tests",
                details={
                    "f2p_errors": fail_to_pass.errors,
                    "f2p_skipped": fail_to_pass.skipped,
                    "p2p_errors": pass_to_pass.errors,
                    "p2p_skipped": pass_to_pass.skipped,
                },
            ),
        )
    if f2p_rate == 1.0 and p2p_rate == 1.0:
        return GradingDecision(ResolvedStatus.FULL, f2p_rate, p2p_rate, True)
    failure = make_failure(
        FailureType.TEST_FAILURE,
        EvaluationStage.GRADING,
        "F2P and P2P requirements were not both satisfied",
        details={"fail_to_pass_rate": f2p_rate, "pass_to_pass_rate": p2p_rate},
    )
    if 0.0 < f2p_rate < 1.0 and p2p_rate == 1.0:
        return GradingDecision(
            ResolvedStatus.PARTIAL,
            f2p_rate,
            p2p_rate,
            True,
            failure,
        )
    return GradingDecision(
        ResolvedStatus.NO,
        f2p_rate,
        p2p_rate,
        True,
        failure,
    )
