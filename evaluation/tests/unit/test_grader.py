from zzcode.evaluation import (
    FailureType,
    ResolvedStatus,
    SafetyResult,
    TestCaseResult,
    TestCaseStatus,
    TestGroupResult,
    grade_test_results,
)


def _group(statuses, completed=True):
    cases = tuple(
        TestCaseResult(f"tests/test_x.py::test_{index}", status)
        for index, status in enumerate(statuses)
    )
    return TestGroupResult(
        expected=tuple(case.nodeid for case in cases),
        cases=cases,
        passed=statuses.count(TestCaseStatus.PASSED),
        total=len(statuses),
        failed=statuses.count(TestCaseStatus.FAILED),
        errors=statuses.count(TestCaseStatus.ERROR),
        skipped=statuses.count(TestCaseStatus.SKIPPED),
        not_run=(),
        collection_errors=(),
        completed=completed,
    )


def test_grader_full_partial_and_regression_rules():
    safe = SafetyResult(True, ("calc.py",), 1, 1)
    full = grade_test_results(
        _group([TestCaseStatus.PASSED, TestCaseStatus.PASSED]),
        _group([TestCaseStatus.PASSED]),
        safe,
    )
    partial = grade_test_results(
        _group([TestCaseStatus.PASSED, TestCaseStatus.FAILED]),
        _group([TestCaseStatus.PASSED]),
        safe,
    )
    regression = grade_test_results(
        _group([TestCaseStatus.PASSED, TestCaseStatus.PASSED]),
        _group([TestCaseStatus.FAILED]),
        safe,
    )

    assert full.resolved_status == ResolvedStatus.FULL
    assert partial.resolved_status == ResolvedStatus.PARTIAL
    assert regression.resolved_status == ResolvedStatus.NO
    assert regression.failure.failure_type == FailureType.TEST_FAILURE


def test_grader_incomplete_or_unsafe_results_are_no():
    safe = SafetyResult(True, ("calc.py",), 1, 1)
    incomplete = grade_test_results(
        _group([TestCaseStatus.PASSED], completed=False),
        _group([TestCaseStatus.PASSED]),
        safe,
    )
    unsafe = SafetyResult(False, ("tests/test_x.py",), 1, 1, ("protected",))
    unsafe_result = grade_test_results(None, None, unsafe)

    assert incomplete.resolved_status == ResolvedStatus.NO
    assert incomplete.failure.failure_type == FailureType.TEST_ERROR
    assert unsafe_result.resolved_status == ResolvedStatus.NO


def test_grader_treats_expected_error_or_skip_as_test_error():
    safe = SafetyResult(True, ("calc.py",), 1, 1)

    for status in (TestCaseStatus.ERROR, TestCaseStatus.SKIPPED):
        result = grade_test_results(
            _group([TestCaseStatus.PASSED, status]),
            _group([TestCaseStatus.PASSED]),
            safe,
        )
        assert result.resolved_status == ResolvedStatus.NO
        assert not result.tests_completed
        assert result.failure.failure_type == FailureType.TEST_ERROR
