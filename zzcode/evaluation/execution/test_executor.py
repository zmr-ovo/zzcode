"""Inject trusted private tests and execute F2P/P2P selectors with pytest."""

from __future__ import annotations

import os
from ...execution.commands import CommandRequest
from ...execution.shell import LocalExecutor
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
            process = LocalExecutor().execute(CommandRequest(command, Path(workspace), os.environ.copy(), timeout_seconds))
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
        return self._record_process_result(
            group_name, command, test_ids, timeout_seconds, artifact_dir,
            junit_path, process, time.monotonic() - started,
        )

    def _record_process_result(
        self, group_name, command, test_ids, timeout_seconds, artifact_dir,
        junit_path, process, duration, *, image_digest=None, container_id=None,
    ) -> TestRun:
        # 本机和容器只负责执行；评分结果解析与缺失测试判断共用同一逻辑。
        timed_out = process.timed_out or process.cancelled
        failure = None
        if timed_out:
            reconciled = reconcile_expected_tests(test_ids, JUnitReport(()))
            failure = make_failure(
                FailureType.TEST_TIMEOUT, EvaluationStage.TEST_EXECUTION,
                f"{group_name} tests exceeded {timeout_seconds} seconds",
                details={"timeout_seconds": timeout_seconds, "group": group_name},
            )
        else:
            try:
                reconciled = reconcile_expected_tests(test_ids, parse_junit(junit_path))
            except ArtifactError as exc:
                reconciled = reconcile_expected_tests(test_ids, JUnitReport((), (str(exc),)))
                failure = make_failure(
                    FailureType.TEST_ERROR, EvaluationStage.TEST_EXECUTION,
                    f"{group_name} test results are unavailable: {exc}",
                    details={"returncode": process.returncode, "group": group_name},
                )
            if failure is None and not reconciled.completed:
                failure = make_failure(
                    FailureType.TEST_ERROR, EvaluationStage.TEST_EXECUTION,
                    f"{group_name} tests did not produce all expected results",
                    details={"returncode": process.returncode,
                             "not_run": list(reconciled.not_run),
                             "collection_errors": list(reconciled.collection_errors)},
                )
        run = TestRun(
            group_name, command, None if timed_out else process.returncode,
            timed_out, duration, process.stdout, process.stderr,
            junit_path, reconciled, failure, image_digest, container_id,
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
                "image_digest": run.image_digest,
                "container_id": run.container_id,
            },
            overwrite=False,
        )
