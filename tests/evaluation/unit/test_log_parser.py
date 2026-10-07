import pytest

from zzcode.evaluation import ArtifactError, TestCaseStatus, parse_junit, reconcile_expected_tests


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="4" failures="1" errors="1" skipped="1">
  <testsuite name="pytest" tests="4" failures="1" errors="1" skipped="1">
    <testcase classname="tests.test_calc" name="test_pass" time="0.1" />
    <testcase classname="tests.test_calc" name="test_fail" time="0.2">
      <failure message="assert 1 == 2">details</failure>
    </testcase>
    <testcase classname="tests.test_calc" name="test_error" time="0.3">
      <error message="fixture failed">details</error>
    </testcase>
    <testcase classname="tests.test_calc" name="test_skip" time="0.0">
      <skipped message="not supported" />
    </testcase>
  </testsuite>
</testsuites>
"""


def test_parse_junit_distinguishes_all_test_outcomes(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT, encoding="utf-8")

    report = parse_junit(path)

    assert [case.status for case in report.cases] == [
        TestCaseStatus.PASSED,
        TestCaseStatus.FAILED,
        TestCaseStatus.ERROR,
        TestCaseStatus.SKIPPED,
    ]
    assert report.cases[0].nodeid == "tests/test_calc.py::test_pass"


def test_reconcile_supports_node_and_file_selectors_and_marks_not_run(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT, encoding="utf-8")
    report = parse_junit(path)

    result = reconcile_expected_tests(
        ("tests/test_calc.py::test_pass", "hidden/test_missing.py::test_missing"),
        report,
    )

    assert result.passed == 1
    assert result.total == 2
    assert result.not_run == ("hidden/test_missing.py::test_missing",)
    assert not result.completed

    whole_file = reconcile_expected_tests(("tests/test_calc.py",), report)
    assert whole_file.total == 4
    assert whole_file.failed == 1
    assert whole_file.errors == 1
    assert whole_file.skipped == 1


def test_parse_junit_rejects_missing_or_malformed_xml(tmp_path):
    with pytest.raises(ArtifactError, match="does not exist"):
        parse_junit(tmp_path / "missing.xml")

    malformed = tmp_path / "malformed.xml"
    malformed.write_text("<testsuite>", encoding="utf-8")
    with pytest.raises(ArtifactError, match="invalid JUnit"):
        parse_junit(malformed)
