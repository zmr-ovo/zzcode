"""Inject trusted private tests and execute F2P/P2P selectors with pytest."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from ..errors import ArtifactError, DatasetValidationError
from ..failures import make_failure
from ..schema import PrivateTestSpec
from ..status import EvaluationStage, FailureType
from ..serialization import write_json_atomic, write_text_atomic
from .log_parser import parse_junit, reconcile_expected_tests
from .models import JUnitReport, TestRun
from .patch_applier import PatchApplier
from ..grading.safety import SafetyPolicy, inspect_patch


class TestExecutor:
    __test__ = False

    def __init__(self, python_executable: Path | str | None = None):
        self.python_executable = str(python_executable or sys.executable)
        self.patch_applier = PatchApplier()

    def inject_test_patch(self, workspace: Path, spec: PrivateTestSpec) -> None:
        try:
            patch = spec.test_patch_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise DatasetValidationError(
                f"cannot read private test patch for {spec.instance_id}: {exc}"
            ) from exc
        safety = inspect_patch(
            patch,
            SafetyPolicy(
                protected_prefixes=(),
                max_files=100,
                max_changed_lines=10000,
            ),
        )
        invalid_paths = [
            path
            for path in safety.touched_paths
            if path != "hidden_tests" and not path.startswith("hidden_tests/")
        ]
        if not safety.passed or invalid_paths:
            reasons = list(safety.violations)
            if invalid_paths:
                reasons.append(
                    "private test patch may only modify hidden_tests/: " + ", ".join(invalid_paths)
                )
            raise DatasetValidationError(
                f"unsafe private test patch for {spec.instance_id}: {'; '.join(reasons)}"
            )
        result = self.patch_applier.apply(workspace, patch)
        if not result.applied:
            raise DatasetValidationError(
                f"private test patch cannot be applied for {spec.instance_id}: {result.stderr}"
            )

    def run_fail_to_pass(
        self,
        workspace: Path,
        test_ids: tuple[str, ...],
        timeout_seconds: float,
        artifact_dir: Path,
    ) -> TestRun:
        return self._run("f2p", workspace, test_ids, timeout_seconds, artifact_dir)

    def run_pass_to_pass(
        self,
        workspace: Path,
        test_ids: tuple[str, ...],
        timeout_seconds: float,
        artifact_dir: Path,
    ) -> TestRun:
        return self._run("p2p", workspace, test_ids, timeout_seconds, artifact_dir)

    def _run(
        self,
        group_name: str,
        workspace: Path,
        test_ids: tuple[str, ...],
        timeout_seconds: float,
        artifact_dir: Path,
    ) -> TestRun:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        junit_path = artifact_dir / f"{group_name}.xml"
        command = (
            self.python_executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit_path}",
            *test_ids,
        )
        started = time.monotonic()
        try:
            process = subprocess.run(
                command,
                cwd=Path(workspace),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            failure = make_failure(
                FailureType.TEST_TIMEOUT,
                EvaluationStage.TEST_EXECUTION,
                f"{group_name} tests exceeded {timeout_seconds} seconds",
                details={"timeout_seconds": timeout_seconds, "group": group_name},
            )
            result = reconcile_expected_tests(test_ids, JUnitReport(()))
            run = TestRun(
                group_name,
                command,
                None,
                True,
                duration,
                exc.stdout or "",
                exc.stderr or "",
                junit_path,
                result,
                failure,
            )
            self._write_artifacts(artifact_dir, run)
            return run
        except OSError as exc:
            duration = time.monotonic() - started
            failure = make_failure(
                FailureType.INFRASTRUCTURE_ERROR,
                EvaluationStage.TEST_EXECUTION,
                f"{group_name} pytest process could not start: {exc}",
                retryable=True,
                details={"group": group_name},
            )
            reconciled = reconcile_expected_tests(test_ids, JUnitReport((), (str(exc),)))
            run = TestRun(
                group_name,
                command,
                None,
                False,
                duration,
                "",
                str(exc),
                junit_path,
                reconciled,
                failure,
            )
            self._write_artifacts(artifact_dir, run)
            return run
        duration = time.monotonic() - started
        try:
            report = parse_junit(junit_path)
            reconciled = reconcile_expected_tests(test_ids, report)
        except ArtifactError as exc:
            failure = make_failure(
                FailureType.TEST_ERROR,
                EvaluationStage.TEST_EXECUTION,
                f"{group_name} test results are unavailable: {exc}",
                details={"returncode": process.returncode, "group": group_name},
            )
            reconciled = reconcile_expected_tests(test_ids, JUnitReport((), (str(exc),)))
            run = TestRun(
                group_name,
                command,
                process.returncode,
                False,
                duration,
                process.stdout,
                process.stderr,
                junit_path,
                reconciled,
                failure,
            )
            self._write_artifacts(artifact_dir, run)
            return run
        failure = None
        if not reconciled.completed:
            failure = make_failure(
                FailureType.TEST_ERROR,
                EvaluationStage.TEST_EXECUTION,
                f"{group_name} tests did not produce all expected results",
                details={
                    "returncode": process.returncode,
                    "not_run": list(reconciled.not_run),
                    "collection_errors": list(reconciled.collection_errors),
                },
            )
        run = TestRun(
            group_name,
            command,
            process.returncode,
            False,
            duration,
            process.stdout,
            process.stderr,
            junit_path,
            reconciled,
            failure,
        )
        self._write_artifacts(artifact_dir, run)
        return run

    @staticmethod
    def _write_artifacts(artifact_dir: Path, run: TestRun) -> None:
        command = " ".join(run.command)
        log = f"command: {command}\nreturncode: {run.returncode}\ntimed_out: {run.timed_out}\n\nSTDOUT\n{run.stdout}\n\nSTDERR\n{run.stderr}\n"
        write_text_atomic(artifact_dir / f"{run.group_name}.log", log, overwrite=False)
        write_json_atomic(
            artifact_dir / f"{run.group_name}.result.json",
            {
                "group_name": run.group_name,
                "command": list(run.command),
                "returncode": run.returncode,
                "timed_out": run.timed_out,
                "duration_seconds": run.duration_seconds,
                "junit_path": str(run.junit_path),
                "result": run.result.to_dict(),
                "failure": run.failure.to_dict() if run.failure else None,
            },
            overwrite=False,
        )
