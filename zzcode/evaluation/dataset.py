"""Load public Repo Tasks and their isolated private grading specs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import DatasetValidationError, PrivateDataLeakageError, SchemaValidationError
from .schema import PrivateTestSpec, SCHEMA_VERSION, TaskInstance


_SPLIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PRIVATE_KEY_PREFIXES = {
    "f2p",
    "failtopass",
    "grader",
    "grading",
    "goldpatch",
    "hiddentests",
    "p2p",
    "passtopass",
    "privategrading",
    "privatepath",
    "testpatch",
}
_MANIFEST_FIELDS = {
    "schema_version",
    "instance_id",
    "repo",
    "base_commit",
    "problem_statement",
    "problem_statement_path",
    "environment_id",
    "metadata",
}


def _normalized_key(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def assert_inference_payload_safe(value: object, location: str = "payload") -> None:
    """Reject private grading field names anywhere in an Agent-visible value."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise PrivateDataLeakageError(f"{location} contains a non-string key")
            normalized = _normalized_key(key)
            if any(normalized.startswith(prefix) for prefix in _PRIVATE_KEY_PREFIXES):
                raise PrivateDataLeakageError(f"private grading field {key!r} found in {location}")
            assert_inference_payload_safe(child, f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_inference_payload_safe(child, f"{location}[{index}]")


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DatasetValidationError(f"missing {description}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DatasetValidationError(f"invalid JSON in {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetValidationError(f"{description} must be a JSON object: {path}")
    return value


def _safe_child(root: Path, relative: object, description: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise DatasetValidationError(f"{description} must be a non-empty relative path")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise DatasetValidationError(f"{description} must be relative: {relative}")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise DatasetValidationError(f"{description} escapes its configured root: {relative}")
    return resolved


def _roots_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise DatasetValidationError(f"missing public manifest: {path}") from exc
    rows: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetValidationError(f"invalid manifest JSON at {path}:{line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise DatasetValidationError(f"manifest row {line_number} must be a JSON object")
        unknown = sorted(set(row) - _MANIFEST_FIELDS)
        if unknown:
            raise DatasetValidationError(
                f"manifest row {line_number} contains unknown fields: {', '.join(unknown)}"
            )
        instance_id = row.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id:
            raise DatasetValidationError(f"manifest row {line_number} has no instance_id")
        if instance_id in rows:
            raise DatasetValidationError(f"duplicate manifest instance_id: {instance_id}")
        rows[instance_id] = row
    if not rows:
        raise DatasetValidationError(f"manifest contains no tasks: {path}")
    return rows


def _load_split(path: Path, known_ids: Iterable[str]) -> tuple[str, ...]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise DatasetValidationError(f"missing dataset split: {path}") from exc
    selected = tuple(line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#"))
    if not selected:
        raise DatasetValidationError(f"dataset split is empty: {path}")
    if len(selected) != len(set(selected)):
        raise DatasetValidationError(f"dataset split contains duplicate instance ids: {path}")
    unknown = sorted(set(selected) - set(known_ids))
    if unknown:
        raise DatasetValidationError(f"dataset split references unknown instances: {', '.join(unknown)}")
    return selected


def _merge_metadata(manifest_metadata: object, task_metadata: dict[str, Any]) -> dict[str, Any]:
    if manifest_metadata is None:
        merged: dict[str, Any] = {}
    elif isinstance(manifest_metadata, dict):
        merged = dict(manifest_metadata)
    else:
        raise DatasetValidationError("manifest metadata must be a JSON object")
    for key, value in task_metadata.items():
        if key in merged and merged[key] != value:
            raise DatasetValidationError(f"conflicting public metadata value for {key!r}")
        merged[key] = value
    assert_inference_payload_safe(merged, "task metadata")
    return merged


class EvaluationDataset:
    """A validated split with separate public and private representations."""

    def __init__(
        self,
        *,
        public_root: Path,
        private_root: Path,
        split: str,
        tasks: tuple[TaskInstance, ...],
        private_specs: dict[str, PrivateTestSpec],
    ) -> None:
        self.public_root = public_root
        self.private_root = private_root
        self.split = split
        self._tasks = tasks
        self._private_specs = dict(private_specs)
        self._tasks_by_id = {task.instance_id: task for task in tasks}

    @classmethod
    def load(cls, public_root: Path, private_root: Path, split: str) -> "EvaluationDataset":
        public_root = Path(public_root).resolve()
        supplied_private_root = Path(private_root).resolve()
        if not public_root.is_dir():
            raise DatasetValidationError(f"public_root is not a directory: {public_root}")
        if not supplied_private_root.is_dir():
            raise DatasetValidationError(f"private_root is not a directory: {supplied_private_root}")
        if not isinstance(split, str) or not _SPLIT_RE.fullmatch(split):
            raise DatasetValidationError("split must be a simple name without path separators")

        nested_private_root = supplied_private_root / public_root.name
        private_root = nested_private_root.resolve() if nested_private_root.is_dir() else supplied_private_root
        if _roots_overlap(public_root, private_root):
            raise PrivateDataLeakageError(
                "public_root and private_root must be separate, non-nested directory trees"
            )

        manifest = _load_manifest(public_root / "manifest.jsonl")
        selected_ids = _load_split(public_root / "splits" / f"{split}.txt", manifest)
        tasks: list[TaskInstance] = []
        private_specs: dict[str, PrivateTestSpec] = {}
        for instance_id in selected_ids:
            row = manifest[instance_id]
            tasks.append(cls._task_from_row(public_root, row))
            private_specs[instance_id] = cls._private_spec(private_root, instance_id)

        return cls(
            public_root=public_root,
            private_root=private_root,
            split=split,
            tasks=tuple(tasks),
            private_specs=private_specs,
        )

    @staticmethod
    def _task_from_row(public_root: Path, row: dict[str, Any]) -> TaskInstance:
        instance_id = row.get("instance_id")
        instance_root = _safe_child(public_root / "instances", instance_id, "instance_id")
        inline_statement = row.get("problem_statement")
        statement_path = row.get("problem_statement_path")
        if bool(inline_statement) == bool(statement_path):
            raise DatasetValidationError(
                f"task {instance_id} must define exactly one of problem_statement or problem_statement_path"
            )
        if statement_path:
            problem_path = _safe_child(public_root, statement_path, "problem_statement_path")
            if not problem_path.is_relative_to(instance_root):
                raise DatasetValidationError(
                    f"problem_statement_path for {instance_id} must stay inside its instance directory"
                )
            try:
                problem_statement = problem_path.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise DatasetValidationError(
                    f"missing problem statement for {instance_id}: {problem_path}"
                ) from exc
        else:
            problem_statement = inline_statement

        task_json_path = instance_root / "task.json"
        task_metadata: dict[str, Any] = {}
        if task_json_path.is_file():
            task_json = _read_json(task_json_path, f"public task metadata for {instance_id}")
            task_json_instance = task_json.pop("instance_id", None)
            if task_json_instance != instance_id:
                raise DatasetValidationError(
                    f"public task metadata instance_id mismatch: expected {instance_id}, got {task_json_instance}"
                )
            task_metadata = task_json
        metadata = _merge_metadata(row.get("metadata"), task_metadata)
        payload = {
            "schema_version": row.get("schema_version", SCHEMA_VERSION),
            "instance_id": instance_id,
            "repo": row.get("repo"),
            "base_commit": row.get("base_commit"),
            "problem_statement": problem_statement,
            "environment_id": row.get("environment_id"),
            "metadata": metadata,
        }
        assert_inference_payload_safe(payload)
        try:
            return TaskInstance.from_dict(payload)
        except SchemaValidationError as exc:
            raise DatasetValidationError(f"invalid public task {instance_id}: {exc}") from exc

    @staticmethod
    def _private_spec(private_root: Path, instance_id: str) -> PrivateTestSpec:
        instance_root = _safe_child(private_root, instance_id, "private instance_id")
        grading = _read_json(instance_root / "grading.json", f"private grading data for {instance_id}")
        allowed = {
            "schema_version",
            "instance_id",
            "gold_patch",
            "test_patch",
            "FAIL_TO_PASS",
            "PASS_TO_PASS",
        }
        unknown = sorted(set(grading) - allowed)
        if unknown:
            raise DatasetValidationError(
                f"private grading data for {instance_id} contains unknown fields: {', '.join(unknown)}"
            )
        if grading.get("instance_id") != instance_id:
            raise DatasetValidationError(
                f"private grading instance_id mismatch: expected {instance_id}, got {grading.get('instance_id')}"
            )
        gold_patch = _safe_child(instance_root, grading.get("gold_patch"), "gold_patch")
        test_patch = _safe_child(instance_root, grading.get("test_patch"), "test_patch")
        for path, description in ((gold_patch, "gold_patch"), (test_patch, "test_patch")):
            if not path.is_file():
                raise DatasetValidationError(f"missing {description} for {instance_id}: {path}")
        if gold_patch == test_patch:
            raise DatasetValidationError(f"gold_patch and test_patch must be different for {instance_id}")
        try:
            return PrivateTestSpec(
                schema_version=grading.get("schema_version", SCHEMA_VERSION),
                instance_id=instance_id,
                gold_patch_path=gold_patch,
                test_patch_path=test_patch,
                fail_to_pass=grading.get("FAIL_TO_PASS"),
                pass_to_pass=grading.get("PASS_TO_PASS"),
            )
        except SchemaValidationError as exc:
            raise DatasetValidationError(f"invalid private grading data for {instance_id}: {exc}") from exc

    def tasks(self) -> list[TaskInstance]:
        return [TaskInstance.from_dict(task.to_dict()) for task in self._tasks]

    def private_spec(self, instance_id: str) -> PrivateTestSpec:
        try:
            return self._private_specs[instance_id]
        except KeyError as exc:
            raise DatasetValidationError(f"unknown instance_id in selected split: {instance_id}") from exc

    def inference_payload(self, instance_id: str) -> dict[str, Any]:
        try:
            payload = self._tasks_by_id[instance_id].to_dict()
        except KeyError as exc:
            raise DatasetValidationError(f"unknown instance_id in selected split: {instance_id}") from exc
        assert_inference_payload_safe(payload)
        return payload

    def digest(self) -> str:
        """Hash the selected task definitions and private grading contents."""

        task_rows: list[dict[str, Any]] = []
        for task in sorted(self._tasks, key=lambda item: item.instance_id):
            private = self._private_specs[task.instance_id]
            task_rows.append(
                {
                    "public": task.to_dict(),
                    "private": {
                        "instance_id": private.instance_id,
                        "FAIL_TO_PASS": list(private.fail_to_pass),
                        "PASS_TO_PASS": list(private.pass_to_pass),
                        "gold_patch_sha256": hashlib.sha256(
                            private.gold_patch_path.read_bytes()
                        ).hexdigest(),
                        "test_patch_sha256": hashlib.sha256(
                            private.test_patch_path.read_bytes()
                        ).hexdigest(),
                    },
                }
            )
        canonical = json.dumps(
            {"schema_version": SCHEMA_VERSION, "split": self.split, "tasks": task_rows},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"
