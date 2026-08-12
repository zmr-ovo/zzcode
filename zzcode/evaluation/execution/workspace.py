"""Create independent local Git workspaces at an exact base commit."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Mapping

from ..errors import ArtifactError
from ..schema import TaskInstance


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ArtifactError(f"Git command failed to execute: git {' '.join(args)}: {exc}") from exc


class WorkspaceManager:
    """Phase 3 local workspace manager; Docker isolation is added in Phase 4."""

    def __init__(self, root: Path, repositories: Mapping[str, Path]):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.repositories = {name: Path(path).resolve() for name, path in repositories.items()}
        for name, path in self.repositories.items():
            if not path.is_dir():
                raise ArtifactError(f"repository {name!r} is not a directory: {path}")
            result = _git(["rev-parse", "--is-inside-work-tree"], path)
            if result.returncode != 0 or result.stdout.strip() != "true":
                raise ArtifactError(f"repository {name!r} is not a Git worktree: {path}")

    def create_inference(self, task: TaskInstance) -> Path:
        return self._create(task, "inference")

    def create_grading(self, task: TaskInstance) -> Path:
        return self._create(task, "grading")

    def _create(self, task: TaskInstance, role: str) -> Path:
        try:
            source = self.repositories[task.repo]
        except KeyError as exc:
            raise ArtifactError(f"task repository is not allowlisted: {task.repo}") from exc
        task_root = self.root / task.instance_id
        destination = task_root / role
        resolved = destination.resolve()
        if not resolved.is_relative_to(self.root):
            raise ArtifactError("workspace path escapes configured root")
        if destination.exists():
            raise ArtifactError(f"workspace already exists and will not be overwritten: {destination}")
        task_root.mkdir(parents=True, exist_ok=True)
        clone = _git(["clone", "--quiet", "--no-hardlinks", str(source), str(destination)])
        if clone.returncode != 0:
            shutil.rmtree(destination, ignore_errors=True)
            raise ArtifactError(f"failed to clone {task.repo}: {clone.stderr.strip()}")
        checkout = _git(["checkout", "--quiet", "--detach", task.base_commit], destination)
        if checkout.returncode != 0:
            shutil.rmtree(destination, ignore_errors=True)
            raise ArtifactError(
                f"failed to checkout base commit {task.base_commit}: {checkout.stderr.strip()}"
            )
        self.assert_clean_base(destination, task.base_commit)
        return destination

    def assert_clean_base(self, workspace: Path, base_commit: str) -> None:
        workspace = Path(workspace).resolve()
        if not workspace.is_relative_to(self.root):
            raise ArtifactError("workspace is outside configured root")
        head = _git(["rev-parse", "HEAD"], workspace)
        expected = _git(["rev-parse", f"{base_commit}^{{commit}}"], workspace)
        if head.returncode != 0 or expected.returncode != 0 or head.stdout.strip() != expected.stdout.strip():
            raise ArtifactError(f"workspace HEAD does not match base commit {base_commit}")
        status = _git(["status", "--porcelain=v1", "--untracked-files=all"], workspace)
        if status.returncode != 0 or status.stdout:
            raise ArtifactError("workspace is not clean at base commit")
