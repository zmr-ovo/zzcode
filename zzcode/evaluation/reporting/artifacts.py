"""Persist evaluation progress without overwriting earlier runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..errors import ArtifactError, RunAlreadyExistsError
from ..prediction import load_predictions
from ..schema import AgentRunResult, EvaluationResult, FailureRecord, Prediction, RunManifest
from ..serialization import append_jsonl, load_json, write_json_atomic, write_text_atomic
from ..status import RunStatus


def _safe_component(value: str, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
    ):
        raise ArtifactError(f"{field_name} must be a safe directory name")
    return value


def generate_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ArtifactError("run id timestamp must include a timezone")
    utc = now.astimezone(timezone.utc)
    return f"{utc.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:8]}"


@dataclass(frozen=True)
class RunPaths:
    root: Path
    run_manifest: Path
    predictions: Path
    instance_results: Path
    results: Path
    run_failure: Path
    instances: Path

    @classmethod
    def from_root(cls, root: Path) -> "RunPaths":
        root = Path(root)
        return cls(
            root=root,
            run_manifest=root / "run_manifest.json",
            predictions=root / "predictions.jsonl",
            instance_results=root / "instance_results.jsonl",
            results=root / "results.json",
            run_failure=root / "run_failure.json",
            instances=root / "instances",
        )

    def instance_dir(self, instance_id: str) -> Path:
        _safe_component(instance_id, "instance_id")
        resolved_root = self.root.resolve()
        resolved_instances = self.instances.resolve()
        if not resolved_instances.is_relative_to(resolved_root):
            raise ArtifactError("instances directory escapes the run directory")
        path = (self.instances / instance_id).resolve()
        if not path.is_relative_to(resolved_instances):
            raise ArtifactError("instance artifact path escapes the run directory")
        return path


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def start_run(self, manifest: RunManifest) -> RunPaths:
        if not isinstance(manifest, RunManifest):
            raise ArtifactError("manifest must be a RunManifest instance")
        if manifest.status != RunStatus.CREATED:
            raise ArtifactError("a new run manifest must start in CREATED status")
        run_root = self.root / manifest.run_id
        try:
            run_root.mkdir(parents=False, exist_ok=False)
        except FileExistsError as exc:
            raise RunAlreadyExistsError(
                f"run {manifest.run_id!r} already exists and will not be overwritten"
            ) from exc
        paths = RunPaths.from_root(run_root)
        paths.instances.mkdir()
        write_json_atomic(paths.run_manifest, manifest.to_dict(), overwrite=False)
        return paths

    def _validate_paths(self, paths: RunPaths) -> None:
        if not isinstance(paths, RunPaths):
            raise ArtifactError("paths must be a RunPaths instance")
        resolved = paths.root.resolve()
        if not resolved.is_relative_to(self.root) or resolved.parent != self.root:
            raise ArtifactError("run paths do not belong to this ArtifactStore")
        expected = RunPaths.from_root(resolved)
        if paths != expected:
            raise ArtifactError("run artifact paths do not match the run directory contract")

    def open_run(self, run_id: str) -> RunPaths:
        _safe_component(run_id, "run_id")
        run_root = (self.root / run_id).resolve()
        if not run_root.is_relative_to(self.root):
            raise ArtifactError("run path escapes the artifact root")
        paths = RunPaths.from_root(run_root)
        if not paths.root.is_dir() or not paths.run_manifest.is_file():
            raise ArtifactError(f"evaluation run does not exist or is incomplete: {run_id}")
        return paths

    def load_manifest(self, paths: RunPaths) -> RunManifest:
        self._validate_paths(paths)
        return RunManifest.from_dict(load_json(paths.run_manifest))

    def _assert_running(self, paths: RunPaths) -> None:
        status = self.load_manifest(paths).status
        if status != RunStatus.RUNNING:
            raise ArtifactError(f"run artifacts can only be written while RUNNING, not {status.value}")

    def update_manifest(self, paths: RunPaths, manifest: RunManifest) -> Path:
        current = self.load_manifest(paths)
        if current.run_id != manifest.run_id or paths.root.name != manifest.run_id:
            raise ArtifactError("manifest run_id does not match the run directory")
        allowed = {
            RunStatus.CREATED: {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.INTERRUPTED},
            RunStatus.RUNNING: {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.INTERRUPTED},
            RunStatus.COMPLETED: set(),
            RunStatus.FAILED: set(),
            RunStatus.INTERRUPTED: set(),
        }
        if current.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.INTERRUPTED}:
            raise ArtifactError(f"terminal run {current.run_id} will not be overwritten")
        if manifest.status == current.status:
            raise ArtifactError(f"run is already in status {current.status.value}")
        if manifest.status != current.status and manifest.status not in allowed[current.status]:
            raise ArtifactError(
                f"invalid run status transition: {current.status.value} -> {manifest.status.value}"
            )
        immutable_fields = (
            "dataset_name",
            "dataset_digest",
            "split",
            "agent_commit",
            "provider",
            "model_name_or_path",
            "environment_id",
            "started_at",
            "task_count",
            "model_parameters",
            "resource_limits",
            "environment",
            "image_digest",
        )
        changed = [name for name in immutable_fields if getattr(current, name) != getattr(manifest, name)]
        if changed:
            raise ArtifactError(f"run manifest immutable fields changed: {', '.join(changed)}")
        return write_json_atomic(paths.run_manifest, manifest.to_dict())

    def write_agent_result(self, paths: RunPaths, result: AgentRunResult) -> Path:
        self._validate_paths(paths)
        self._assert_running(paths)
        instance_dir = paths.instance_dir(result.instance_id)
        instance_dir.mkdir(parents=True, exist_ok=True)
        result_path = instance_dir / "agent_result.json"
        if not result_path.exists():
            return write_json_atomic(result_path, result.to_dict(), overwrite=False)
        current = AgentRunResult.from_dict(load_json(result_path))
        if current.status.value != "RUNNING":
            raise ArtifactError(
                f"terminal Agent result for {result.instance_id} will not be overwritten"
            )
        if result.status.value == "RUNNING":
            raise ArtifactError(f"Agent result for {result.instance_id} is already running")
        if current.started_at != result.started_at:
            raise ArtifactError("Agent result started_at changed during lifecycle update")
        return write_json_atomic(result_path, result.to_dict(), overwrite=True)

    def write_patch(self, paths: RunPaths, instance_id: str, patch: str) -> Path:
        self._validate_paths(paths)
        self._assert_running(paths)
        instance_dir = paths.instance_dir(instance_id)
        instance_dir.mkdir(parents=True, exist_ok=True)
        return write_text_atomic(instance_dir / "patch.diff", patch, overwrite=False)

    def append_prediction(self, paths: RunPaths, prediction: Prediction) -> Path:
        self._validate_paths(paths)
        self._assert_running(paths)
        if not isinstance(prediction, Prediction):
            raise ArtifactError("prediction must be a Prediction instance")
        try:
            return append_jsonl(
                paths.predictions,
                prediction.to_dict(),
                unique_key="instance_id",
            )
        except ArtifactError:
            raise

    def load_predictions(self, paths: RunPaths) -> dict[str, Prediction]:
        self._validate_paths(paths)
        return load_predictions(paths.predictions)

    def write_instance_result(self, paths: RunPaths, result: EvaluationResult) -> Path:
        self._validate_paths(paths)
        self._assert_running(paths)
        instance_dir = paths.instance_dir(result.instance_id)
        instance_dir.mkdir(parents=True, exist_ok=True)
        report_path = instance_dir / "report.json"
        write_json_atomic(report_path, result.to_dict(), overwrite=False)
        append_jsonl(paths.instance_results, result.to_dict(), unique_key="instance_id")
        return report_path

    def write_run_failure(
        self,
        paths: RunPaths,
        failure: FailureRecord,
        *,
        recorded_at: str,
    ) -> Path:
        self._validate_paths(paths)
        status = self.load_manifest(paths).status
        if status not in {RunStatus.CREATED, RunStatus.RUNNING}:
            raise ArtifactError(f"run failure cannot be written after {status.value}")
        if not isinstance(failure, FailureRecord):
            raise ArtifactError("failure must be a FailureRecord instance")
        return write_json_atomic(
            paths.run_failure,
            {"recorded_at": recorded_at, "failure": failure.to_dict()},
            overwrite=False,
        )

    def write_results(self, paths: RunPaths, payload: dict) -> Path:
        self._validate_paths(paths)
        self._assert_running(paths)
        return write_json_atomic(paths.results, payload, overwrite=False)
