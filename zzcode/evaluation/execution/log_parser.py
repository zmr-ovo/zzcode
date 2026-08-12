"""Parse pytest JUnit XML and reconcile it with expected F2P/P2P selectors."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..errors import ArtifactError
from .models import JUnitReport, TestCaseResult, TestCaseStatus, TestGroupResult


def _nodeid(testcase: ET.Element) -> str:
    classname = testcase.get("classname", "").strip()
    name = testcase.get("name", "").strip()
    if not name:
        raise ArtifactError("JUnit testcase is missing name")
    if not classname:
        return name
    path = classname.replace(".", "/")
    if not path.endswith(".py"):
        path += ".py"
    return f"{path}::{name}"


def parse_junit(path: Path) -> JUnitReport:
    path = Path(path)
    try:
        root = ET.parse(path).getroot()
    except FileNotFoundError as exc:
        raise ArtifactError(f"JUnit XML does not exist: {path}") from exc
    except ET.ParseError as exc:
        raise ArtifactError(f"invalid JUnit XML {path}: {exc}") from exc
    cases: list[TestCaseResult] = []
    seen: set[str] = set()
    for testcase in root.iter("testcase"):
        nodeid = _nodeid(testcase)
        if nodeid in seen:
            raise ArtifactError(f"duplicate testcase in JUnit XML: {nodeid}")
        seen.add(nodeid)
        status = TestCaseStatus.PASSED
        detail = None
        for tag, candidate in (
            ("failure", TestCaseStatus.FAILED),
            ("error", TestCaseStatus.ERROR),
            ("skipped", TestCaseStatus.SKIPPED),
        ):
            child = testcase.find(tag)
            if child is not None:
                status = candidate
                detail = child.get("message") or (child.text or "").strip() or None
                break
        try:
            duration = float(testcase.get("time", "0"))
        except ValueError as exc:
            raise ArtifactError(f"invalid testcase duration for {nodeid}") from exc
        cases.append(TestCaseResult(nodeid, status, duration, detail))
    collection_errors: list[str] = []
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        try:
            errors = int(suite.get("errors", "0"))
        except ValueError as exc:
            raise ArtifactError("JUnit testsuite errors count is invalid") from exc
        if errors:
            collection_errors.append(
                f"{suite.get('name', 'testsuite')} reported {errors} collection/execution error(s)"
            )
    return JUnitReport(tuple(cases), tuple(dict.fromkeys(collection_errors)))


def _selector_matches(selector: str, nodeid: str) -> bool:
    return nodeid == selector or ("::" not in selector and nodeid.startswith(selector + "::"))


def reconcile_expected_tests(
    expected_ids: tuple[str, ...] | list[str],
    observed: JUnitReport,
) -> TestGroupResult:
    expected = tuple(expected_ids)
    if not expected:
        raise ArtifactError("expected test selectors must not be empty")
    selected: list[TestCaseResult] = []
    selected_ids: set[str] = set()
    not_run: list[str] = []
    for selector in expected:
        matches = [case for case in observed.cases if _selector_matches(selector, case.nodeid)]
        if not matches:
            not_run.append(selector)
            continue
        for case in matches:
            if case.nodeid not in selected_ids:
                selected.append(case)
                selected_ids.add(case.nodeid)
    statuses = [case.status for case in selected]
    total = len(selected) + len(not_run)
    return TestGroupResult(
        expected=expected,
        cases=tuple(selected),
        passed=statuses.count(TestCaseStatus.PASSED),
        total=total,
        failed=statuses.count(TestCaseStatus.FAILED),
        errors=statuses.count(TestCaseStatus.ERROR),
        skipped=statuses.count(TestCaseStatus.SKIPPED),
        not_run=tuple(not_run),
        collection_errors=observed.collection_errors,
        completed=not not_run and not observed.collection_errors,
    )
