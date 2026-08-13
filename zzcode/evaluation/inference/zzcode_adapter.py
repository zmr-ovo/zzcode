"""Run the production zzcode runtime and convert its workspace changes to a Prediction."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ..dataset import assert_inference_payload_safe
from ..errors import ArtifactError
from ..failures import make_failure
from ..schema import AgentRunResult, Prediction, TaskInstance
from ..serialization import load_json, write_json_atomic, write_text_atomic
from ..status import AgentRunStatus, EvaluationStage, FailureType
from .models import AgentRunConfig, InferenceOutcome
from .patch_collector import collect_patch
from .providers import provider_credentials_available, required_provider_environment


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(value: str) -> str:
    redacted = str(value)
    secret_names = {
        "OPENAI_API_KEY",
        "OPENAI_API_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "RIGHT_CODES_API_KEY",
        "GITHUB_PAT",
        "GH_PAT",
    }
    for name in secret_names:
        secret = os.environ.get(name)
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


class ZZCodeAgentAdapter:
    """Production adapter. It has no FakeModel client or fallback path."""

    def __init__(self, *, python_executable: Path | str | None = None) -> None:
        self.python_executable = str(python_executable or sys.executable)

    def run(
        self,
        task: TaskInstance,
        workspace: Path,
        config: AgentRunConfig,
        artifact_dir: Path,
    ) -> InferenceOutcome:
        if not isinstance(task, TaskInstance):
            raise TypeError("task must be a TaskInstance")
        if not isinstance(config, AgentRunConfig):
            raise TypeError("config must be an AgentRunConfig")
        assert_inference_payload_safe(task.to_dict())
        workspace = Path(workspace).resolve()
        artifact_dir = Path(artifact_dir).resolve()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        if not workspace.is_dir():
            raise ArtifactError(f"inference workspace is not a directory: {workspace}")
        if artifact_dir == workspace or artifact_dir.is_relative_to(workspace):
            raise ArtifactError("Agent artifacts must be outside the inference workspace")

        started_at = _now()
        started = time.monotonic()
        if not provider_credentials_available(config):
            required = " or ".join(required_provider_environment(config))
            failure = make_failure(
                FailureType.PROVIDER_UNAVAILABLE,
                EvaluationStage.AGENT,
                f"provider credentials are unavailable; configure {required}",
                retryable=True,
                details={"provider": config.provider},
            )
            result = self._result(
                task,
                AgentRunStatus.FAILED,
                started_at,
                time.monotonic() - started,
                failure=failure,
                metadata={"provider": config.provider, "model": config.model},
            )
            return InferenceOutcome(task, workspace, result, None, None)

        request_path = artifact_dir / "agent_request.json"
        response_path = artifact_dir / "agent_response.json"
        stdout_path = artifact_dir / "agent_worker.stdout.log"
        stderr_path = artifact_dir / "agent_worker.stderr.log"
        write_json_atomic(
            request_path,
            {
                "task": task.to_dict(),
                "config": config.to_worker_dict(),
                "workspace": str(workspace),
                "artifact_dir": str(artifact_dir),
            },
            overwrite=False,
        )
        command = (
            self.python_executable,
            "-m",
            "zzcode.evaluation.inference.worker",
            "--request",
            str(request_path),
            "--response",
            str(response_path),
        )
        # Repo Tasks may evaluate an older revision of zzcode itself.  Without
        # an explicit harness import root, the worker would import the target
        # checkout's package from cwd and that revision may not contain the
        # evaluation worker at all.  Keep the current Agent runtime external to
        # the repository under test, as SWE-bench-style inference requires.
        harness_import_root = str(Path(__file__).resolve().parents[3])
        try:
            process = subprocess.Popen(
                command,
                # Do not start in the target checkout: Repo Tasks can point to
                # a historical zzcode revision that has no evaluation package.
                # The worker still receives and strictly anchors all Agent
                # filesystem tools to ``workspace``.
                cwd=harness_import_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            failure = make_failure(
                FailureType.INFRASTRUCTURE_ERROR,
                EvaluationStage.AGENT,
                f"Agent worker could not start: {exc}",
                retryable=True,
            )
            result = self._result(
                task,
                AgentRunStatus.FAILED,
                started_at,
                time.monotonic() - started,
                failure=failure,
                metadata={"provider": config.provider, "model": config.model},
            )
            return InferenceOutcome(task, workspace, result, None, None)

        try:
            stdout, stderr = process.communicate(timeout=config.timeout_seconds)
        except subprocess.TimeoutExpired:
            self._terminate_group(process)
            stdout, stderr = process.communicate()
            duration = time.monotonic() - started
            write_text_atomic(stdout_path, _redact(stdout), overwrite=False)
            write_text_atomic(stderr_path, _redact(stderr), overwrite=False)
            failure = make_failure(
                FailureType.AGENT_TIMEOUT,
                EvaluationStage.AGENT,
                f"Agent exceeded {config.timeout_seconds} seconds",
                details={"timeout_seconds": config.timeout_seconds},
            )
            result = self._result(
                task,
                AgentRunStatus.INTERRUPTED,
                started_at,
                duration,
                failure=failure,
                metadata={"provider": config.provider, "model": config.model},
            )
            return InferenceOutcome(task, workspace, result, None, None)

        write_text_atomic(stdout_path, _redact(stdout), overwrite=False)
        write_text_atomic(stderr_path, _redact(stderr), overwrite=False)
        duration = time.monotonic() - started
        if not response_path.is_file():
            failure = make_failure(
                FailureType.INFRASTRUCTURE_ERROR,
                EvaluationStage.AGENT,
                f"Agent worker exited with code {process.returncode} without a response artifact",
                retryable=True,
                details={"returncode": process.returncode},
            )
            result = self._result(
                task,
                AgentRunStatus.FAILED,
                started_at,
                duration,
                failure=failure,
                metadata={"provider": config.provider, "model": config.model},
            )
            return InferenceOutcome(task, workspace, result, None, None)

        response = load_json(response_path)
        if response.get("kind") == "provider_error":
            failure = make_failure(
                FailureType.PROVIDER_UNAVAILABLE,
                EvaluationStage.AGENT,
                _redact(response.get("message", "provider request failed")),
                retryable=True,
                details={"provider": config.provider},
            )
            result = self._result(
                task,
                AgentRunStatus.FAILED,
                started_at,
                duration,
                failure=failure,
                metadata={"provider": config.provider, "model": config.model},
            )
            return InferenceOutcome(task, workspace, result, None, None)
        if response.get("kind") == "agent_error":
            failure = make_failure(
                FailureType.TOOL_FAILURE,
                EvaluationStage.AGENT,
                _redact(response.get("message", "Agent runtime failed")),
                details={"exception_type": str(response.get("exception_type", "unknown"))},
            )
            result = self._result(
                task,
                AgentRunStatus.FAILED,
                started_at,
                duration,
                failure=failure,
                metadata={"provider": config.provider, "model": config.model},
            )
            return InferenceOutcome(task, workspace, result, None, None)
        if process.returncode != 0 or response.get("kind") != "completed":
            failure = make_failure(
                FailureType.INFRASTRUCTURE_ERROR,
                EvaluationStage.AGENT,
                "Agent worker returned an invalid terminal response",
                retryable=True,
                details={"returncode": process.returncode},
            )
            result = self._result(
                task,
                AgentRunStatus.FAILED,
                started_at,
                duration,
                failure=failure,
                metadata={"provider": config.provider, "model": config.model},
            )
            return InferenceOutcome(task, workspace, result, None, None)

        metadata = {
            "provider": config.provider,
            "model": config.model,
            "attempts": int(response.get("attempts", 0)),
            "stop_reason": str(response.get("stop_reason", "")),
            "runtime_status": str(response.get("runtime_status", "")),
            "runtime_run_id": str(response.get("runtime_run_id", "")),
            "task_mode": str(response.get("task_mode", "")),
            "completion_gate_passed": bool(
                response.get("completion_gate_passed", response.get("runtime_status") == "completed")
            ),
            "coding_progress": dict(response.get("coding_progress", {})),
        }
        final_answer = _redact(response.get("final_answer", "")) or None
        try:
            patch = collect_patch(workspace)
        except ArtifactError as exc:
            failure = make_failure(
                FailureType.INFRASTRUCTURE_ERROR,
                EvaluationStage.PATCH_COLLECTION,
                str(exc),
                retryable=True,
            )
            result = self._result(
                task,
                AgentRunStatus.FAILED,
                started_at,
                duration,
                final_answer=final_answer,
                tool_steps=int(response.get("tool_steps", 0)),
                token_usage=response.get("token_usage", {}),
                failure=failure,
                metadata=metadata,
            )
            return InferenceOutcome(task, workspace, result, None, None)
        if not patch.strip():
            failure = make_failure(
                FailureType.EMPTY_PATCH,
                EvaluationStage.PATCH_COLLECTION,
                "Agent completed without producing a Git patch",
                details={"stop_reason": metadata["stop_reason"]},
            )
            result = self._result(
                task,
                AgentRunStatus.FAILED,
                started_at,
                duration,
                final_answer=final_answer,
                tool_steps=int(response.get("tool_steps", 0)),
                token_usage=response.get("token_usage", {}),
                failure=failure,
                metadata=metadata,
            )
            return InferenceOutcome(task, workspace, result, "", None)

        prediction = Prediction(task.instance_id, config.model, patch)
        gate_passed = bool(
            response.get("completion_gate_passed", response.get("runtime_status") == "completed")
        )
        failure = None
        status = AgentRunStatus.COMPLETED
        if not gate_passed:
            status = AgentRunStatus.FAILED
            failure = make_failure(
                FailureType.AGENT_INTERRUPTED,
                EvaluationStage.AGENT,
                "Agent produced a patch but did not satisfy the Coding completion gate",
                details={"stop_reason": metadata["stop_reason"]},
            )
        result = self._result(
            task,
            status,
            started_at,
            duration,
            final_answer=final_answer,
            patch_generated=True,
            tool_steps=int(response.get("tool_steps", 0)),
            token_usage=response.get("token_usage", {}),
            failure=failure,
            metadata=metadata,
        )
        return InferenceOutcome(task, workspace, result, patch, prediction)

    @staticmethod
    def _terminate_group(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        # The process leader can exit before a child that ignored SIGTERM.
        # Always attempt SIGKILL on the original process group before returning.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    @staticmethod
    def _result(
        task: TaskInstance,
        status: AgentRunStatus,
        started_at: str,
        duration_seconds: float,
        *,
        final_answer: str | None = None,
        patch_generated: bool = False,
        token_usage: dict | None = None,
        tool_steps: int = 0,
        failure=None,
        metadata: dict | None = None,
    ) -> AgentRunResult:
        return AgentRunResult(
            instance_id=task.instance_id,
            status=status,
            started_at=started_at,
            completed_at=_now(),
            duration_seconds=duration_seconds,
            final_answer=final_answer,
            patch_generated=patch_generated,
            token_usage=token_usage or {},
            tool_steps=tool_steps,
            failure=failure,
            metadata=metadata or {},
        )
