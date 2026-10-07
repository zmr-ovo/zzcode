"""Run trusted F2P/P2P selectors inside an isolated grading container."""

from __future__ import annotations

import os
import time
from pathlib import Path

from ..errors import ArtifactError
from ..failures import make_failure
from ..status import EvaluationStage, FailureType
from .docker_runner import DockerRunner
from .log_parser import reconcile_expected_tests
from .models import JUnitReport, MountSpec, ResourceLimits, TestRun
from .test_executor import TestExecutor


class DockerTestExecutor(TestExecutor):
    """A drop-in TestExecutor whose test process is a short-lived container."""

    __test__ = False

    def __init__(
        self,
        runner: DockerRunner,
        *,
        image: str,
        limits: ResourceLimits | None = None,
    ) -> None:
        super().__init__()
        if not image.strip():
            raise ValueError("image must not be empty")
        self.runner = runner
        self.image = image
        self.limits = limits or ResourceLimits()

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
        workspace = Path(workspace).resolve()
        artifact_dir = Path(artifact_dir).resolve()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        original_artifact_mode = artifact_dir.stat().st_mode & 0o777
        # The directory contains only generated grading output and must be writable
        # by the fixed unprivileged uid used inside the container.
        os.chmod(artifact_dir, 0o777)
        junit_path = artifact_dir / f"{group_name}.xml"
        command = (group_name, *test_ids)
        mounts = (
            MountSpec(workspace, "/workspace", read_only=True),
            MountSpec(artifact_dir, "/artifacts", read_only=False),
        )
        handle = None
        started = time.monotonic()
        try:
            handle = self.runner.create(
                self.image,
                mounts=mounts,
                limits=self.limits,
                command=command,
            )
            self.runner.assert_isolated(
                self.runner.inspect(handle),
                mounts=mounts,
                limits=self.limits,
            )
            process = self.runner.start_and_wait(handle, timeout_seconds)
            if process.resource_status == "oom":
                raise ArtifactError("grading container exceeded its memory limit")
        except ArtifactError as exc:
            duration = time.monotonic() - started
            failure = make_failure(
                FailureType.INFRASTRUCTURE_ERROR,
                EvaluationStage.TEST_EXECUTION,
                f"{group_name} grading container failed: {exc}",
                retryable=True,
                details={"group": group_name, "image": self.image},
            )
            result = reconcile_expected_tests(test_ids, JUnitReport((), (str(exc),)))
            run = TestRun(
                group_name,
                command,
                None,
                False,
                duration,
                "",
                str(exc),
                junit_path,
                result,
                failure,
                handle.image_digest if handle else None,
                handle.container_id if handle else None,
            )
            self._write_artifacts(artifact_dir, run)
            return run
        finally:
            try:
                if handle is not None and self.runner.exists(handle):
                    self.runner.cleanup(handle)
            finally:
                os.chmod(artifact_dir, original_artifact_mode)

        return self._record_process_result(
            group_name, command, test_ids, timeout_seconds, artifact_dir,
            junit_path, process, process.duration_seconds,
            image_digest=handle.image_digest, container_id=handle.container_id,
        )
